#pragma once
#include "Arduino.h"
#include "IPAddress.h"
constexpr int WIFI_STA = 1;
constexpr int WL_CONNECTED = 3;
struct FakeWiFi {
  void mode(int) {}
  void persistent(bool) {}
  void setSleep(bool) {}
  void setAutoReconnect(bool) {}
  void begin(const char*, const char*) {}
  String macAddress() { return "00:00:00:00:00:01"; }
  IPAddress localIP() { return IPAddress("192.0.2.2"); }
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
