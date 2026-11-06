#ifndef FALL_DETECTION_H
#define FALL_DETECTION_H

#include "imu_sensor.h"

enum FallState { IDLE, IMPACT_DETECTED, STILLNESS_CHECK, FALL_CONFIRMED };

class FallDetector {
private:
    FallState currentState = IDLE;
    unsigned long impactTime = 0;
    unsigned long stillnessTime = 0;

public:
    FallState update(const IMUData& data, unsigned long currentTime);
    void reset();
};

#endif