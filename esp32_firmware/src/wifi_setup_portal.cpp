#include "config.h"
#include "wifi_setup_portal.h"

#include <ArduinoJson.h>
#include <WiFi.h>

namespace {
constexpr char SETUP_AP_NAME[] = "GaitWatch-Setup";
constexpr char PREFERENCES_NAMESPACE[] = "gaitwatch";
constexpr char SETUP_PAGE[] PROGMEM = R"rawliteral(
<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cài đặt Wi-Fi · GaitWatch</title>
  <style>
    body{font:16px system-ui,sans-serif;max-width:520px;margin:32px auto;padding:0 18px;color:#17233b}
    h1{font-size:24px}p{line-height:1.5;color:#53627a}
    label{display:block;margin:18px 0 6px;font-weight:600}
    select,input,button{box-sizing:border-box;width:100%;padding:12px;border:1px solid #cbd4e2;border-radius:8px;font:inherit}
    button{margin-top:16px;background:#4169e1;color:#fff;border:0;font-weight:700}
    #status{min-height:24px;margin-top:16px}
  </style>
</head>
<body>
  <h1>Kết nối Wi-Fi cho GaitWatch</h1>
  <p>Chọn mạng Wi-Fi 2.4 GHz gần bạn, nhập mật khẩu rồi lưu. ESP32 sẽ thử kết nối và tự lưu mạng cho lần khởi động sau.</p>
  <button type="button" id="scan">Quét mạng Wi-Fi</button>
  <form method="post" action="/save">
    <label for="networks">Mạng tìm thấy</label>
    <select id="networks"><option value="">Đang tải danh sách...</option></select>
    <label for="ssid">Tên Wi-Fi (SSID)</label>
    <input id="ssid" name="ssid" maxlength="32" required autocomplete="username">
    <label for="password">Mật khẩu Wi-Fi</label>
    <input id="password" name="password" type="password" maxlength="64" autocomplete="current-password">
    <button type="submit">Lưu và kết nối</button>
  </form>
  <div id="status" role="status"></div>
  <script>
    const statusBox=document.getElementById("status");
    const list=document.getElementById("networks");
    async function scan(){
      statusBox.textContent="Đang quét mạng...";
      list.replaceChildren();
      try{
        const response=await fetch("/scan",{cache:"no-store"});
        if(!response.ok)throw new Error("Không quét được mạng Wi-Fi.");
        const networks=await response.json();
        if(!networks.length){
          list.add(new Option("Không tìm thấy mạng", ""));
          statusBox.textContent="Kiểm tra lại hoặc nhập SSID thủ công.";
          return;
        }
        list.add(new Option("Chọn mạng Wi-Fi", ""));
        networks.forEach(function(network){
          const label=network.ssid+" ("+network.rssi+" dBm)"+(network.open?"":" · có mật khẩu");
          list.add(new Option(label,network.ssid));
        });
        statusBox.textContent="Đã tìm thấy "+networks.length+" mạng.";
      }catch(error){statusBox.textContent=error.message;}
    }
    list.addEventListener("change",function(){if(list.value)document.getElementById("ssid").value=list.value;});
    document.getElementById("scan").addEventListener("click",scan);
    scan();
  </script>
</body>
</html>
)rawliteral";
}

bool WiFiSetupPortal::begin(const char* fallbackSsid,
                            const char* fallbackPassword)
{
    preferencesReady = preferences.begin(PREFERENCES_NAMESPACE, false);
    if (!preferencesReady) {
        Serial.println("ERROR: Could not open Wi-Fi settings storage.");
        ssid = fallbackSsid;
        password = fallbackPassword;
    } else {
        ssid = preferences.getString("ssid", fallbackSsid);
        password = preferences.getString("password", fallbackPassword);
    }

    registerRoutes();
    const bool configured =
        ssid.length() > 0 && ssid != "YOUR_WIFI_NAME";
    if (configured) {
        WiFi.mode(WIFI_STA);
        connect();
    }
    return configured;
}

void WiFiSetupPortal::registerRoutes()
{
    server.on("/", HTTP_GET, [this]() { handleRoot(); });
    server.on("/scan", HTTP_GET, [this]() { handleScan(); });
    server.on("/save", HTTP_POST, [this]() { handleSave(); });
    server.onNotFound([this]() {
        server.sendHeader("Location", "/", true);
        server.send(302, "text/plain", "");
    });
}

bool WiFiSetupPortal::startPortal()
{
    if (portalActive) {
        return true;
    }

    setupPassword = WIFI_SETUP_PASSWORD;

    WiFi.mode(WIFI_AP_STA);
    if (!WiFi.softAP(SETUP_AP_NAME, setupPassword.c_str())) {
        Serial.println("ERROR: Could not start Wi-Fi setup access point.");
        return false;
    }

    dnsServer.start(53, "*", WiFi.softAPIP());
    server.begin();
    portalActive = true;
    Serial.printf("Wi-Fi setup page: connect to '%s' with password '%s', then open http://%s\n",
                  SETUP_AP_NAME, setupPassword.c_str(),
                  WiFi.softAPIP().toString().c_str());
    return true;
}

bool WiFiSetupPortal::handleClient()
{
    if (portalActive) {
        dnsServer.processNextRequest();
        server.handleClient();
    }

    if (!pendingConnect || millis() < connectAfter) {
        return false;
    }

    pendingConnect = false;
    stopPortal();
    WiFi.mode(WIFI_STA);
    connect();
    return true;
}

void WiFiSetupPortal::connect()
{
    Serial.printf("Connecting to configured Wi-Fi network '%s'.\n", ssid.c_str());
    WiFi.begin(ssid.c_str(), password.c_str());
}

void WiFiSetupPortal::stopPortal()
{
    if (!portalActive) {
        return;
    }

    server.stop();
    dnsServer.stop();
    WiFi.softAPdisconnect(true);
    portalActive = false;
    Serial.println("Wi-Fi setup access point stopped.");
}

void WiFiSetupPortal::handleRoot()
{
    server.send_P(200, "text/html; charset=utf-8", SETUP_PAGE);
}

void WiFiSetupPortal::handleScan()
{
    const int count = WiFi.scanNetworks(false, true);
    if (count < 0) {
        WiFi.scanDelete();
        server.send(503, "application/json", "{\"error\":\"scan_failed\"}");
        return;
    }

    JsonDocument document;
    JsonArray networks = document.to<JsonArray>();
    for (int i = 0; i < count; ++i) {
        if (WiFi.SSID(i).isEmpty()) {
            continue;
        }
        JsonObject network = networks.add<JsonObject>();
        network["ssid"] = WiFi.SSID(i);
        network["rssi"] = WiFi.RSSI(i);
        network["open"] = WiFi.encryptionType(i) == WIFI_AUTH_OPEN;
    }

    String body;
    serializeJson(document, body);
    WiFi.scanDelete();
    server.send(200, "application/json", body);
}

void WiFiSetupPortal::handleSave()
{
    const String requestedSsid = server.arg("ssid");
    const String requestedPassword = server.arg("password");
    if (requestedSsid.isEmpty() || requestedSsid.length() > 32 ||
        requestedPassword.length() > 64) {
        server.send(400, "text/plain; charset=utf-8",
                    "SSID hoặc mật khẩu không hợp lệ.");
        return;
    }

    if (!preferencesReady) {
        preferencesReady = preferences.begin(PREFERENCES_NAMESPACE, false);
    }
    if (!preferencesReady) {
        server.send(500, "text/plain; charset=utf-8",
                    "Không thể mở bộ nhớ cài đặt Wi-Fi.");
        return;
    }
    if (preferences.putString("ssid", requestedSsid) == 0 ||
        preferences.putString("password", requestedPassword) == 0) {
        server.send(500, "text/plain; charset=utf-8",
                    "Không thể lưu thông tin Wi-Fi.");
        return;
    }

    ssid = requestedSsid;
    password = requestedPassword;
    server.send(200, "text/html; charset=utf-8",
                "<meta charset='utf-8'><p>Đã lưu Wi-Fi. ESP32 đang kết nối; "
                "hãy chờ vài giây rồi kiểm tra dữ liệu trên dashboard.</p>");
    pendingConnect = true;
    connectAfter = millis() + 1500;
}
