#pragma once
#include "Arduino.h"
#include <cstdio>

class IPAddress {
 public:
  IPAddress() = default;
  IPAddress(const char* value) { fromString(String(value)); }
  bool fromString(const String& value) {
    unsigned int a, b, c, d;
    char extra;
    if (std::sscanf(value.c_str(), "%u.%u.%u.%u%c", &a, &b, &c, &d, &extra) != 4 ||
        a > 255 || b > 255 || c > 255 || d > 255) return false;
    bytes_[0] = a; bytes_[1] = b; bytes_[2] = c; bytes_[3] = d;
    return true;
  }
  String toString() const {
    return String(static_cast<unsigned int>(bytes_[0])) + "." + String(static_cast<unsigned int>(bytes_[1])) + "." + String(static_cast<unsigned int>(bytes_[2])) + "." + String(static_cast<unsigned int>(bytes_[3]));
  }
  uint8_t operator[](int index) const { return bytes_[index]; }
  operator uint32_t() const {
    return bytes_[0] | (uint32_t(bytes_[1]) << 8) | (uint32_t(bytes_[2]) << 16) | (uint32_t(bytes_[3]) << 24);
  }
 private:
  uint8_t bytes_[4] = {};
};
