#include "imu_sensor.h"
#include "config.h"

namespace {
constexpr uint8_t WHO_AM_I_REGISTER = 0x0F;
constexpr uint8_t LSM6DS3_WHO_AM_I = 0x69;
constexpr uint8_t CTRL1_XL_REGISTER = 0x10;
constexpr uint8_t CTRL2_G_REGISTER = 0x11;
constexpr uint8_t OUTX_L_G_REGISTER = 0x22;
constexpr uint8_t SENSOR_DATA_LENGTH = 12;

int16_t readInt16LE()
{
    const uint8_t low = static_cast<uint8_t>(Wire.read());
    const uint8_t high = static_cast<uint8_t>(Wire.read());
    return static_cast<int16_t>((static_cast<uint16_t>(high) << 8) | low);
}
}

bool IMUSensor::begin() {
    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(400000);

    uint8_t deviceId = 0;
    if (!readRegister(WHO_AM_I_REGISTER, deviceId) || deviceId != LSM6DS3_WHO_AM_I) {
        return false;
    }

    // ODR 104 Hz, accelerometer +/-4 g, gyroscope +/-2000 dps.
    return writeRegister(CTRL1_XL_REGISTER, 0x48) &&
           writeRegister(CTRL2_G_REGISTER, 0x4C);
}

IMUData IMUSensor::readData() {
    IMUData data;
    Wire.beginTransmission(LSM6DS3_I2C_ADDRESS);
    Wire.write(OUTX_L_G_REGISTER);
    if (Wire.endTransmission(false) != 0) {
        return data;
    }

    const uint8_t bytesRead = Wire.requestFrom(
        static_cast<uint8_t>(LSM6DS3_I2C_ADDRESS), SENSOR_DATA_LENGTH);
    if (bytesRead != SENSOR_DATA_LENGTH || Wire.available() < SENSOR_DATA_LENGTH) {
        while (Wire.available()) {
            Wire.read();
        }
        return data;
    }

    const int16_t gxRaw = readInt16LE();
    const int16_t gyRaw = readInt16LE();
    const int16_t gzRaw = readInt16LE();
    const int16_t axRaw = readInt16LE();
    const int16_t ayRaw = readInt16LE();
    const int16_t azRaw = readInt16LE();

    // Sensitivity at the configured full scales: 0.122 mg/LSB and 70 mdps/LSB.
    data.ax = axRaw * 0.000122f;
    data.ay = ayRaw * 0.000122f;
    data.az = azRaw * 0.000122f;
    data.gx = gxRaw * 0.070f;
    data.gy = gyRaw * 0.070f;
    data.gz = gzRaw * 0.070f;
    data.accel_mag = sqrtf(data.ax * data.ax + data.ay * data.ay + data.az * data.az);
    data.valid = true;
    return data;
}

bool IMUSensor::readRegister(uint8_t reg, uint8_t& value)
{
    Wire.beginTransmission(LSM6DS3_I2C_ADDRESS);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0 ||
        Wire.requestFrom(static_cast<uint8_t>(LSM6DS3_I2C_ADDRESS), 1) != 1 ||
        Wire.available() < 1) {
        return false;
    }
    value = static_cast<uint8_t>(Wire.read());
    return true;
}

bool IMUSensor::writeRegister(uint8_t reg, uint8_t value)
{
    Wire.beginTransmission(LSM6DS3_I2C_ADDRESS);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}