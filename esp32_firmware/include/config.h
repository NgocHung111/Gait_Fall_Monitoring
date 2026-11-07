#ifndef CONFIG_H
#define CONFIG_H

#include "secrets.h"

// Replace this with the Render service URL assigned during deployment.
#define SERVER_URL "https://gait-fall-monitoring.onrender.com"

// ESP32-C3 SuperMini and LSM6DS3 hardware.
#define SDA_PIN 8
#define SCL_PIN 9
#define LSM6DS3_I2C_ADDRESS 0x6B
#define BUZZER_PIN 5
#define BUTTON_PIN 4

// Processing intervals and thresholds (acceleration in g, gyroscope in dps).
#define STEP_DYNAMIC_THRESHOLD 0.18f
#define STEP_BASELINE_ALPHA 0.05f
#define MIN_STEP_INTERVAL 300UL

#define ACCEL_THRESHOLD_HIGH 2.5f
#define FALL_IMPACT_SETTLE_MIN_MS 250UL
#define FALL_IMPACT_SETTLE_MAX_MS 2500UL
#define FALL_STILLNESS_DURATION_MS 2000UL
#define FALL_STILLNESS_ACCEL_MIN 0.85f
#define FALL_STILLNESS_ACCEL_MAX 1.15f
#define FALL_STILLNESS_GYRO_MAX 35.0f

#define DEBOUNCE_DELAY 50UL
#define CONFIRMATION_TIMEOUT 15000UL
#define SAMPLE_INTERVAL_MS 20UL
#define WIFI_RETRY_INTERVAL_MS 10000UL

#endif
