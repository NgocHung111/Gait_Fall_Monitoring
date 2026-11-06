#ifndef WIFI_TRACKER_H
#define WIFI_TRACKER_H

#include <WiFi.h>
#include <ArduinoJson.h>

class WiFiTracker {
public:
    bool scanNetworks(JsonObject destination);
};

#endif