#ifndef WIFI_SETUP_PORTAL_H
#define WIFI_SETUP_PORTAL_H

#include <Arduino.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <WebServer.h>

class WiFiSetupPortal {
public:
    bool begin(const char* fallbackSsid, const char* fallbackPassword);
    bool startPortal();
    bool handleClient();
    void connect();
    void stopPortal();
    bool isPortalActive() const { return portalActive; }

private:
    void registerRoutes();
    void handleRoot();
    void handleScan();
    void handleSave();

    DNSServer dnsServer;
    WebServer server{80};
    Preferences preferences;
    String ssid;
    String password;
    String setupPassword;
    bool portalActive = false;
    bool preferencesReady = false;
    bool pendingConnect = false;
    unsigned long connectAfter = 0;
};

#endif
