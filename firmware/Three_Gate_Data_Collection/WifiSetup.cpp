#include "WifiSetup.h"
#include <ctype.h>
#include <cstring>
#include <esp_wifi.h>
#include "Config.h"
#if __has_include("setup.local.h")
#include "setup.local.h"
#else
const char* setupApPassword = "";
#endif

namespace {
const char kPage[] PROGMEM = R"HTML(<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ESP32 網路設定</title>
<style>body{font:18px sans-serif;max-width:32em;margin:2em auto;padding:1em}label,input,button{display:block;margin:.7em 0}input{box-sizing:border-box;width:100%;padding:.5em}button{padding:.6em}#status{white-space:pre-wrap}</style>
<h1>ESP32 網路設定</h1><p>使用可互通的 2.4 GHz Wi-Fi。可直接輸入 SSID，無須等待掃描。</p>
<button id="scan" type="button">掃描 SSID</button><p id="scan-status" role="status"></p>
<form id="setup"><label>SSID<input name="ssid" list="networks" maxlength="32" required></label><datalist id="networks"></datalist>
<label>Wi-Fi 密碼<input name="password" type="password" maxlength="64" autocomplete="new-password"></label>
<label>手動 Django HTTPS origin（選填）<input name="origin" placeholder="https://192.168.1.10:8000" maxlength="47"></label><button id="save">連線並保存</button></form>
<p id="status" role="status">尚未保存。</p><button id="exit" type="button" disabled>退出連線</button><button id="cancel" type="button">取消設定</button>
<p>取得 IP 並保存成功後，按「退出連線」關閉設定熱點，再將手機切回目標 Wi-Fi，掃 Dashboard 相機 QR。ESP32 會保留目標 Wi-Fi 連線。保存不等待 Django 上線。</p>
<p>不需變更時按「取消設定」或再按一下 BOOT，ESP32 會關閉熱點並恢復原本保存的 Wi-Fi；已有保存設定且設定頁長時間未開啟時也會自動取消。ESP32 連線目標 Wi-Fi 時熱點可能切換頻道，手機若暫時斷開，重新連上設定熱點即可。</p>
<script>
const form=document.querySelector('#setup'),status=document.querySelector('#status'),save=document.querySelector('#save'),exit=document.querySelector('#exit'),cancel=document.querySelector('#cancel');let busy=false,scanning=false,closing='',closingText='';
form.addEventListener('submit',async e=>{e.preventDefault();scanning=false;save.disabled=true;exit.disabled=true;try{const r=await fetch('/save',{method:'POST',body:new URLSearchParams(new FormData(form))});const s=await r.text();form.elements.password.value='';status.textContent=s;busy=r.ok;if(!busy)save.disabled=false}catch{status.textContent='設定頁連線失敗，請確認仍連上設定熱點。';save.disabled=false}});
document.querySelector('#scan').onclick=async()=>{const target=document.querySelector('#scan-status');target.textContent='掃描中…';try{const r=await fetch('/scan',{method:'POST'});scanning=r.status===202;target.textContent=await r.text()}catch{target.textContent='掃描請求失敗，可直接輸入 SSID。'}};
const requestClose=async(kind,button,text)=>{button.disabled=true;try{const r=await fetch('/'+kind,{method:'POST'});const s=await r.text();if(r.ok){closing=kind;closingText=text}status.textContent=r.ok?text:s;if(!r.ok&&kind==='cancel')button.disabled=false}catch{status.textContent='請求失敗，請確認仍連上設定熱點。';if(kind==='cancel')button.disabled=false}};
exit.onclick=()=>requestClose('exit',exit,'退出請求已送出，請將手機切回目標 Wi-Fi。');
cancel.onclick=()=>requestClose('cancel',cancel,'已取消設定，熱點即將關閉；ESP32 會恢復原本保存的 Wi-Fi。');
const statusPoll=setInterval(async()=>{try{const r=await fetch('/status',{cache:'no-store'});const s=await r.text();const ready=s.startsWith('saved');if(closing==='cancel'||(closing==='exit'&&ready))return;closing='';status.textContent=s;exit.disabled=!ready;if(ready){busy=false;save.disabled=true}else if(!s.startsWith('connecting')){busy=false;save.disabled=false}else busy=true;if(!scanning)return;const scan=await fetch('/scan',{cache:'no-store'});if(scan.status===200){scanning=false;const names=(await scan.text()).split('\n');document.querySelector('#networks').replaceChildren(...names.filter(Boolean).map(n=>{const o=document.createElement('option');o.value=n;return o}));document.querySelector('#scan-status').textContent='掃描完成，也可直接輸入。'}}catch{if(closing){clearInterval(statusPoll);status.textContent=closingText}else status.textContent=busy?'手機可能因 ESP32 切換頻道暫時離開熱點；請重新連上設定熱點，本頁會自動更新。':'設定頁連線失敗，請確認仍連上設定熱點。'}},500);
</script></html>)HTML";
bool usableIP(const IPAddress& ip) {
  return ip[0] != 0 && ip[0] != 127 && ip[0] < 224 && !(ip[0] == 169 && ip[1] == 254);
}
}

