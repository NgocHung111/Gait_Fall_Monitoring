#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <cstring>
#include "config.h"
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

void setup() {
    Serial.begin(115200);
    pinMode(BUZZER_PIN, OUTPUT);
    digitalWrite(BUZZER_PIN, LOW);
    pinMode(BUTTON_PIN, INPUT_PULLUP);

    if (!imu.begin()) {
        Serial.println("ERROR: LSM6DS3 not detected or failed to initialize at I2C 0x6B.");
    } else {
        Serial.println("LSM6DS3 initialized.");
    }

    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    if (strcmp(WIFI_SSID, "YOUR_WIFI_NAME") != 0 && strlen(WIFI_SSID) > 0) {
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

    JsonDocument payload;
    payload["event"] = "FALL_DETECTED";
    payload["steps"] = stepCounter.getStepCount();
    payload["uptime_ms"] = millis();
    JsonObject wifiData = payload["wifi_data"].to<JsonObject>();
    if (!wifiTracker.scanNetworks(wifiData)) {
        Serial.println("WARNING: Wi-Fi scan failed; sending the alert without access points.");
    }

    String body;
    serializeJson(payload, body);

    HTTPClient http;
    http.setTimeout(5000);
    if (!http.begin(SERVER_URL)) {
        Serial.println("ERROR: Could not initialize HTTP connection.");
        return false;
    }
    http.addHeader("Content-Type", "application/json");
    const int statusCode = http.POST(body);
    http.end();

    if (statusCode < 200 || statusCode >= 300) {
        Serial.printf("ERROR: Backend returned HTTP status %d.\n", statusCode);
        return false;
    }
    Serial.printf("Fall alert delivered (HTTP %d).\n", statusCode);
    return true;
}

void loop() {
    unsigned long now = millis();

    if (WiFi.status() != WL_CONNECTED &&
        strcmp(WIFI_SSID, "YOUR_WIFI_NAME") != 0 &&
        now - lastWiFiAttempt >= WIFI_RETRY_INTERVAL_MS) {
        Serial.println("Retrying Wi-Fi connection...");
        WiFi.begin(WIFI_SSID, WIFI_PASS);
        lastWiFiAttempt = now;
    }

    IMUData data = imu.readData();

    stepCounter.processSample(data, now);

    if (!isAlerting) {
        if (fallDetector.update(data, now) == FALL_CONFIRMED) {
            isAlerting = true;
            alertStartTime = now;
            digitalWrite(BUZZER_PIN, HIGH);
            Serial.println("Fall detected. Press the button within 15 seconds to cancel.");
        }
    } else {
        if (buttonPressed(now)) {
            isAlerting = false;
            digitalWrite(BUZZER_PIN, LOW);
            fallDetector.reset();
            Serial.println("Fall alert canceled by user.");
        } else if (now - alertStartTime >= CONFIRMATION_TIMEOUT) {
            digitalWrite(BUZZER_PIN, LOW);
            sendAlertToBackend();
            isAlerting = false;
            fallDetector.reset();
        }
    }

    delay(SAMPLE_INTERVAL_MS);
}