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

  const uint8_t index = stationIndex - 1;
  servos_[index].write(angle);
  angles_[index] = angle;
  return true;
}

void GateController::setHomeAngles(const int (&homeAngles)[FirmwareConfig::kGateCount]) {
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    homeAngles_[index] = homeAngles[index];
  }
}

void GateController::moveAllHome() {
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    servos_[index].write(homeAngles_[index]);
    angles_[index] = homeAngles_[index];
  }
}

bool GateController::atAngle(uint8_t stationIndex, int angle) const {
  if (stationIndex < 1 || stationIndex > FirmwareConfig::kGateCount) {
    return false;
  }
  return angles_[stationIndex - 1] == angle;
}

bool GateController::atHome(uint8_t stationIndex) const {
  if (stationIndex < 1 || stationIndex > FirmwareConfig::kGateCount) {
    return false;
  }
  return angles_[stationIndex - 1] == homeAngles_[stationIndex - 1];
}

bool GateController::atHome() const {
  for (uint8_t index = 1; index <= FirmwareConfig::kGateCount; index += 1) {
    if (!atHome(index)) {
      return false;
    }
  }
  return true;
}
