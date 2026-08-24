#include "ClassifierController.h"

#include "Config.h"

void ClassifierController::begin(uint32_t currentTime, GateController& gates) {
  gates_ = &gates;
  state_ = State::kUninitialized;
  servo_.attach(FirmwareConfig::kClassifierPin);
  if (!servo_.attached()) {
    state_ = State::kPermanentInitializationError;
    Serial.println("Classifier attach failed on GPIO25.");
    return;
  }

  servo_.write(ClassificationSequence::kSorterHomeAngle);
  state_ = State::kBootHomeSettling;
  bootHomeStartedAt_ = currentTime;
  Serial.println("Classifier returning to HOME 85 degrees.");
}

void ClassifierController::tick(uint32_t currentTime) {
  if (
      state_ == State::kBootHomeSettling &&
      ClassificationSequence::bootHomeReady(currentTime, bootHomeStartedAt_)) {
    state_ = State::kIdleHome;
    return;
  }
  if (state_ != State::kRunning) {
    return;
  }
  applyStep(sequence_.tick(currentTime));
}

bool ClassifierController::start(
    int commandId,
    const String& classificationCode,
    uint32_t currentTime,
    String& reason) {
  reason = "";
  if (
      state_ == State::kPermanentInitializationError ||
      !servo_.attached() ||
      gates_ == nullptr) {
    reason = "classifier_attach_failed";
    return false;
  }
  if (state_ != State::kIdleHome || result_.available) {
    reason = "classifier_busy";
    return false;
  }

  const ClassificationSequence::Step step = sequence_.start(
      commandId,
      classificationCode.c_str(),
      currentTime);
  if (step.action == ClassificationSequence::Action::kIgnored) {
    reason = step.reason;
    return false;
  }
  if (
      step.action == ClassificationSequence::Action::kFailed &&
      String(step.reason) == "classification_sequence_busy") {
    reason = step.reason;
    return false;
  }

  activeClassificationCode_ = classificationCode;
  state_ = step.action == ClassificationSequence::Action::kFailed
      ? State::kIdleHome
      : State::kRunning;
  applyStep(step);
  return true;
}

bool ClassifierController::busy() const {
  return state_ == State::kBootHomeSettling || state_ == State::kRunning;
}

bool ClassifierController::available() const {
  return state_ == State::kIdleHome || state_ == State::kRunning;
}

bool ClassifierController::hasResult() const {
  return result_.available;
}

ClassifierController::Result ClassifierController::takeResult() {
  Result result = result_;
  result_ = Result();
  return result;
}

void ClassifierController::applyStep(
    const ClassificationSequence::Step& step) {
  switch (step.action) {
    case ClassificationSequence::Action::kNone:
    case ClassificationSequence::Action::kIgnored:
      return;

    case ClassificationSequence::Action::kMoveSorterToTarget:
      servo_.write(step.angle);
      return;

    case ClassificationSequence::Action::kReleaseGate3:
      if (!gates_->moveGate(3, ClassificationSequence::kGateReleaseAngle)) {
        finishOperation(false, step.commandId, "gate3_release_failed");
      }
      return;

    case ClassificationSequence::Action::kHomeAll:
      gates_->moveAll(ClassificationSequence::kGateHomeAngle);
      servo_.write(ClassificationSequence::kSorterHomeAngle);
      return;

    case ClassificationSequence::Action::kCompleted:
      finishOperation(true, step.commandId, step.reason);
      return;

    case ClassificationSequence::Action::kFailed:
      finishOperation(false, step.commandId, step.reason);
      return;
  }
}

void ClassifierController::finishOperation(
    bool success,
    int commandId,
    const char* reason) {
  state_ = State::kIdleHome;
  result_.available = true;
  result_.success = success;
  result_.commandId = commandId;
  result_.classificationCode = activeClassificationCode_;
  result_.reason = success ? "classification_sorter_completed" : String(reason);
  activeClassificationCode_ = "";
}
