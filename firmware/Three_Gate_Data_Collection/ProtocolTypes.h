#pragma once

#include <Arduino.h>

#include "Config.h"

struct TimingConfig {
  uint32_t revision;
  uint32_t firstStationSettleMS;
  uint32_t servoSettleMS;
  uint32_t fruitSettleMS;
  uint32_t finalGateReturnDelayMS;
  uint32_t idleCommandPollIntervalMS;
  uint32_t feederStopUS;
  uint32_t feederDriveUS;
  uint32_t feederMaxRunMS;
  int gateHomeAngles[FirmwareConfig::kGateCount];
  int sorterHomeAngle;
  int sorterBinAngles[FirmwareConfig::kSorterBinCount];

  TimingConfig()
      : revision(0),
        firstStationSettleMS(0),
        servoSettleMS(0),
        fruitSettleMS(0),
        finalGateReturnDelayMS(0),
        idleCommandPollIntervalMS(0),
        feederStopUS(0),
        feederDriveUS(0),
        feederMaxRunMS(0),
        gateHomeAngles{FirmwareConfig::kHomeAngle, FirmwareConfig::kHomeAngle, FirmwareConfig::kHomeAngle},
        sorterHomeAngle(FirmwareConfig::kSorterHomeAngle),
        sorterBinAngles{
            FirmwareConfig::kSorterBinAngles[0],
            FirmwareConfig::kSorterBinAngles[1],
            FirmwareConfig::kSorterBinAngles[2],
            FirmwareConfig::kSorterBinAngles[3]} {}
};

struct MotorCommand {
  String command;
  int commandId;
  int stationIndex;
  int homeAngle;
  int releaseAngle;
  bool autoTriggerEnabled;
  bool hasAutoTriggerEnabled;
  TimingConfig timing;
  bool hasTimingConfig;
  String serverStatus;
  String classificationCode;
  String feedContext;

  MotorCommand()
      : command("none"),
        commandId(0),
        stationIndex(0),
        homeAngle(0),
        releaseAngle(0),
        autoTriggerEnabled(false),
        hasAutoTriggerEnabled(false),
        timing(),
        hasTimingConfig(false),
        serverStatus(""),
        classificationCode(""),
        feedContext("") {}
};

struct HttpResult {
  uint32_t sentAt = 0;
  uint32_t generation = 0;
  uint32_t networkRevision = 0;
  String serverRunId;
  String bootId;
  int statusCode;
  uint32_t elapsedMS;
  String body;

  HttpResult() : statusCode(0), elapsedMS(0), body("") {}

  bool isSuccess() const {
    return statusCode >= 200 && statusCode < 300;
  }

};
