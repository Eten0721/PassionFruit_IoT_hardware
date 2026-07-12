#pragma once

#include <Arduino.h>

struct TimingConfig {
  uint32_t revision;
  uint32_t firstStationSettleMS;
  uint32_t servoSettleMS;
  uint32_t fruitSettleMS;
  uint32_t finalGateReturnDelayMS;

  TimingConfig()
      : revision(0),
        firstStationSettleMS(0),
        servoSettleMS(0),
        fruitSettleMS(0),
        finalGateReturnDelayMS(0) {}
};

struct MotorCommand {
  String command;
  int commandId;
  int stationIndex;
  int homeAngle;
  int releaseAngle;
  int servoSettleMS;
  int fruitSettleMS;
  bool autoTriggerEnabled;
  bool hasAutoTriggerEnabled;
  TimingConfig timing;
  bool hasTimingConfig;
  String serverStatus;
  String classificationCode;

  MotorCommand()
      : command("none"),
        commandId(0),
        stationIndex(0),
        homeAngle(0),
        releaseAngle(0),
        servoSettleMS(0),
        fruitSettleMS(0),
        autoTriggerEnabled(false),
        hasAutoTriggerEnabled(false),
        timing(),
        hasTimingConfig(false),
        serverStatus(""),
        classificationCode("") {}
};

struct HttpResult {
  int statusCode;
  uint32_t elapsedMS;
  String body;
  bool connectionClosed;

  HttpResult()
      : statusCode(0), elapsedMS(0), body(""), connectionClosed(false) {}

  bool isSuccess() const {
    return statusCode >= 200 && statusCode < 300;
  }

  bool isTransportFailure() const {
    return statusCode <= 0;
  }
};
