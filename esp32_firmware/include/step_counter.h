#ifndef STEP_COUNTER_H
#define STEP_COUNTER_H

#include "imu_sensor.h"

class StepCounter {
private:
    uint32_t step_count = 0;
    float baseline = 1.0f;
    bool baselineInitialized = false;
    bool wasAboveThreshold = false;
    unsigned long last_step_time = 0;

public:
    bool processSample(const IMUData& data, unsigned long currentTime);
    uint32_t getStepCount() const { return step_count; }
};

#endif