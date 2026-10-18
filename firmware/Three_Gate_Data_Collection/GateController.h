#pragma once

#include <Arduino.h>
#include <ESP32Servo.h>

#include "Config.h"

class GateController {
 public:
  void begin();
  bool moveGate(uint8_t stationIndex, int angle);
  // Only takes effect on the next moveAllHome(); callers re-home after changing it.
  void setHomeAngles(const int (&homeAngles)[FirmwareConfig::kGateCount]);
  void moveAllHome();
  bool atAngle(uint8_t stationIndex, int angle) const;
  bool atHome(uint8_t stationIndex) const;
  bool atHome() const;

 private:
  Servo servos_[FirmwareConfig::kGateCount];
  int angles_[FirmwareConfig::kGateCount] = {-1, -1, -1};
  int homeAngles_[FirmwareConfig::kGateCount] = {
      FirmwareConfig::kHomeAngle, FirmwareConfig::kHomeAngle, FirmwareConfig::kHomeAngle};
};