bool WifiSetup::validConfig(const String& ssid, const String& password) {
  if (ssid.length() == 0 || ssid.length() > 32) return false;
  for (unsigned i = 0; i < ssid.length(); ++i) if (static_cast<uint8_t>(ssid.charAt(i)) < 32) return false;
  if (password.length() == 0) return true;
  if (password.length() < 8 || password.length() > 64) return false;
  for (unsigned i = 0; i < password.length(); ++i) {
    const unsigned char c = password.charAt(i);
    if (password.length() == 64 ? !isxdigit(c) : c < 32 || c > 126) return false;
  }
  return true;
}

bool WifiSetup::validOrigin(const String& origin, bool sameSubnet) {
  if (!origin.startsWith("https://") || origin.length() >= sizeof(Record::origin)) return false;
  const String hostPort = origin.substring(8);
  const int colon = hostPort.indexOf(':');
  const String host = colon < 0 ? hostPort : hostPort.substring(0, colon);
  const String port = colon < 0 ? String("443") : hostPort.substring(colon + 1);
  if (port.length() == 0 || port.length() > 5) return false;
  for (unsigned i = 0; i < port.length(); ++i) if (!isdigit(static_cast<unsigned char>(port.charAt(i)))) return false;
  IPAddress ip;
  if (!ip.fromString(host) || ip.toString() != host || !usableIP(ip) || port.toInt() < 1 || port.toInt() > 65535) return false;
  const uint32_t mask = static_cast<uint32_t>(WiFi.subnetMask());
  const uint32_t address = static_cast<uint32_t>(ip);
  const bool local = mask != 0 && usableIP(WiFi.localIP()) &&
      (address & mask) == (static_cast<uint32_t>(WiFi.localIP()) & mask) &&
      (address & ~mask) != 0 && (address & ~mask) != ~mask;
  if (sameSubnet) return local;
  return local || ip[0] == 10 || (ip[0] == 172 && ip[1] >= 16 && ip[1] <= 31) || (ip[0] == 192 && ip[1] == 168);
}

void WifiSetup::begin() {
  storageReady_ = preferences_.begin("pf-wifi", false);
  Record stored;
  if (storageReady_ && preferences_.getBytesLength("config") == sizeof(stored) &&
      preferences_.getBytes("config", &stored, sizeof(stored)) == sizeof(stored) && stored.schema == 1 &&
      memchr(stored.ssid, 0, sizeof(stored.ssid)) && memchr(stored.password, 0, sizeof(stored.password)) &&
      memchr(stored.origin, 0, sizeof(stored.origin)) && validConfig(stored.ssid, stored.password)) record_ = stored;
  // Counting events proves a candidate result is new, not left over from the association being replaced.
  if (!eventHandler_) {
    eventHandler_ = WiFi.onEvent([this](arduino_event_id_t event, arduino_event_info_t info) {
      if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
        gotIpEvents_.fetch_add(1, std::memory_order_release);
      } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
        lastDisconnectReason_.store(info.wifi_sta_disconnected.reason, std::memory_order_relaxed);
        disconnectEvents_.fetch_add(1, std::memory_order_release);
      }
    });
  }
  server_.on("/", HTTP_GET, [this] {
    if (!acceptSetupClient()) return;
    server_.send_P(200, "text/html; charset=utf-8", kPage);
  });
  server_.on("/save", HTTP_POST, [this] { if (acceptSetupClient()) saveRequest(); });
  server_.on("/exit", HTTP_POST, [this] { if (acceptSetupClient()) exitRequest(); });
  server_.on("/cancel", HTTP_POST, [this] { if (acceptSetupClient()) cancelRequest(); });
  server_.on("/scan", HTTP_POST, [this] { if (acceptSetupClient()) scanRequest(); });
  server_.on("/scan", HTTP_GET, [this] {
    if (!acceptSetupClient()) return;
    const int count = WiFi.scanComplete();
    String names;
    if (count >= 0) for (int i = 0; i < count; ++i) names += WiFi.SSID(i) + "\n";
    server_.send(count < 0 ? 202 : 200, "text/plain; charset=utf-8", names);
  });
  server_.on("/status", HTTP_GET, [this] { if (acceptSetupClient()) statusRequest(); });
}

