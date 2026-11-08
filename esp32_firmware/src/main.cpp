#include "config.h"

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Preferences.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <cstring>
#include "imu_sensor.h"
#include "step_counter.h"
#include "fall_detection.h"
#include "wifi_tracker.h"
#include "wifi_setup_portal.h"

IMUSensor imu;
StepCounter stepCounter;
FallDetector fallDetector;
WiFiTracker wifiTracker;
WiFiSetupPortal wifiSetupPortal;
Preferences resetPreferences;
bool resetPreferencesReady = false;

struct StepTelemetrySnapshot {
    uint32_t steps;
    uint8_t intervalCount;
    uint32_t intervals[10];
};

QueueHandle_t stepTelemetryQueue = nullptr;
bool isAlerting = false;
unsigned long alertStartTime = 0;
unsigned long lastWiFiAttempt = 0;
unsigned long lastSensorLogTime = 0;
unsigned long wifiConnectionStartedAt = 0;
uint32_t lastReportedStepCount = 0;
bool imuInitialized = false;
volatile bool wifiRetryPending = false;
volatile bool wifiDisconnectedEventPending = false;
volatile bool wifiGotIpEventPending = false;
volatile bool stepCounterResetPending = false;
volatile uint8_t wifiDisconnectReason = 0;

void stepTelemetryTask(void* parameter);

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info)
{
    if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
        wifiDisconnectReason = info.wifi_sta_disconnected.reason;
        wifiDisconnectedEventPending = true;
        wifiRetryPending = true;
    } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
        wifiGotIpEventPending = true;
        wifiRetryPending = false;
    }
}

void setup() {
    Serial.begin(115200);
    // Thêm 2 dòng này để chờ cổng Serial trên máy tính nhận diện chip
    unsigned long startMs = millis();
    while (!Serial && (millis() - startMs < 3000)) { delay(10); }

    Serial.println("\n=== HE THONG KHOI DONG ===");
    pinMode(BUZZER_PIN, OUTPUT);
    digitalWrite(BUZZER_PIN, LOW);
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    resetPreferencesReady = resetPreferences.begin("gaitwatch", false);
    if (!resetPreferencesReady) {
        Serial.println("[RESET] Could not open persistent reset state.");
    }

    imuInitialized = imu.begin();
    if (imuInitialized) {
        Serial.println("IMU initialized.");
    }

    WiFi.setAutoReconnect(false);
    WiFi.onEvent(onWiFiEvent);
    if (wifiSetupPortal.begin(WIFI_SSID, WIFI_PASS)) {
        Serial.println("Connecting to Wi-Fi. ESP32-C3 requires a 2.4 GHz network.");
        lastWiFiAttempt = millis();
        wifiConnectionStartedAt = lastWiFiAttempt;
    } else {
        Serial.println("No saved Wi-Fi network; starting the setup page.");
        wifiSetupPortal.startPortal();
    }
    stepTelemetryQueue = xQueueCreate(1, sizeof(StepTelemetrySnapshot));
    if (stepTelemetryQueue == nullptr) {
        Serial.println("ERROR: Could not create the step telemetry queue.");
    } else if (xTaskCreate(stepTelemetryTask, "stepTelemetry", 6144, nullptr, 1,
                           nullptr) != pdPASS) {
        Serial.println("ERROR: Could not start the step telemetry task.");
        vQueueDelete(stepTelemetryQueue);
        stepTelemetryQueue = nullptr;
    }
}

bool buttonPressed(unsigned long now)
{
    static int lastReading = HIGH;
    static int stableState = HIGH;
    static unsigned long lastChangeTime = 0;

    const int reading = digitalRead(BUTTON_PIN);
    if (reading != lastReading) {
        lastReading = reading;
        lastChangeTime = now;
    }
    if (now - lastChangeTime >= DEBOUNCE_DELAY && reading != stableState) {
        stableState = reading;
        return stableState == LOW;
    }
    return false;
}

