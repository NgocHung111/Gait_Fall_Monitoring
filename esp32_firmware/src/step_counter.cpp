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
        last_step_time = currentTime;
        isStep = true;
    }

    wasAboveThreshold = aboveThreshold;
    baseline += STEP_BASELINE_ALPHA * (data.accel_mag - baseline);
    return isStep;
}