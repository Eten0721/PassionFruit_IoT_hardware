#pragma once
#include "Arduino.h"
class WiFiClientSecure {
 public:
  void setInsecure() {}
  void setTimeout(uint32_t) {}
  void setHandshakeTimeout(uint32_t) {}
  bool connected() { return true; }
  void stop() { std::lock_guard<std::mutex> lock(Fake::mutex); ++Fake::connectionStops; }
};