bool postTelemetry(JsonDocument& doc, const char* description,
                   unsigned long timeoutMs, String& responseBody)
{
    if (WiFi.status() != WL_CONNECTED) {
        Serial.printf("ERROR: Cannot send %s; Wi-Fi is not connected.\n", description);
        return false;
    }

    String payload;
    serializeJson(doc, payload);
    Serial.printf("[HTTP] %s JSON payload:\n", description);
    Serial.println(payload);

    const String endpoint = String(SERVER_URL) + "/api/telemetry";
    Serial.printf("[HTTP] POST %s\n", endpoint.c_str());

    HTTPClient http;
    http.setTimeout(timeoutMs);
    if (!http.begin(endpoint)) {
        Serial.println("ERROR: Could not initialize HTTP connection.");
        return false;
    }
    http.addHeader("Content-Type", "application/json");

    const unsigned long requestStartTime = millis();
    const int statusCode = http.POST(payload);
    const unsigned long responseTimeMs = millis() - requestStartTime;
    Serial.printf("[HTTP] Response code: %d; response time: %lu ms.\n",
                  statusCode, responseTimeMs);

    if (statusCode > 0) {
        responseBody = http.getString();
        Serial.println("[HTTP] Backend response:");
        Serial.println(responseBody);
    } else {
        Serial.printf("[HTTP] Request failed: %s\n",
                      http.errorToString(statusCode).c_str());
    }
    http.end();

    if (statusCode != HTTP_CODE_OK) {
        Serial.printf("ERROR: Backend returned HTTP status %d for %s.\n",
                      statusCode, description);
        return false;
    }

    return true;
}

bool sendAlertToBackend()
{
    JsonDocument doc;
    doc["event"] = "FALL_DETECTED";
    doc["steps"] = stepCounter.getStepCount();
    doc["uptime_ms"] = millis();
    JsonObject wifiData = doc["wifi_data"].to<JsonObject>();
    if (!wifiTracker.scanNetworks(wifiData)) {
        Serial.println("WARNING: Wi-Fi scan failed; sending the alert without access points.");
    }

    String responseBody;
    if (!postTelemetry(doc, "fall alert", 65000, responseBody)) {
        return false;
    }

    JsonDocument response;
    const DeserializationError parseError = deserializeJson(response, responseBody);
    if (parseError) {
        Serial.printf("[HTTP] Could not parse backend response: %s\n",
                      parseError.c_str());
        return false;
    }

    if (response["telegram_sent"].as<bool>()) {
        Serial.println("[TELEGRAM] Backend confirms the alert message was sent successfully.");
    } else {
        Serial.println(
            "[TELEGRAM] Backend returned HTTP 200, but did not confirm Telegram delivery.");
    }
    return true;
}

bool sendStepUpdateToBackend(const StepTelemetrySnapshot& snapshot)
{
    JsonDocument doc;
    doc["event"] = "STEP_UPDATE";
    doc["steps"] = snapshot.steps;
    doc["uptime_ms"] = millis();

    JsonArray intervalArray = doc["step_intervals_ms"].to<JsonArray>();
    for (uint8_t i = 0; i < snapshot.intervalCount; i++) {
        intervalArray.add(snapshot.intervals[i]);
    }

    String responseBody;
    if (!postTelemetry(doc, "step update", 20000, responseBody)) {
        return false;
    }

    Serial.printf("[TELEMETRY] Step update accepted by backend: %lu steps.\n",
                  static_cast<unsigned long>(snapshot.steps));
    return true;
}

