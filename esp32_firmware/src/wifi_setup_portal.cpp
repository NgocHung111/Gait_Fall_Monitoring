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
  <meta name="theme-color" content="#f5f7fb">
  <title>GaitWatch | Cài đặt thiết bị</title>
  <style>
    :root{color-scheme:light;--ink:#1d2738;--muted:#8490a2;--line:#e9edf3;--surface:#fff;--canvas:#f5f7fb;--blue:#4775f5;--green:#2daf87;font-family:Arial,sans-serif}
    *{box-sizing:border-box}
    body{min-width:320px;min-height:100vh;margin:0;color:var(--ink);background:var(--canvas)}
    .topbar{height:68px;display:flex;align-items:center;justify-content:space-between;padding:0 max(22px,calc((100vw - 1040px)/2));background:var(--surface);border-bottom:1px solid var(--line)}
    .brand{display:flex;align-items:center;gap:10px;color:#233047;font-size:20px;font-weight:800;letter-spacing:-.7px}
    .brand-mark{display:grid;width:36px;height:36px;place-items:center;border-radius:11px;color:#fff;background:#5278f4;box-shadow:0 5px 12px #5278f433}
    .brand-mark svg{width:24px;height:24px}
    .connection{display:flex;align-items:center;gap:8px;color:#708098;font-size:12px}
    .dot{width:8px;height:8px;border-radius:50%;background:#e9a23b;box-shadow:0 0 0 4px #fff3e4}
    main{width:min(100% - 32px,760px);margin:38px auto 60px}
    .crumb{margin:0 0 26px;color:#8b97a8;font-size:12px}
    .crumb span{margin:0 8px;color:#c0c8d3}
    .eyebrow{margin:0 0 8px;color:#7185c7;font-size:10px;font-weight:700;letter-spacing:1.4px;text-transform:uppercase}
    h1{margin:0;font-size:clamp(25px,6vw,34px);letter-spacing:-1.1px}
    .intro{margin:9px 0 24px;color:#7f8ba0;font-size:14px;line-height:1.6}
    .notice{display:flex;gap:12px;align-items:flex-start;margin:0 0 20px;padding:14px 16px;border:1px solid #e6ecff;border-radius:11px;background:#f8f9ff;color:#63749d;font-size:12px;line-height:1.6}
    .notice-icon{flex:0 0 24px;display:grid;width:24px;height:24px;place-items:center;border-radius:8px;color:#5278f4;background:#eaf0ff;font-weight:700}
    .card{padding:24px;border:1px solid var(--line);border-radius:14px;background:var(--surface);box-shadow:0 8px 26px #263b6608}
    .card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}
    h2{margin:0;font-size:16px;letter-spacing:-.2px}
    .device{color:#97a2b3;font-size:11px}
    label{display:block;margin:18px 0 7px;color:#53627a;font-size:12px;font-weight:700}
    select,input{display:block;width:100%;min-height:46px;padding:11px 13px;border:1px solid #dfe5ee;border-radius:9px;background:#fff;color:var(--ink);font:inherit;font-size:14px;outline:none}
    select:focus,input:focus{border-color:#86a0fa;box-shadow:0 0 0 3px #4775f51a}
    .field-help{margin:7px 0 0;color:#9aa5b5;font-size:11px;line-height:1.5}
    .scan{display:flex;width:100%;min-height:42px;align-items:center;justify-content:center;gap:8px;margin:0 0 4px;border:1px solid #dfe6fb;border-radius:9px;color:#4268de;background:#f7f9ff;font:inherit;font-size:13px;font-weight:700;cursor:pointer}
    .scan:hover{background:#edf2ff}
    .scan:disabled{opacity:.6;cursor:wait}
    .submit{width:100%;min-height:46px;margin-top:23px;border:0;border-radius:9px;color:#fff;background:var(--blue);font:inherit;font-size:14px;font-weight:700;cursor:pointer;box-shadow:0 5px 12px #5278f433}
    .submit:hover{background:#3e68e7}
    .status{display:none;margin-top:16px;padding:12px 14px;border:1px solid transparent;border-radius:9px;font-size:12px;line-height:1.5}
    .status.visible{display:block}
    .status.info{border-color:#e4ebff;color:#536baf;background:#f7f9ff}
    .status.success{border-color:#d8f1e8;color:#238665;background:#f3fbf7}
    .status.error{border-color:#f5dada;color:#b34848;background:#fff7f7}
    .foot{margin:18px 0 0;text-align:center;color:#a0a9b7;font-size:11px}
    @media(max-width:520px){.topbar{height:60px;padding:0 18px}main{margin-top:26px}.card{padding:19px}.device{display:none}}
  </style>
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none"><path d="M3 12h4l2.2-6 4.1 12 2.3-6H21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </span>
      gait<span style="color:#8490a2;font-weight:600">watch</span>
    </div>
    <div class="connection"><span class="dot"></span>Đang cài đặt thiết bị</div>
  </header>
  <main>
    <p class="crumb">Thiết bị <span>/</span> Cài đặt Wi-Fi</p>
    <p class="eyebrow">Thiết lập thiết bị</p>
    <h1>Kết nối GaitWatch</h1>
    <p class="intro">Chọn mạng Wi-Fi tại nơi thiết bị sẽ hoạt động. Sau khi lưu, ESP32 ghi nhớ mạng này để tự kết nối những lần sau.</p>
    <div class="notice">
      <span class="notice-icon" aria-hidden="true">i</span>
      <span>Chỉ chọn Wi-Fi <strong>2.4 GHz</strong>. Nếu dùng hotspot điện thoại, hãy bật hotspot trước khi quét mạng.</span>
    </div>
    <section class="card" aria-labelledby="wifi-title">
      <div class="card-head">
        <h2 id="wifi-title">Mạng Wi-Fi</h2>
        <span class="device">GaitWatch · ESP32</span>
      </div>
      <button class="scan" type="button" id="scan">
        <span aria-hidden="true">↻</span> Quét lại mạng Wi-Fi
      </button>
      <form method="post" action="/save">
        <label for="networks">Mạng tìm thấy</label>
        <select id="networks" aria-label="Mạng Wi-Fi gần đây">
          <option value="">Đang quét mạng...</option>
        </select>
        <p class="field-help">Không thấy mạng? Nhập tên mạng (SSID) bên dưới.</p>
        <label for="ssid">Tên Wi-Fi (SSID)</label>
        <input id="ssid" name="ssid" maxlength="32" required autocomplete="username" placeholder="Ví dụ: WiFi-Nha">
        <label for="password">Mật khẩu Wi-Fi</label>
        <input id="password" name="password" type="password" maxlength="64" autocomplete="current-password" placeholder="Nhập mật khẩu mạng">
        <button class="submit" type="submit">Lưu và kết nối</button>
      </form>
      <div class="status" id="status" role="status" aria-live="polite"></div>
    </section>
    <p class="foot">Cài đặt được lưu an toàn trên thiết bị của bạn.</p>
  </main>
  <script>
    const statusBox=document.getElementById("status");
    const list=document.getElementById("networks");
    const scanButton=document.getElementById("scan");
    function showStatus(message,type){
      statusBox.textContent=message;
      statusBox.className="status visible "+type;
    }
    async function scan(){
      scanButton.disabled=true;
      showStatus("Đang quét các mạng Wi-Fi gần thiết bị...","info");
      list.replaceChildren();
      try{
        const response=await fetch("/scan",{cache:"no-store"});
        if(!response.ok)throw new Error("Không quét được mạng Wi-Fi.");
        const networks=await response.json();
        if(!networks.length){
          list.add(new Option("Không tìm thấy mạng", ""));
          showStatus("Không tìm thấy mạng. Kiểm tra Wi-Fi 2.4 GHz hoặc nhập SSID thủ công.","error");
          return;
        }
        list.add(new Option("Chọn mạng Wi-Fi", ""));
        networks.forEach(function(network){
          const label=network.ssid+" ("+network.rssi+" dBm)"+(network.open?"":" · có mật khẩu");
          list.add(new Option(label,network.ssid));
        });
        showStatus("Đã tìm thấy "+networks.length+" mạng Wi-Fi.","success");
      }catch(error){showStatus(error.message,"error");}
      finally{scanButton.disabled=false;}
    }
    list.addEventListener("change",function(){if(list.value)document.getElementById("ssid").value=list.value;});
    scanButton.addEventListener("click",scan);
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
