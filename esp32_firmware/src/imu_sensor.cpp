#include "config.h"
#include "imu_sensor.h"

namespace {
constexpr uint8_t WHO_AM_I_REGISTER = 0x0F;
constexpr uint8_t LSM6DS3_WHO_AM_I = 0x69;
constexpr uint8_t LSM6DS3TR_C_WHO_AM_I = 0x6A;
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

    const uint8_t addresses[] = {
        static_cast<uint8_t>(LSM6DS3_I2C_ADDRESS),
        static_cast<uint8_t>(LSM6DS3_I2C_ADDRESS == 0x6A ? 0x6B : 0x6A)
    };
    bool deviceResponded = false;
    uint8_t detectedDeviceId = 0;

    for (const uint8_t address : addresses) {
        i2cAddress = address;
        uint8_t deviceId = 0;
        if (!readRegister(WHO_AM_I_REGISTER, deviceId)) {
            i2cAddress = 0;
            continue;
        }

        deviceResponded = true;
        detectedDeviceId = deviceId;
        if (deviceId == LSM6DS3_WHO_AM_I ||
            deviceId == LSM6DS3TR_C_WHO_AM_I) {
            Serial.printf("LSM6DS3-compatible IMU found at I2C 0x%02X (WHO_AM_I 0x%02X).\n",
                          static_cast<unsigned int>(i2cAddress),
                          static_cast<unsigned int>(deviceId));
            break;
        }
        i2cAddress = 0;
    }

    if (i2cAddress == 0) {
        if (deviceResponded) {
            Serial.printf(
                "I2C device responded, but WHO_AM_I was 0x%02X (expected 0x69 or 0x6A); check sensor model.\n",
                static_cast<unsigned int>(detectedDeviceId));
        } else {
            Serial.println(
                "No IMU response at I2C 0x6A or 0x6B; check SDA/SCL pins, power, GND, and sensor address.");
        }
        return false;
    }

    // ODR 104 Hz, accelerometer +/-4 g, gyroscope +/-2000 dps.
    return writeRegister(CTRL1_XL_REGISTER, 0x48) &&
           writeRegister(CTRL2_G_REGISTER, 0x4C);
}

IMUData IMUSensor::readData() {
    IMUData data;
    if (i2cAddress == 0) {
        return data;
    }

    Wire.beginTransmission(i2cAddress);
    Wire.write(OUTX_L_G_REGISTER);
    if (Wire.endTransmission(false) != 0) {
        return data;
    }

    const uint8_t bytesRead = Wire.requestFrom(
        i2cAddress, SENSOR_DATA_LENGTH);
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
    Wire.beginTransmission(i2cAddress);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0 ||
        Wire.requestFrom(i2cAddress, 1) != 1 ||
        Wire.available() < 1) {
        return false;
    }
    value = static_cast<uint8_t>(Wire.read());
    return true;
}

bool IMUSensor::writeRegister(uint8_t reg, uint8_t value)
{
    Wire.beginTransmission(i2cAddress);
    Wire.write(reg);
    Wire.write(value);
    return Wire.endTransmission() == 0;
}