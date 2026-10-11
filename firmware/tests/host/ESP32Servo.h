#pragma once
#include "Arduino.h"
class Servo {
 public:
  int attach(int pin, int = 0, int = 0) { pin_ = pin; return 1; }
  bool attached() const { return pin_ >= 0; }
  void write(int value) { Fake::assertControlThread(); Fake::outputs.push_back({millis(), pin_, value}); }
  void writeMicroseconds(int value) { write(value); }
 private:
  int pin_ = -1;
};
