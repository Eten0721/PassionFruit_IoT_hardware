#pragma once

#include <Arduino.h>
#include <ESP32Servo.h>

class ClassifierController {
 public:
  enum class State : uint8_t {
    kUninitialized,
    kBootHomeSettling,
    kIdleHome,
    kHoldingClassificationPosition,
    kReturningHome,
    kHomeSettling,
    kErrorReturningHome,
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

  void begin(uint32_t currentTime);
  void tick(uint32_t currentTime);
  bool start(
      int commandId,
      const String& classificationCode,
      uint32_t currentTime,
      String& reason);
  bool busy() const;
  bool hasResult() const;
  Result takeResult();
  State state() const;

 private:
  enum class Classification : uint8_t {
    kInvalid,
    kHigh,
    kLow,
    kProcessing,
    kMedium,
  };

  Servo servo_;
  State state_ = State::kUninitialized;
  uint32_t phaseDeadlineAt_ = 0;
  uint32_t operationStartedAt_ = 0;
  int commandId_ = 0;
  String classificationCode_;
  String pendingFailureReason_;
  bool completingWithError_ = false;
  Result result_;

  Classification parseClassification(const String& code) const;
  int angleForClassification(Classification classification) const;
  void startReturnHome(uint32_t currentTime, bool withError, const String& reason);
  void finishOperation(bool success, const String& reason);
  static bool timeReached(uint32_t currentTime, uint32_t deadline);
};