bool readStepResetRevision(uint32_t& revision)
{
    if (WiFi.status() != WL_CONNECTED) {
        return false;
    }

    HTTPClient http;
    http.setTimeout(3000);
    const String endpoint = String(SERVER_URL) + "/api/device/reset-state";
    if (!http.begin(endpoint)) {
        Serial.println("[RESET] Could not initialize reset-state request.");
        return false;
    }

    const int statusCode = http.GET();
    if (statusCode != HTTP_CODE_OK) {
        Serial.printf("[RESET] Reset-state request returned HTTP %d.\n", statusCode);
        http.end();
        return false;
    }

    JsonDocument response;
    const DeserializationError parseError = deserializeJson(response, http.getString());
    http.end();
    if (parseError || !response["reset_revision"].is<uint32_t>()) {
        Serial.println("[RESET] Could not parse the reset-state response.");
        return false;
    }

    revision = response["reset_revision"].as<uint32_t>();
    return true;
}

void pollStepResetCommand(uint32_t& lastResetRevision, bool& resetRevisionKnown)
{
    uint32_t revision = 0;
    if (!readStepResetRevision(revision)) {
        return;
    }

    if (!resetRevisionKnown) {
        resetRevisionKnown = true;
        if (resetPreferencesReady &&
            resetPreferences.putUInt("reset_revision", revision) == 0) {
            Serial.println("[RESET] Could not persist initial reset revision.");
        }
        lastResetRevision = revision;
    } else if (revision != lastResetRevision) {
        lastResetRevision = revision;
        stepCounterResetPending = true;
        if (resetPreferencesReady &&
            resetPreferences.putUInt("reset_revision", revision) == 0) {
            Serial.println("[RESET] Could not persist reset revision.");
        }
        Serial.println("[RESET] Dashboard requested a step counter reset.");
    }
}

void stepTelemetryTask(void*)
{
    StepTelemetrySnapshot snapshot{};
    uint32_t lastResetRevision =
        resetPreferencesReady ? resetPreferences.getUInt("reset_revision", 0) : 0;
    bool resetRevisionKnown =
        resetPreferencesReady && resetPreferences.isKey("reset_revision");
    unsigned long lastResetPoll = 0;
    for (;;) {
        const unsigned long now = millis();
        if (WiFi.status() == WL_CONNECTED &&
            now - lastResetPoll >= 3000UL) {
            lastResetPoll = now;
            pollStepResetCommand(lastResetRevision, resetRevisionKnown);
        }

        if (xQueueReceive(stepTelemetryQueue, &snapshot,
                          pdMS_TO_TICKS(1000)) == pdTRUE) {
            while (!sendStepUpdateToBackend(snapshot)) {
                const unsigned long retryNow = millis();
                if (WiFi.status() == WL_CONNECTED &&
                    retryNow - lastResetPoll >= 3000UL) {
                    lastResetPoll = retryNow;
                    pollStepResetCommand(lastResetRevision, resetRevisionKnown);
                }
                StepTelemetrySnapshot latestSnapshot{};
                if (xQueueReceive(stepTelemetryQueue, &latestSnapshot,
                                  pdMS_TO_TICKS(1000)) == pdTRUE) {
                    snapshot = latestSnapshot;
                }
            }
        }
    }
}

