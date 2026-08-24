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

  void begin(uint32_t currentTime, GateController& gates);
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
  String activeClassificationCode_;
  Result result_;

  void applyStep(const ClassificationSequence::Step& step);
  void finishOperation(
      bool success,
      int commandId,
      const char* reason);
};
