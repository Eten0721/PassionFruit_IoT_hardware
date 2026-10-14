#pragma once
#include "Arduino.h"
#include "IPAddress.h"
#include <map>
using network_event_handle_t = unsigned int;
enum arduino_event_id_t { ARDUINO_EVENT_WIFI_STA_GOT_IP, ARDUINO_EVENT_WIFI_STA_DISCONNECTED };
struct arduino_event_info_t { struct { uint8_t reason = 0; } wifi_sta_disconnected; };
enum {
  WIFI_REASON_MIC_FAILURE = 14, WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT = 15,
  WIFI_REASON_NO_AP_FOUND = 201, WIFI_REASON_AUTH_FAIL = 202, WIFI_REASON_HANDSHAKE_TIMEOUT = 204,
  WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY = 210,
  WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD = 211, WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD = 212,
};
constexpr int WIFI_STA = 1;
constexpr int WIFI_AP_STA = 3;
constexpr int WIFI_SCAN_RUNNING = -1;
namespace Fake {
inline std::atomic<bool> ap{false};
inline bool apFail = false;
inline std::atomic<bool> noIP{false};
inline std::string wifiSSID = "host-test";
inline int scanState = -2;
inline std::vector<std::string> scanNames;
inline bool scanAsync = false;
inline std::atomic<bool> scanInProgress{false};
inline std::atomic<bool> scanStopFail{false};
}
constexpr int WL_CONNECTED = 3;
struct FakeWiFi {
  std::map<network_event_handle_t, std::function<void(arduino_event_id_t, arduino_event_info_t)>> handlers;
  std::atomic<bool> ipReady{false};
  network_event_handle_t onEvent(std::function<void(arduino_event_id_t, arduino_event_info_t)> handler) {
    std::lock_guard<std::recursive_mutex> lock(eventMutex);
    const auto id = ++lastHandle;
    handlers[id] = handler;
    return id;
  }
  void removeEvent(network_event_handle_t id) {
    std::lock_guard<std::recursive_mutex> lock(eventMutex);
    handlers.erase(id);
  }
  void emitEvent(arduino_event_id_t event, uint8_t reason = 0) {
    std::lock_guard<std::recursive_mutex> lock(eventMutex);
    arduino_event_info_t info;
    info.wifi_sta_disconnected.reason = reason;
    for (const auto& handler : handlers) handler.second(event, info);
  }
  void pollEvents() {
    std::lock_guard<std::recursive_mutex> lock(eventMutex);
    const bool ready = Fake::connected && !Fake::noIP;
    const bool previous = ipReady.exchange(ready);
    if (ready != previous) emitEvent(ready ? ARDUINO_EVENT_WIFI_STA_GOT_IP : ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  }
  void mode(int) {}
  void persistent(bool) {}
  void setSleep(bool) {}
  void setAutoReconnect(bool) {}
  void begin(const char* ssid, const char*, int32_t = 0) { Fake::wifiSSID = ssid; }
  bool disconnect(bool, bool) {
    std::lock_guard<std::recursive_mutex> lock(eventMutex);
    Fake::connected = false;
    ipReady = false;
    emitEvent(ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
    return true;
  }
  bool softAP(const char*, const char*) { Fake::ap = !Fake::apFail; return Fake::ap; }
  bool softAPdisconnect(bool off) { assert(!off); Fake::ap = false; return true; }
  int scanComplete() { return Fake::scanState; }
  int scanNetworks(bool async, bool) { Fake::scanAsync = async; Fake::scanInProgress = true; Fake::scanState = -1; return -1; }
  void scanDelete() { Fake::scanState = -2; }
  String SSID(int i) { return Fake::scanNames.at(i); }
  String SSID() { return Fake::wifiSSID; }
  int RSSI(int) { return -50; }
  int channel(int) { return 1; }
  String macAddress() { return "00:00:00:00:00:01"; }
  IPAddress localIP() { return IPAddress(Fake::noIP ? "0.0.0.0" : "192.0.2.2"); }
  IPAddress subnetMask() { return IPAddress("255.255.255.0"); }
  IPAddress softAPIP() { return IPAddress("192.168.4.1"); }
  int status() {
    std::unique_lock<std::mutex> lock(Fake::mutex);
    if (Fake::holdWiFi && std::this_thread::get_id() != Fake::controlThread) {
      Fake::workerInWiFi = true;
      Fake::changed.notify_all();
      Fake::changed.wait(lock, [] { return !Fake::holdWiFi; });
    }
    pollEvents();
    return Fake::connected ? WL_CONNECTED : 0;
  }
 private:
  std::recursive_mutex eventMutex;
  network_event_handle_t lastHandle = 0;
};
inline FakeWiFi WiFi;
