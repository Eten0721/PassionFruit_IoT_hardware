#pragma once
#include <Arduino.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#if defined(CORE_DEBUG_LEVEL) && CORE_DEBUG_LEVEL >= 5
#error "Verbose WebServer logging exposes submitted Wi-Fi passwords; use a lower debug level."
#endif

// Network-worker ownership keeps Wi-Fi, HTTP and flash I/O out of the control tick.
class WifiSetup {
 public:
  ~WifiSetup();
  void begin();
  void tick(bool requested);
  bool active() const { return active_; }
  bool configured() const { return record_.ssid[0] != 0; }
  void reconnect() const;
  String manualOrigin() const { return record_.origin; }
  static bool validOrigin(const String& origin, bool sameSubnet);
 private:
  struct Record {
    uint32_t schema = 1;
    char ssid[33] = {};
    char password[65] = {};
    char origin[48] = {};
  } record_, candidate_;
  Preferences preferences_;
  WebServer server_{80};
  bool active_ = false;
  bool connecting_ = false;
  bool saved_ = false;
  bool exitRequested_ = false;
  bool storageReady_ = false;
  uint32_t phaseStartedAt_ = 0;
  String reason_;
  void open();
  void saveRequest();
  void exitRequest();
  bool savedConnectionReady() const;
  void scanRequest();
  void statusRequest();
  void fail(const char* reason);
  static bool validConfig(const String& ssid, const String& password);
};
