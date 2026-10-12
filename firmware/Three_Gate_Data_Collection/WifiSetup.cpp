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
<p id="status" role="status">尚未保存。</p><p>取得 IP 並保存成功後，設定熱點會自動關閉。請將手機切回目標 Wi-Fi，再掃 Dashboard 相機 QR。保存不等待 Django 上線。</p>
<script>
const form=document.querySelector('#setup'),status=document.querySelector('#status'),save=document.querySelector('#save');let busy=false;
form.addEventListener('submit',async e=>{e.preventDefault();save.disabled=true;try{const r=await fetch('/save',{method:'POST',body:new URLSearchParams(new FormData(form))});const s=await r.text();form.elements.password.value='';status.textContent=s;busy=r.ok;if(!busy)save.disabled=false}catch{status.textContent='設定頁連線失敗，請確認仍連上設定熱點。';save.disabled=false}});
document.querySelector('#scan').onclick=async()=>{const target=document.querySelector('#scan-status');target.textContent='掃描中…';try{const r=await fetch('/scan',{method:'POST'});target.textContent=await r.text()}catch{target.textContent='掃描請求失敗，可直接輸入 SSID。'}};
setInterval(async()=>{try{const r=await fetch('/status',{cache:'no-store'});const s=await r.text();status.textContent=s;if(s.startsWith('saved')){busy=false;save.disabled=true}else if(!s.startsWith('connecting')){busy=false;save.disabled=false}const scan=await fetch('/scan',{cache:'no-store'});if(scan.status===200){const names=(await scan.text()).split('\n');document.querySelector('#networks').replaceChildren(...names.filter(Boolean).map(n=>{const o=document.createElement('option');o.value=n;return o}));document.querySelector('#scan-status').textContent='掃描完成，也可直接輸入。'}}catch{if(!busy)status.textContent='熱點可能已關閉，請切回目標 Wi-Fi。'}},500);
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
  server_.on("/", HTTP_GET, [this] { server_.sendHeader("Cache-Control", "no-store"); server_.send_P(200, "text/html; charset=utf-8", kPage); });
  server_.on("/save", HTTP_POST, [this] { saveRequest(); });
  server_.on("/scan", HTTP_POST, [this] { scanRequest(); });
  server_.on("/scan", HTTP_GET, [this] {
    const int count = WiFi.scanComplete();
    String names;
    if (count >= 0) for (int i = 0; i < count; ++i) names += WiFi.SSID(i) + "\n";
    server_.send(count < 0 ? 202 : 200, "text/plain; charset=utf-8", names);
  });
  server_.on("/status", HTTP_GET, [this] { statusRequest(); });
  if (configured()) reconnect();
}

void WifiSetup::open() {
  if (!validConfig("setup", setupApPassword) || String(setupApPassword).length() < 8 || String(setupApPassword).length() > 63) {
    reason_ = "setup_ap_failed";
    Serial.println(reason_);
    return;
  }
  const String mac = WiFi.macAddress();
  String suffix;
  for (unsigned i = 9; i < mac.length(); ++i) if (mac.charAt(i) != ':') suffix += mac.charAt(i);
  WiFi.mode(WIFI_AP_STA);
  WiFi.setAutoReconnect(false);
  if (!WiFi.softAP(("PF-Setup-" + suffix).c_str(), setupApPassword)) {
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    reason_ = "setup_ap_failed";
    Serial.println(reason_);
    return;
  }
  active_ = true;
  saved_ = false;
  reason_ = "ready";
  server_.begin();
}

void WifiSetup::saveRequest() {
  server_.sendHeader("Cache-Control", "no-store");
  if (connecting_ || saved_) { server_.send(409, "text/plain", "setup_busy"); return; }
  const String ssid = server_.arg("ssid"), password = server_.arg("password"), origin = server_.arg("origin");
  if (!validConfig(ssid, password) || (origin.length() && !validOrigin(origin, false))) {
    server_.send(400, "text/plain", "wifi_configuration_invalid");
    return;
  }
  if (WiFi.scanComplete() == WIFI_SCAN_RUNNING && esp_wifi_scan_stop() != ESP_OK) {
    server_.send(503, "text/plain", "wifi_scan_stop_failed");
    return;
  }
  candidate_ = Record();
  memcpy(candidate_.ssid, ssid.c_str(), ssid.length());
  memcpy(candidate_.password, password.c_str(), password.length());
  memcpy(candidate_.origin, origin.c_str(), origin.length());
  WiFi.scanDelete();
  WiFi.disconnect(false, false);
  WiFi.begin(candidate_.ssid, candidate_.password);
  connecting_ = true;
  phaseStartedAt_ = millis();
  reason_ = "connecting";
  server_.send(202, "text/plain", reason_);
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
  server_.sendHeader("Cache-Control", "no-store");
  server_.send(200, "text/plain; charset=utf-8", saved_ ? "saved\nIP: " + WiFi.localIP().toString() + "\n請切回目標 Wi-Fi。" : reason_);
}

void WifiSetup::fail(const char* reason) {
  connecting_ = false;
  candidate_ = Record();
  reason_ = reason;
  WiFi.disconnect(false, false);
  if (configured()) reconnect();
}

void WifiSetup::tick(uint32_t now, bool requested) {
  if (requested && !active_) open();
  if (!active_) return;
  server_.handleClient();
  now = millis();
  if (connecting_) {
    if (now - phaseStartedAt_ >= FirmwareConfig::kWifiConnectAttemptTimeoutMS) { fail("wifi_connect_timeout"); return; }
    if (WiFi.status() != WL_CONNECTED || !usableIP(WiFi.localIP()) || WiFi.SSID() != String(candidate_.ssid)) return;
    if (candidate_.origin[0] && !validOrigin(candidate_.origin, true)) { fail("wifi_configuration_invalid"); return; }
    if (!storageReady_) storageReady_ = preferences_.begin("pf-wifi", false);
    if (!storageReady_ || preferences_.putBytes("config", &candidate_, sizeof(candidate_)) != sizeof(candidate_)) {
      fail("wifi_config_save_failed");
      return;
    }
    record_ = candidate_;
    candidate_ = Record();
    connecting_ = false;
    saved_ = true;
    phaseStartedAt_ = now;
    reason_ = "saved";
  }
  if (saved_ && now - phaseStartedAt_ >= 3000UL) {
    WiFi.softAPdisconnect(false);
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    WiFi.scanDelete();
    server_.stop();
    active_ = false;
  }
}

WifiSetup::~WifiSetup() {
  if (active_) { server_.stop(); WiFi.softAPdisconnect(false); }
}

void WifiSetup::reconnect() const { WiFi.begin(record_.ssid, record_.password); }