void loop() {
    unsigned long now = millis();

    if (wifiDisconnectedEventPending) {
        const uint8_t reason = wifiDisconnectReason;
        wifiDisconnectedEventPending = false;
        Serial.printf("Wi-Fi disconnected (reason %u, status %d).\n",
                      static_cast<unsigned int>(reason),
                      static_cast<int>(WiFi.status()));
    }

    if (wifiGotIpEventPending) {
        wifiGotIpEventPending = false;
        wifiConnectionStartedAt = now;
        wifiSetupPortal.stopPortal();
        Serial.print("Wi-Fi connected. IP address: ");
        Serial.println(WiFi.localIP());
    }

    if (wifiSetupPortal.handleClient()) {
        lastWiFiAttempt = now;
        wifiConnectionStartedAt = now;
        wifiRetryPending = false;
    }

    if (stepCounterResetPending) {
        stepCounterResetPending = false;
        stepCounter.reset();
        lastReportedStepCount = 0;
        Serial.println("[RESET] Step counter reset by dashboard.");
        if (stepTelemetryQueue != nullptr) {
            StepTelemetrySnapshot snapshot{};
            if (xQueueOverwrite(stepTelemetryQueue, &snapshot) != pdPASS) {
                Serial.println("[RESET] Could not queue the reset count update.");
            }
        }
    }

    if (wifiRetryPending &&
        WiFi.status() != WL_CONNECTED &&
        now - lastWiFiAttempt >= WIFI_RETRY_INTERVAL_MS) {
        wifiRetryPending = false;
        Serial.println("Retrying Wi-Fi connection...");
        if (!WiFi.reconnect()) {
            Serial.println("Wi-Fi reconnect request failed; restarting with configured credentials.");
            wifiSetupPortal.connect();
        }
        lastWiFiAttempt = now;
    }

    if (WiFi.status() != WL_CONNECTED &&
        !wifiSetupPortal.isPortalActive() &&
        now - wifiConnectionStartedAt >= WIFI_SETUP_TIMEOUT_MS) {
        wifiSetupPortal.startPortal();
    }

    IMUData data = imu.readData();

    const bool stepDetected = stepCounter.processSample(data, now);

    if (now - lastSensorLogTime >= 1000) {
        lastSensorLogTime = now;
        if (data.valid) {
            Serial.printf(
                "IMU accel[g]=%.2f,%.2f,%.2f mag=%.2f "
                "gyro[dps]=%.1f,%.1f,%.1f steps=%lu\n",
                data.ax, data.ay, data.az, data.accel_mag,
                data.gx, data.gy, data.gz,
                static_cast<unsigned long>(stepCounter.getStepCount()));
        } else if (imuInitialized) {
            Serial.println("ERROR: IMU read failed after initialization; check I2C connection.");
        } else {
            Serial.println("IMU unavailable; see startup diagnostics above.");
        }
    }

    if (!isAlerting) {
        if (fallDetector.update(data, now) == FALL_CONFIRMED) {
            isAlerting = true;
            alertStartTime = now;
            digitalWrite(BUZZER_PIN, HIGH);
            Serial.printf(
                "[ALERT] Buzzer ON; bat dau dem nguoc %lu giay de huy canh bao.\n",
                static_cast<unsigned long>(CONFIRMATION_TIMEOUT / 1000UL));
            Serial.println("Fall detected. Press the button within 15 seconds to cancel.");
        }
    } else {
        if (buttonPressed(now)) {
            Serial.println(
                "[BUTTON] Da bam nut huy canh bao! Tat Buzzer và reset State Machine.");
            isAlerting = false;
            digitalWrite(BUZZER_PIN, LOW);
            fallDetector.reset();
            Serial.println("Fall alert canceled by user.");
        } else if (now - alertStartTime >= CONFIRMATION_TIMEOUT) {
            Serial.printf(
                "[ALERT] Het 15s dem nguoc! Dang quet danh sach Wi-Fi BSSID "
                "va gui request POST den Backend %s.\n",
                SERVER_URL);
            digitalWrite(BUZZER_PIN, LOW);
            sendAlertToBackend();
            isAlerting = false;
            fallDetector.reset();
        }
    }

    if (stepDetected && WiFi.status() == WL_CONNECTED &&
        stepCounter.getStepCount() > lastReportedStepCount) {
        if (stepTelemetryQueue == nullptr) {
            Serial.println("ERROR: Step update skipped because the telemetry task is unavailable.");
        } else {
            StepTelemetrySnapshot snapshot{};
            snapshot.steps = stepCounter.getStepCount();
            snapshot.intervalCount =
                stepCounter.getRecentStepIntervals(snapshot.intervals, 10);
            if (xQueueOverwrite(stepTelemetryQueue, &snapshot) == pdPASS) {
                lastReportedStepCount = snapshot.steps;
            } else {
                Serial.println("ERROR: Could not queue the step telemetry update.");
            }
        }
    }

    delay(SAMPLE_INTERVAL_MS);
}