// Only definitive results end an attempt early; transient reasons still get the full timeout.
const char* WifiSetup::attemptFailure(uint8_t reason) {
  switch (reason) {
    case WIFI_REASON_AUTH_FAIL:
    case WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_HANDSHAKE_TIMEOUT:
    case WIFI_REASON_MIC_FAILURE:
      return "wifi_auth_failed";
    case WIFI_REASON_NO_AP_FOUND:
    case WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY:
    case WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD:
    case WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD:
      return "wifi_ssid_not_found";
    default:
      return nullptr;
  }
}

// The server listens on every interface; only the password-protected setup AP may use it.
bool WifiSetup::acceptSetupClient() {
  server_.sendHeader("Cache-Control", "no-store");
  if (server_.client().localIP() != WiFi.softAPIP()) {
    server_.send(403, "text/plain", "setup_ap_only");
    return false;
  }
  lastActivityAt_ = millis();
  return true;
}

void WifiSetup::open() {
  if (!validConfig("setup", setupApPassword) || String(setupApPassword).length() < 8 || String(setupApPassword).length() > 63) {
    openFailure_ = "setup_ap_failed";
    reason_ = openFailure_;
    Serial.println("setup_ap_failed: setup.local.h password must be 8-63 printable characters");
    return;
  }
  const String mac = WiFi.macAddress();
  String suffix;
  for (unsigned i = 9; i < mac.length(); ++i) if (mac.charAt(i) != ':') suffix += mac.charAt(i);
  WiFi.mode(WIFI_AP_STA);
  // The AP follows the STA channel; an idle reconnect attempt would keep hopping it.
  if (WiFi.status() != WL_CONNECTED) WiFi.disconnect(false, false);
  if (!WiFi.softAP(("PF-Setup-" + suffix).c_str(), setupApPassword)) {
    WiFi.mode(WIFI_STA);
    openFailure_ = "setup_ap_failed";
    reason_ = openFailure_;
    Serial.println("setup_ap_failed: softAP start failed");
    return;
  }
  openFailure_ = "";
  active_ = true;
  saved_ = false;
  exitRequested_ = false;
  cancelRequested_ = false;
  lastActivityAt_ = millis();
  reason_ = "ready";
  server_.begin();
}

void WifiSetup::close() {
  if (WiFi.scanComplete() == WIFI_SCAN_RUNNING) esp_wifi_scan_stop();
  WiFi.softAPdisconnect(false);
  WiFi.mode(WIFI_STA);
  WiFi.scanDelete();
  server_.stop();
  active_ = false;
  connecting_ = false;
  saved_ = false;
  exitRequested_ = false;
  cancelRequested_ = false;
  candidate_ = Record();
}

// Leaves the saved Wi-Fi untouched; the network worker reconnects it when it is not up.
void WifiSetup::cancelSetup() {
  const bool savedLinkUp = !connecting_ && WiFi.status() == WL_CONNECTED && WiFi.SSID() == String(record_.ssid);
  if (!savedLinkUp) WiFi.disconnect(false, false);
  close();
  Serial.println("setup_cancelled");
}

void WifiSetup::startConnect(const Record& record) {
  candidate_ = record;
  // A scanned channel hint keeps the STA, and the AP that follows it, from sweeping every channel.
  int32_t channel = 0;
  int32_t rssi = INT32_MIN;
  const int count = WiFi.scanComplete();
  for (int i = 0; i < count; ++i) {
    if (WiFi.SSID(i) != candidate_.ssid || WiFi.RSSI(i) <= rssi) continue;
    rssi = WiFi.RSSI(i);
    channel = WiFi.channel(i);
  }
  WiFi.scanDelete();
  WiFi.disconnect(false, false);
  gotIpBaseline_ = gotIpEvents_.load(std::memory_order_acquire);
  disconnectBaseline_ = disconnectEvents_.load(std::memory_order_acquire);
  WiFi.begin(candidate_.ssid, candidate_.password, channel);
  connecting_ = true;
  phaseStartedAt_ = millis();
  reason_ = "connecting";
}

void WifiSetup::saveRequest() {
  if (connecting_ || saved_ || exitRequested_ || cancelRequested_) { server_.send(409, "text/plain", "setup_busy"); return; }
  const String ssid = server_.arg("ssid"), password = server_.arg("password"), origin = server_.arg("origin");
  if (!validConfig(ssid, password) || (origin.length() && !validOrigin(origin, false))) {
    server_.send(400, "text/plain", "wifi_configuration_invalid");
    return;
  }
  if (WiFi.scanComplete() == WIFI_SCAN_RUNNING && esp_wifi_scan_stop() != ESP_OK) {
    server_.send(503, "text/plain", "wifi_scan_stop_failed");
    return;
  }
  Record next;
  memcpy(next.ssid, ssid.c_str(), ssid.length());
  memcpy(next.password, password.c_str(), password.length());
  memcpy(next.origin, origin.c_str(), origin.length());
  startConnect(next);
  server_.send(202, "text/plain", reason_);
}

