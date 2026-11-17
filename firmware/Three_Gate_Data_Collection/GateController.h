#pragma once

#include <Arduino.h>
#include <ESP32Servo.h>

#include "Config.h"

class GateController {
 public:
  void begin();
  // Targets farther than one step are approached one step per interval from tick().
  bool moveGate(uint8_t stationIndex, int angle);
  // Only takes effect on the next moveAllHome(); callers re-home after changing it.
  void setHomeAngles(const int (&homeAngles)[FirmwareConfig::kGateCount]);
  // Only takes effect on the next move; a step of kReleaseAngle or more moves in one write.
  void setStepProfile(int stepAngle, uint32_t stepIntervalMS);
  void moveAllHome();
  // Advances stepped moves. Returns the ms spent moving since the previous tick so
  // callers can start their settle waits once the gate has arrived.
  uint32_t tick(uint32_t currentTime);
  bool moving() const;
  // Total ms spent in stepped moves; wraps like millis().
  uint32_t motionMS() const;
  // True only once the gate has arrived.
  bool atAngle(uint8_t stationIndex, int angle) const;
  bool atHome(uint8_t stationIndex) const;
  bool atHome() const;

 private:
  Servo servos_[FirmwareConfig::kGateCount];
  // -1 until the first write: the boot position is unknown, so that move cannot be stepped.
  int angles_[FirmwareConfig::kGateCount] = {-1, -1, -1};
  int targetAngles_[FirmwareConfig::kGateCount] = {-1, -1, -1};
  int homeAngles_[FirmwareConfig::kGateCount] = {
      FirmwareConfig::kHomeAngle, FirmwareConfig::kHomeAngle, FirmwareConfig::kHomeAngle};
  int stepAngle_ = FirmwareConfig::kGateStepAngle;
  uint32_t stepIntervalMS_ = FirmwareConfig::kGateStepIntervalMS;
  uint32_t lastStepAt_ = 0;
  uint32_t lastTickAt_ = 0;
  uint32_t motionMS_ = 0;

  void setTarget(uint8_t index, int angle);
  void startMotion(bool wasMoving);
  void stepToward(uint8_t index);
};
