#include "ClassificationSequence.h"

#include <string.h>

ClassificationSequence::Step ClassificationSequence::start(
    int commandId,
    const char* classificationCode,
    uint32_t currentTime,
    const int (&binAngles)[FirmwareConfig::kSorterBinCount]) {
  if (commandId <= 0) {
    return Step(Action::kFailed, 0, commandId, "", "invalid_command_id");
  }
  if (commandId == commandId_ || commandId == lastTerminalCommandId_) {
    return Step(Action::kIgnored, 0, commandId, classificationCode, "duplicate_command_id");
  }
  if (busy()) {
    return Step(Action::kFailed, 0, commandId, classificationCode, "classification_sequence_busy");
  }

  const int bin = binForClassification(classificationCode);
  if (bin < 0) {
    lastTerminalCommandId_ = commandId;
    return Step(Action::kFailed, 0, commandId, classificationCode, "invalid_classification_code");
  }

  commandId_ = commandId;
  strncpy(classificationCode_, classificationCode, sizeof(classificationCode_) - 1);
  classificationCode_[sizeof(classificationCode_) - 1] = '\0';
  operationStartedAt_ = currentTime;
  phase_ = Phase::kPositioningSorter;
  phaseDeadlineAt_ = currentTime + kSorterPositionSettleMS;
  return Step(Action::kMoveSorterToTarget, binAngles[bin], commandId_, classificationCode_);
}

ClassificationSequence::Step ClassificationSequence::tick(uint32_t currentTime) {
  if (!busy()) {
    return Step();
  }
  if (
      phase_ != Phase::kFailureHoming &&
      timeReached(currentTime, operationStartedAt_ + kOperationTimeoutMS)) {
    phase_ = Phase::kFailureHoming;
    phaseDeadlineAt_ = currentTime + kJointHomeSettleMS;
    failureReason_ = "classifier_timeout";
    return Step(Action::kHomeAll, 0, commandId_, classificationCode_, failureReason_);
  }
  if (!timeReached(currentTime, phaseDeadlineAt_)) {
    return Step();
  }

  switch (phase_) {
    case Phase::kPositioningSorter:
      phase_ = Phase::kHoldingAfterGateRelease;
      phaseDeadlineAt_ = currentTime + kFruitDropHoldMS;
      return Step(Action::kReleaseGate3, kGateReleaseAngle, commandId_, classificationCode_);

    case Phase::kHoldingAfterGateRelease:
      phase_ = Phase::kHoming;
      phaseDeadlineAt_ = currentTime + kJointHomeSettleMS;
      return Step(Action::kHomeAll, 0, commandId_, classificationCode_);

    case Phase::kHoming:
      return terminal(Action::kCompleted, "classification_sorter_completed");

    case Phase::kFailureHoming:
      return terminal(Action::kFailed, failureReason_);

    case Phase::kIdle:
      return Step();
  }
  return Step();
}

bool ClassificationSequence::busy() const {
  return phase_ != Phase::kIdle;
}

bool ClassificationSequence::bootHomeReady(
    uint32_t currentTime,
    uint32_t bootHomeStartedAt) {
  return timeReached(currentTime, bootHomeStartedAt + kJointHomeSettleMS);
}

// Index into the bin angle order: high, medium, low, processing.
int ClassificationSequence::binForClassification(const char* classificationCode) {
  if (classificationCode == nullptr) {
    return -1;
  }
  if (strcmp(classificationCode, "high") == 0 || strcmp(classificationCode, "high_medium") == 0) {
    return 0;
  }
  if (strcmp(classificationCode, "medium") == 0 || strcmp(classificationCode, "discard") == 0) {
    return 1;
  }
  if (strcmp(classificationCode, "low") == 0) {
    return 2;
  }
  if (strcmp(classificationCode, "processing") == 0) {
    return 3;
  }
  return -1;
}

ClassificationSequence::Step ClassificationSequence::terminal(
    Action action,
    const char* reason) {
  const int completedCommandId = commandId_;
  lastTerminalCommandId_ = commandId_;
  commandId_ = 0;
  operationStartedAt_ = 0;
  phaseDeadlineAt_ = 0;
  phase_ = Phase::kIdle;
  failureReason_ = "";
  return Step(action, 0, completedCommandId, classificationCode_, reason);
}

bool ClassificationSequence::timeReached(uint32_t currentTime, uint32_t deadline) {
  return static_cast<int32_t>(currentTime - deadline) >= 0;
}
