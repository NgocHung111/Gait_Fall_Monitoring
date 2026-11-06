#include "wifi_tracker.h"

bool WiFiTracker::scanNetworks(JsonObject destination) {
    const int networkCount = WiFi.scanNetworks(false, true);
    JsonArray wifiArray = destination["wifiAccessPoints"].to<JsonArray>();

    if (networkCount < 0) {
        WiFi.scanDelete();
        return false;
    }

    // Keep the five strongest access points without relying on scan ordering.
    int selected[5] = {-1, -1, -1, -1, -1};
    for (int slot = 0; slot < 5; ++slot) {
        int strongest = -1;
        for (int i = 0; i < networkCount; ++i) {
            bool alreadySelected = false;
            for (int j = 0; j < slot; ++j) {
                if (selected[j] == i) {
                    alreadySelected = true;
                    break;
                }
            }
            if (!alreadySelected &&
                (strongest < 0 || WiFi.RSSI(i) > WiFi.RSSI(strongest))) {
                strongest = i;
            }
        }
        if (strongest < 0) {
            break;
        }
        selected[slot] = strongest;

        JsonObject accessPoint = wifiArray.add<JsonObject>();
        accessPoint["macAddress"] = WiFi.BSSIDstr(strongest);
        accessPoint["signalStrength"] = WiFi.RSSI(strongest);
        accessPoint["channel"] = WiFi.channel(strongest);
    }

    WiFi.scanDelete();
    return true;
}