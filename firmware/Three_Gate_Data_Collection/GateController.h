#pragma once

#include <Arduino.h>
#include <ESP32Servo.h>

#include "Config.h"

class GateController {
 public:
  void begin();
  bool moveGate(uint8_t stationIndex, int angle);
  void moveAll(int angle);
  bool allAtAngle(int angle) const;
  bool atHome() const;

 private:
  Servo servos_[FirmwareConfig::kGateCount];
  int angles_[FirmwareConfig::kGateCount] = {-1, -1, -1};
};
