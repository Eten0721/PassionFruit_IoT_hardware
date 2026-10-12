#pragma once
#include "Arduino.h"
#include "IPAddress.h"
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
  void mode(int) {}
  void persistent(bool) {}
  void setSleep(bool) {}
  void setAutoReconnect(bool) {}
  void begin(const char* ssid, const char*) { Fake::wifiSSID = ssid; }
  bool disconnect(bool, bool) { Fake::connected = false; return true; }
  bool softAP(const char*, const char*) { Fake::ap = !Fake::apFail; return Fake::ap; }
  bool softAPdisconnect(bool off) { assert(!off); Fake::ap = false; return true; }
  int scanComplete() { return Fake::scanState; }
  int scanNetworks(bool async, bool) { Fake::scanAsync = async; Fake::scanInProgress = true; Fake::scanState = -1; return -1; }
  void scanDelete() { Fake::scanState = -2; }
  String SSID(int i) { return Fake::scanNames.at(i); }
  String SSID() { return Fake::wifiSSID; }
  String macAddress() { return "00:00:00:00:00:01"; }
  IPAddress localIP() { return IPAddress(Fake::noIP ? "0.0.0.0" : "192.0.2.2"); }
  IPAddress subnetMask() { return IPAddress("255.255.255.0"); }
  int status() const {
    std::unique_lock<std::mutex> lock(Fake::mutex);
    if (Fake::holdWiFi && std::this_thread::get_id() != Fake::controlThread) {
      Fake::workerInWiFi = true;
      Fake::changed.notify_all();
      Fake::changed.wait(lock, [] { return !Fake::holdWiFi; });
    }
    return Fake::connected ? WL_CONNECTED : 0;
  }
};
inline FakeWiFi WiFi;
