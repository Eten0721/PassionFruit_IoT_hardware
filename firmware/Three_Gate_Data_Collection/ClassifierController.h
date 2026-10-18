#pragma once

#include <Arduino.h>
#include <ESP32Servo.h>

#include "ClassificationSequence.h"
#include "GateController.h"

class ClassifierController {
 public:
  enum class State : uint8_t {
    kUninitialized,
    kBootHomeSettling,
    kIdleHome,
    kRunning,
    kPermanentInitializationError,
  };

  struct Result {
    bool available;
    bool success;
    int commandId;
    String classificationCode;
    String reason;

    Result()
        : available(false),
          success(false),
          commandId(0),
          classificationCode(""),
          reason("") {}
  };

  void begin(uint32_t currentTime, GateController& gates, bool home = true);
  // Only takes effect on the next home(); callers re-home after changing it.
  void setAngles(int homeAngle, const int (&binAngles)[FirmwareConfig::kSorterBinCount]);
  void abort();
  void home(uint32_t currentTime);
  void tick(uint32_t currentTime);
  bool start(
      int commandId,
      const String& classificationCode,
      uint32_t currentTime,
      String& reason);
  bool busy() const;
  bool available() const;
  bool hasResult() const;
  Result takeResult();

 private:
  Servo servo_;
  GateController* gates_ = nullptr;
  ClassificationSequence sequence_;
  State state_ = State::kUninitialized;
  uint32_t bootHomeStartedAt_ = 0;
  int homeAngle_ = FirmwareConfig::kSorterHomeAngle;
  int binAngles_[FirmwareConfig::kSorterBinCount] = {
      FirmwareConfig::kSorterBinAngles[0],
      FirmwareConfig::kSorterBinAngles[1],
      FirmwareConfig::kSorterBinAngles[2],
      FirmwareConfig::kSorterBinAngles[3]};
  String activeClassificationCode_;
  Result result_;

  void applyStep(const ClassificationSequence::Step& step);
  void finishOperation(
      bool success,
      int commandId,
      const char* reason);
};
