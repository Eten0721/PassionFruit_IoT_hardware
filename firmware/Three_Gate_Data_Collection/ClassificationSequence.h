#pragma once

#include <stdint.h>

class ClassificationSequence {
 public:
  enum class Action : uint8_t {
    kNone,
    kMoveSorterToTarget,
    kReleaseGate3,
    kHomeAll,
    kCompleted,
    kFailed,
    kIgnored,
  };

  struct Step {
    Action action;
    int angle;
    int commandId;
    const char* classificationCode;
    const char* reason;

    Step(
        Action stepAction = Action::kNone,
        int stepAngle = 0,
        int stepCommandId = 0,
        const char* stepClassificationCode = "",
        const char* stepReason = "")
        : action(stepAction),
          angle(stepAngle),
          commandId(stepCommandId),
          classificationCode(stepClassificationCode),
          reason(stepReason) {}
  };

  static constexpr int kGateHomeAngle = 0;
  static constexpr int kGateReleaseAngle = 90;
  static constexpr int kSorterHomeAngle = 85;
  static constexpr uint32_t kSorterPositionSettleMS = 500UL;
  static constexpr uint32_t kFruitDropHoldMS = 1000UL;
  static constexpr uint32_t kJointHomeSettleMS = 500UL;
  static constexpr uint32_t kOperationTimeoutMS = 5000UL;

  Step start(int commandId, const char* classificationCode, uint32_t currentTime);
  Step tick(uint32_t currentTime);
  bool busy() const;
  static bool bootHomeReady(uint32_t currentTime, uint32_t bootHomeStartedAt);

 private:
  enum class Phase : uint8_t {
    kIdle,
    kPositioningSorter,
    kHoldingAfterGateRelease,
    kHoming,
    kFailureHoming,
  };

  Phase phase_ = Phase::kIdle;
  uint32_t phaseDeadlineAt_ = 0;
  uint32_t operationStartedAt_ = 0;
  int commandId_ = 0;
  int lastTerminalCommandId_ = 0;
  char classificationCode_[16] = {0};
  const char* failureReason_ = "";

  int angleForClassification(const char* classificationCode) const;
  Step terminal(Action action, const char* reason);
  static bool timeReached(uint32_t currentTime, uint32_t deadline);
};
