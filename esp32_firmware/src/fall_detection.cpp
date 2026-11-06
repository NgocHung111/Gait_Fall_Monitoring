#include "fall_detection.h"
#include "config.h"

namespace {
bool isStill(const IMUData& data)
{
    const float gyroMagnitude = sqrtf(
        data.gx * data.gx + data.gy * data.gy + data.gz * data.gz);
    return data.accel_mag >= FALL_STILLNESS_ACCEL_MIN &&
           data.accel_mag <= FALL_STILLNESS_ACCEL_MAX &&
           gyroMagnitude <= FALL_STILLNESS_GYRO_MAX;
}
}

FallState FallDetector::update(const IMUData& data, unsigned long currentTime) {
    if (!data.valid) {
        return currentState;
    }

    switch (currentState) {
        case IDLE:
            if (data.accel_mag > ACCEL_THRESHOLD_HIGH) {
                currentState = IMPACT_DETECTED;
                impactTime = currentTime;
            }
            break;

        case IMPACT_DETECTED:
            if (currentTime - impactTime > FALL_IMPACT_SETTLE_MAX_MS) {
                reset();
            } else if (currentTime - impactTime >= FALL_IMPACT_SETTLE_MIN_MS &&
                       isStill(data)) {
                currentState = STILLNESS_CHECK;
                stillnessTime = currentTime;
            }
            break;

        case STILLNESS_CHECK:
            if (!isStill(data)) {
                currentState = IMPACT_DETECTED;
            } else if (currentTime - stillnessTime >= FALL_STILLNESS_DURATION_MS) {
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