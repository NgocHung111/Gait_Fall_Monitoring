#ifndef IMU_SENSOR_H
#define IMU_SENSOR_H

#include <Arduino.h>
#include <Wire.h>

struct IMUData {
    float ax = 0.0f;
    float ay = 0.0f;
    float az = 0.0f;
    float gx = 0.0f;
    float gy = 0.0f;
    float gz = 0.0f;
    float accel_mag = 0.0f;
    bool valid = false;
};

class IMUSensor {
public:
    bool begin();
    IMUData readData();

private:
    uint8_t i2cAddress = 0;
    bool readRegister(uint8_t reg, uint8_t& value);
    bool writeRegister(uint8_t reg, uint8_t value);
};

#endif