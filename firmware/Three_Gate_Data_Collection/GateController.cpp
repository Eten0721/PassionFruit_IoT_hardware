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

void GateController::moveAll(int angle) {
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    servos_[index].write(angle);
    angles_[index] = angle;
  }
}

bool GateController::atAngle(uint8_t stationIndex, int angle) const {
  if (stationIndex < 1 || stationIndex > FirmwareConfig::kGateCount) {
    return false;
  }
  return angles_[stationIndex - 1] == angle;
}

bool GateController::allAtAngle(int angle) const {
  for (uint8_t index = 0; index < FirmwareConfig::kGateCount; index += 1) {
    if (angles_[index] != angle) {
      return false;
    }
  }
  return true;
}

bool GateController::atHome() const {
  return allAtAngle(FirmwareConfig::kHomeAngle);
}
