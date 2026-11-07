#include "config.h"
#include "fall_detection.h"

namespace {
float gyroMagnitude(const IMUData& data)
{
    return sqrtf(data.gx * data.gx + data.gy * data.gy + data.gz * data.gz);
}

bool isStill(const IMUData& data)
{
    return data.accel_mag >= FALL_STILLNESS_ACCEL_MIN &&
           data.accel_mag <= FALL_STILLNESS_ACCEL_MAX &&
           gyroMagnitude(data) <= FALL_STILLNESS_GYRO_MAX;
}
}

FallState FallDetector::update(const IMUData& data, unsigned long currentTime) {
    if (!data.valid) {
        return currentState;
    }

    switch (currentState) {
        case IDLE:
            if (data.accel_mag > ACCEL_THRESHOLD_HIGH) {
                Serial.printf(
                    "[FALL] NORMAL -> IMPACT: accel_mag=%.2f g vuot nguong %.2f g.\n",
                    data.accel_mag, ACCEL_THRESHOLD_HIGH);
                currentState = IMPACT_DETECTED;
                impactTime = currentTime;
            }
            break;

        case IMPACT_DETECTED:
            if (currentTime - impactTime > FALL_IMPACT_SETTLE_MAX_MS) {
                Serial.printf(
                    "[FALL] IMPACT -> NORMAL: het cua so cho; accel_mag=%.2f g.\n",
                    data.accel_mag);
                reset();
            } else if (currentTime - impactTime >= FALL_IMPACT_SETTLE_MIN_MS &&
                       isStill(data)) {
                Serial.printf(
                    "[FALL] IMPACT -> STILLNESS: accel_mag=%.2f g, "
                    "gyroMagnitude=%.2f dps; bat dau kiem tra nam yen.\n",
                    data.accel_mag, gyroMagnitude(data));
                currentState = STILLNESS_CHECK;
                stillnessTime = currentTime;
            }
            break;

        case STILLNESS_CHECK:
            if (!isStill(data)) {
                Serial.printf(
                    "[FALL] STILLNESS -> IMPACT: chuyen dong tro lai; "
                    "accel_mag=%.2f g, gyroMagnitude=%.2f dps.\n",
                    data.accel_mag, gyroMagnitude(data));
                currentState = IMPACT_DETECTED;
            } else if (currentTime - stillnessTime >= FALL_STILLNESS_DURATION_MS) {
                Serial.printf(
                    "[FALL] STILLNESS -> FALL_CONFIRMED: nam yen du %lu ms; "
                    "accel_mag=%.2f g, gyroMagnitude=%.2f dps.\n",
                    static_cast<unsigned long>(FALL_STILLNESS_DURATION_MS),
                    data.accel_mag, gyroMagnitude(data));
                currentState = FALL_CONFIRMED;
            }
            break;

        case FALL_CONFIRMED:
            break;
    }
    return currentState;
}

void FallDetector::reset()
{
    currentState = IDLE;
    impactTime = 0;
    stillnessTime = 0;
}