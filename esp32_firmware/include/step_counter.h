#ifndef STEP_COUNTER_H
#define STEP_COUNTER_H

#include "imu_sensor.h"

class StepCounter {
private:
    static constexpr uint8_t MAX_RECENT_INTERVALS = 10;

    uint32_t step_count = 0;
    float baseline = 1.0f;
    bool baselineInitialized = false;
    bool wasAboveThreshold = false;
    bool hasPreviousStep = false;
    unsigned long last_step_time = 0;
    uint32_t recentIntervals[MAX_RECENT_INTERVALS] = {};
    uint8_t recentIntervalCount = 0;
    uint8_t nextIntervalIndex = 0;

public:
    bool processSample(const IMUData& data, unsigned long currentTime);
    uint32_t getStepCount() const { return step_count; }
    uint8_t getRecentStepIntervals(uint32_t* intervals, uint8_t maxIntervals) const;
    void reset();
};

#endif