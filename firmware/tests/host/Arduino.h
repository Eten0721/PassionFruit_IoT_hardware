#pragma once

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <sstream>
#include <string>
#include <type_traits>

#include "FakePlatform.h"

constexpr int HEX = 16;
constexpr int OUTPUT = 1;
constexpr int INPUT = 0;
constexpr int LOW = 0;
constexpr int HIGH = 1;

class String {
 public:
  String() = default;
  String(const char* value) : value_(value) {}
  String(const std::string& value) : value_(value) {}
  template <typename T, typename = typename std::enable_if<std::is_arithmetic<T>::value>::type>
  String(T value, int base = 10) {
    std::ostringstream stream;
    if (base == HEX) stream << std::hex;
    stream << value;
    value_ = stream.str();
  }
  unsigned int length() const { return value_.size(); }
  const char* c_str() const { return value_.c_str(); }
  char charAt(unsigned int index) const { return index < value_.size() ? value_[index] : 0; }
  int indexOf(const String& needle, unsigned int from = 0) const {
    const auto index = value_.find(needle.value_, from);
    return index == std::string::npos ? -1 : static_cast<int>(index);
  }
  int indexOf(char needle, unsigned int from = 0) const {
    return indexOf(String(std::string(1, needle)), from);
  }
  String substring(unsigned int from, unsigned int to) const { return value_.substr(from, to - from); }
  String substring(unsigned int from) const { return value_.substr(from); }
  bool startsWith(const String& prefix, unsigned int from = 0) const {
    return value_.compare(from, prefix.length(), prefix.value_) == 0;
  }
  long toInt() const { return std::strtol(value_.c_str(), nullptr, 10); }
  void reserve(unsigned int size) { value_.reserve(size); }
  void trim() {
    const auto first = value_.find_first_not_of(" \t\r\n");
    value_ = first == std::string::npos ? "" : value_.substr(first, value_.find_last_not_of(" \t\r\n") - first + 1);
  }
  void toLowerCase() {
    std::transform(value_.begin(), value_.end(), value_.begin(), [](unsigned char c) { return std::tolower(c); });
  }
  String& operator+=(const String& value) { value_ += value.value_; return *this; }
  String& operator+=(char value) { value_ += value; return *this; }
  friend String operator+(String left, const String& right) { return left += right; }
  friend bool operator==(const String& left, const String& right) { return left.value_ == right.value_; }
  friend bool operator!=(const String& left, const String& right) { return !(left == right); }

 private:
  std::string value_;
};

struct FakeSerial {
  void begin(int) {}
  template <typename T> void print(const T&) {}
  template <typename T> void println(const T&) {}
  void println() {}
};
inline FakeSerial Serial;
inline uint32_t millis() { return Fake::now.load(); }
inline uint32_t micros() { return millis() * 1000; }
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) { Fake::assertControlThread(); }
inline void delayMicroseconds(int) {}
inline unsigned long pulseIn(int, int, unsigned long) {
  Fake::assertControlThread();
  ++Fake::sensorReads;
  Fake::now += Fake::sensorLatencyMS;
  return Fake::echoUS;
}
