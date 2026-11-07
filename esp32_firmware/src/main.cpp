#include "config.h"

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <cstring>
#include "imu_sensor.h"
#include "step_counter.h"
#include "fall_detection.h"
#include "wifi_tracker.h"

IMUSensor imu;
StepCounter stepCounter;
FallDetector fallDetector;
WiFiTracker wifiTracker;

bool isAlerting = false;
unsigned long alertStartTime = 0;
unsigned long lastWiFiAttempt = 0;
unsigned long lastSensorLogTime = 0;
bool imuInitialized = false;
volatile bool wifiRetryPending = false;
volatile bool wifiDisconnectedEventPending = false;
volatile bool wifiGotIpEventPending = false;
volatile uint8_t wifiDisconnectReason = 0;

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

    imuInitialized = imu.begin();
    if (imuInitialized) {
        Serial.println("IMU initialized.");
    }

    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(false);
    WiFi.onEvent(onWiFiEvent);
    if (strcmp(WIFI_SSID, "YOUR_WIFI_NAME") != 0 && strlen(WIFI_SSID) > 0) {
        Serial.println("Connecting to Wi-Fi. ESP32-C3 requires a 2.4 GHz network.");
        WiFi.begin(WIFI_SSID, WIFI_PASS);
        lastWiFiAttempt = millis();
    } else {
        Serial.println("Wi-Fi credentials are placeholders; configure them in config.h.");
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

bool sendAlertToBackend()
{
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("ERROR: Cannot send fall alert; Wi-Fi is not connected.");
        return false;
    }

    JsonDocument doc;
    doc["event"] = "FALL_DETECTED";
    doc["steps"] = stepCounter.getStepCount();
    doc["uptime_ms"] = millis();
    JsonObject wifiData = doc["wifi_data"].to<JsonObject>();
    if (!wifiTracker.scanNetworks(wifiData)) {
        Serial.println("WARNING: Wi-Fi scan failed; sending the alert without access points.");
    }

    String payload;
    serializeJson(doc, payload);
    Serial.println("[HTTP] JSON payload:");
    Serial.println(payload);

    const String endpoint = String(SERVER_URL) + "/api/telemetry";
    Serial.printf("[HTTP] POST %s\n", endpoint.c_str());

    HTTPClient http;
    http.setTimeout(65000);
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

    String responseBody;
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
        Serial.printf("ERROR: Backend returned HTTP status %d.\n", statusCode);
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
        Serial.print("Wi-Fi connected. IP address: ");
        Serial.println(WiFi.localIP());
    }

    if (wifiRetryPending &&
        strcmp(WIFI_SSID, "YOUR_WIFI_NAME") != 0 &&
        now - lastWiFiAttempt >= WIFI_RETRY_INTERVAL_MS) {
        wifiRetryPending = false;
        Serial.println("Retrying Wi-Fi connection...");
        if (!WiFi.reconnect()) {
            Serial.println("Wi-Fi reconnect request failed; restarting with configured credentials.");
            WiFi.begin(WIFI_SSID, WIFI_PASS);
        }
        lastWiFiAttempt = now;
    }

    IMUData data = imu.readData();

    stepCounter.processSample(data, now);

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

    delay(SAMPLE_INTERVAL_MS);
}