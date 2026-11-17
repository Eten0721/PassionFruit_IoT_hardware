#include "GateController.h"

#include "Config.h"

void GateController::begin() {
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    servos_[index].attach(FirmwareConfig::kGatePins[index]);
  }
}

bool GateController::moveGate(uint8_t stationIndex, int angle) {
  if (stationIndex < 1 || stationIndex > FirmwareConfig::kGateCount) {
    return false;
  }

  const bool wasMoving = moving();
  setTarget(stationIndex - 1, angle);
  startMotion(wasMoving);
  return true;
}

void GateController::setHomeAngles(const int (&homeAngles)[FirmwareConfig::kGateCount]) {
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    homeAngles_[index] = homeAngles[index];
  }
}

void GateController::setStepProfile(int stepAngle, uint32_t stepIntervalMS) {
  stepAngle_ = stepAngle;
  stepIntervalMS_ = stepIntervalMS;
}

void GateController::moveAllHome() {
  const bool wasMoving = moving();
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    setTarget(index, homeAngles_[index]);
  }
  startMotion(wasMoving);
}

uint32_t GateController::tick(uint32_t currentTime) {
  if (!moving()) {
    lastTickAt_ = currentTime;
    return 0;
  }

  const uint32_t elapsedMS = currentTime - lastTickAt_;
  lastTickAt_ = currentTime;
  motionMS_ += elapsedMS;
  if (currentTime - lastStepAt_ < stepIntervalMS_) {
    return elapsedMS;
  }
  lastStepAt_ = currentTime;
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    stepToward(index);
  }
  return elapsedMS;
}

bool GateController::moving() const {
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    if (angles_[index] != targetAngles_[index]) {
      return true;
    }
  }
  return false;
}

uint32_t GateController::motionMS() const {
  return motionMS_;
}

bool GateController::atAngle(uint8_t stationIndex, int angle) const {
  if (stationIndex < 1 || stationIndex > FirmwareConfig::kGateCount) {
    return false;
  }
  const uint8_t index = stationIndex - 1;
  return angles_[index] == angle && targetAngles_[index] == angle;
}

bool GateController::atHome(uint8_t stationIndex) const {
  if (stationIndex < 1 || stationIndex > FirmwareConfig::kGateCount) {
    return false;
  }
  return atAngle(stationIndex, homeAngles_[stationIndex - 1]);
}

bool GateController::atHome() const {
  for (uint8_t index = 1; index <= FirmwareConfig::kGateCount; index += 1) {
    if (!atHome(index)) {
      return false;
    }
  }
  return true;
}

void GateController::setTarget(uint8_t index, int angle) {
  targetAngles_[index] = angle;
  if (angles_[index] >= 0 && angles_[index] != angle) {
    return;
  }
  // An unknown boot position cannot be stepped; a held position is re-sent as before.
  servos_[index].write(angle);
  angles_[index] = angle;
}

void GateController::startMotion(bool wasMoving) {
  // A move commanded mid-motion joins the running cadence.
  if (wasMoving || !moving()) {
    return;
  }
  const uint32_t currentTime = millis();
  lastStepAt_ = currentTime;
  lastTickAt_ = currentTime;
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    stepToward(index);
  }
}

void GateController::stepToward(uint8_t index) {
  const int remaining = targetAngles_[index] - angles_[index];
  if (remaining == 0) {
    return;
  }
  const int distance = remaining > 0 ? remaining : -remaining;
  const int step = distance < stepAngle_ ? distance : stepAngle_;
  angles_[index] += remaining > 0 ? step : -step;
  servos_[index].write(angles_[index]);
}
