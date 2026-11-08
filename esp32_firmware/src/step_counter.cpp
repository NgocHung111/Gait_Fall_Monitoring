#include "config.h"
#include "step_counter.h"

bool StepCounter::processSample(const IMUData& data, unsigned long currentTime) {
    if (!data.valid) {
        return false;
    }

    if (!baselineInitialized) {
        baseline = data.accel_mag;
        baselineInitialized = true;
        return false;
    }

    const float dynamicAcceleration = data.accel_mag - baseline;
    const bool aboveThreshold = dynamicAcceleration >= STEP_DYNAMIC_THRESHOLD;
    bool isStep = false;

    if (aboveThreshold && !wasAboveThreshold &&
        currentTime - last_step_time >= MIN_STEP_INTERVAL) {
        step_count++;
        if (hasPreviousStep) {
            recentIntervals[nextIntervalIndex] =
                static_cast<uint32_t>(currentTime - last_step_time);
            nextIntervalIndex = (nextIntervalIndex + 1) % MAX_RECENT_INTERVALS;
            if (recentIntervalCount < MAX_RECENT_INTERVALS) {
                recentIntervalCount++;
            }
        }
        last_step_time = currentTime;
        hasPreviousStep = true;
        isStep = true;
    }

    wasAboveThreshold = aboveThreshold;
    baseline += STEP_BASELINE_ALPHA * (data.accel_mag - baseline);
    return isStep;
}

uint8_t StepCounter::getRecentStepIntervals(
    uint32_t* intervals, uint8_t maxIntervals) const {
    const uint8_t count =
        recentIntervalCount < maxIntervals ? recentIntervalCount : maxIntervals;
    const uint8_t firstIndex =
        (nextIntervalIndex + MAX_RECENT_INTERVALS - recentIntervalCount) %
        MAX_RECENT_INTERVALS;
    const uint8_t skippedIntervals = recentIntervalCount - count;

    for (uint8_t i = 0; i < count; i++) {
        intervals[i] =
            recentIntervals[(firstIndex + skippedIntervals + i) %
                            MAX_RECENT_INTERVALS];
    }
    return count;
}