bool WifiSetup::savedConnectionReady() const {
  return saved_ && WiFi.status() == WL_CONNECTED && usableIP(WiFi.localIP()) && WiFi.SSID() == String(record_.ssid);
}

void WifiSetup::exitRequest() {
  if (!savedConnectionReady() || exitRequested_ || cancelRequested_) {
    server_.send(409, "text/plain", "setup_exit_unavailable");
    return;
  }
  exitRequested_ = true;
  closeRequestedAt_ = millis();
  server_.send(200, "text/plain", "setup_closed");
}

void WifiSetup::cancelRequest() {
  if (exitRequested_ || cancelRequested_) {
    server_.send(409, "text/plain", "setup_cancel_unavailable");
    return;
  }
  cancelRequested_ = true;
  closeRequestedAt_ = millis();
  server_.send(200, "text/plain", "setup_cancelled");
}

void WifiSetup::scanRequest() {
  if (connecting_ || saved_) { server_.send(409, "text/plain", "setup_busy"); return; }
  if (WiFi.scanComplete() != WIFI_SCAN_RUNNING) {
    WiFi.scanDelete();
    WiFi.scanNetworks(true, true);
  }
  server_.send(202, "text/plain; charset=utf-8", "掃描中，可直接輸入 SSID。");
}

void WifiSetup::statusRequest() {
  server_.send(200, "text/plain; charset=utf-8", saved_ ? "saved\nIP: " + WiFi.localIP().toString() + "\n請按「退出連線」關閉設定熱點，再切回目標 Wi-Fi。" : reason_);
}

void WifiSetup::fail(const char* reason) {
  connecting_ = false;
  candidate_ = Record();
  reason_ = reason;
  WiFi.disconnect(false, false);
  if (configured()) reconnect();
}

void WifiSetup::tick(bool requested, bool cancel) {
  if (requested && !active_) open();
  if (!active_) return;
  if (cancel) { cancelSetup(); return; }
  if (saved_ && !savedConnectionReady()) {
    // Setup disables auto-reconnect, so retry the just-saved network explicitly.
    saved_ = false;
    exitRequested_ = false;
    startConnect(record_);
    reason_ = "connecting\nwifi_connection_lost";
  }
  server_.handleClient();
  const uint32_t now = millis();
  // Let the cancel response reach the phone before shutting down its connection.
  if (cancelRequested_) {
    if (now - closeRequestedAt_ >= 500UL) cancelSetup();
    return;
  }
  if (!connecting_ && configured() && now - lastActivityAt_ >= FirmwareConfig::kSetupIdleTimeoutMS) {
    Serial.println("setup_idle_timeout");
    cancelSetup();
    return;
  }
  if (connecting_) {
    if (now - phaseStartedAt_ >= FirmwareConfig::kWifiConnectAttemptTimeoutMS) { fail("wifi_connect_timeout"); return; }
    const uint32_t disconnects = disconnectEvents_.load(std::memory_order_acquire);
    if (disconnects != disconnectBaseline_) {
      disconnectBaseline_ = disconnects;
      const char* failure = attemptFailure(lastDisconnectReason_.load(std::memory_order_relaxed));
      if (failure) { fail(failure); return; }
    }
    if (gotIpEvents_.load(std::memory_order_acquire) == gotIpBaseline_ || WiFi.status() != WL_CONNECTED ||
        !usableIP(WiFi.localIP()) || WiFi.SSID() != String(candidate_.ssid)) return;
    if (candidate_.origin[0] && !validOrigin(candidate_.origin, true)) { fail("wifi_configuration_invalid"); return; }
    const bool unchanged = !strcmp(candidate_.ssid, record_.ssid) && !strcmp(candidate_.password, record_.password) &&
        !strcmp(candidate_.origin, record_.origin);
    if (!unchanged && !storageReady_) storageReady_ = preferences_.begin("pf-wifi", false);
    if (!unchanged && (!storageReady_ || preferences_.putBytes("config", &candidate_, sizeof(candidate_)) != sizeof(candidate_))) {
      fail("wifi_config_save_failed");
      return;
    }
    record_ = candidate_;
    candidate_ = Record();
    connecting_ = false;
    saved_ = true;
    reason_ = "saved";
  }
  // Let the exit response reach the phone before shutting down its connection.
  if (exitRequested_ && now - closeRequestedAt_ >= 500UL && savedConnectionReady()) close();
}

WifiSetup::~WifiSetup() {
  if (eventHandler_) WiFi.removeEvent(eventHandler_);
  if (active_) { server_.stop(); WiFi.softAPdisconnect(false); }
}

void WifiSetup::reconnect() const { WiFi.begin(record_.ssid, record_.password); }
