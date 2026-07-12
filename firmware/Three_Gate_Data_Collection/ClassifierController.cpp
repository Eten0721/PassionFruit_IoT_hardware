#include "ClassifierController.h"

#include "Config.h"

void ClassifierController::begin(uint32_t currentTime) {
  state_ = State::kUninitialized;
  servo_.attach(FirmwareConfig::kClassifierPin);
  if (!servo_.attached()) {
    state_ = State::kPermanentInitializationError;
    Serial.println("Classifier attach failed on GPIO25.");
    return;
  }

  servo_.write(FirmwareConfig::kClassifierHomeAngle);
  state_ = State::kBootHomeSettling;
  phaseDeadlineAt_ = currentTime + FirmwareConfig::kClassifierHomeSettleMS;
  Serial.println("Classifier returning to HOME 85 degrees.");
}

void ClassifierController::tick(uint32_t currentTime) {
  if (
      busy() &&
      operationStartedAt_ != 0 &&
      currentTime - operationStartedAt_ > FirmwareConfig::kClassifierTimeoutMS) {
    startReturnHome(currentTime, true, "classifier_timeout");
  }

  if (!timeReached(currentTime, phaseDeadlineAt_)) {
    return;
  }

  switch (state_) {
    case State::kBootHomeSettling:
      state_ = State::kIdleHome;
      return;

    case State::kHoldingClassificationPosition:
      startReturnHome(currentTime, false, "");
      return;

    case State::kReturningHome:
      state_ = State::kHomeSettling;
      phaseDeadlineAt_ = currentTime + FirmwareConfig::kClassifierHomeSettleMS;
      return;

    case State::kErrorReturningHome:
      state_ = State::kHomeSettling;
      phaseDeadlineAt_ = currentTime + FirmwareConfig::kClassifierHomeSettleMS;
      return;

    case State::kHomeSettling:
      state_ = State::kIdleHome;
      finishOperation(!completingWithError_, pendingFailureReason_);
      return;

    case State::kUninitialized:
    case State::kIdleHome:
    case State::kPermanentInitializationError:
      return;
  }
}

bool ClassifierController::start(
    int commandId,
    const String& classificationCode,
    uint32_t currentTime,
    String& reason) {
  reason = "";
  if (state_ == State::kPermanentInitializationError || !servo_.attached()) {
    reason = "classifier_attach_failed";
    return false;
  }
  if (state_ != State::kIdleHome || result_.available) {
    reason = "classifier_busy";
    return false;
  }

  commandId_ = commandId;
  classificationCode_ = classificationCode;
  operationStartedAt_ = currentTime;
  completingWithError_ = false;
  pendingFailureReason_ = "";

  const Classification classification = parseClassification(classificationCode);
  if (classification == Classification::kInvalid) {
    startReturnHome(currentTime, true, "invalid_classification_code");
    return true;
  }

  servo_.write(angleForClassification(classification));
  state_ = State::kHoldingClassificationPosition;
  phaseDeadlineAt_ = currentTime + FirmwareConfig::kClassifierHoldMS;
  return true;
}

bool ClassifierController::busy() const {
  return state_ == State::kHoldingClassificationPosition ||
         state_ == State::kReturningHome ||
         state_ == State::kHomeSettling ||
         state_ == State::kErrorReturningHome;
}

bool ClassifierController::hasResult() const {
  return result_.available;
}

ClassifierController::Result ClassifierController::takeResult() {
  Result result = result_;
  result_ = Result();
  return result;
}

ClassifierController::State ClassifierController::state() const {
  return state_;
}

ClassifierController::Classification ClassifierController::parseClassification(
    const String& code) const {
  if (code == "high_medium") {
    return Classification::kHighMedium;
  }
  if (code == "low") {
    return Classification::kLow;
  }
  if (code == "processing") {
    return Classification::kProcessing;
  }
  if (code == "discard") {
    return Classification::kDiscard;
  }
  return Classification::kInvalid;
}

int ClassifierController::angleForClassification(
    Classification classification) const {
  switch (classification) {
    case Classification::kHighMedium:
      return FirmwareConfig::kClassifierHighMediumAngle;
    case Classification::kLow:
      return FirmwareConfig::kClassifierLowAngle;
    case Classification::kProcessing:
      return FirmwareConfig::kClassifierProcessingAngle;
    case Classification::kDiscard:
      return FirmwareConfig::kClassifierDiscardAngle;
    case Classification::kInvalid:
      return FirmwareConfig::kClassifierHomeAngle;
  }
  return FirmwareConfig::kClassifierHomeAngle;
}

void ClassifierController::startReturnHome(
    uint32_t currentTime,
    bool withError,
    const String& reason) {
  if (!servo_.attached()) {
    state_ = State::kPermanentInitializationError;
    finishOperation(false, "classifier_attach_failed");
    return;
  }
  servo_.write(FirmwareConfig::kClassifierHomeAngle);
  completingWithError_ = withError;
  pendingFailureReason_ = reason;
  if (reason == "classifier_timeout") {
    operationStartedAt_ = 0;
  }
  state_ = withError ? State::kErrorReturningHome : State::kReturningHome;
  phaseDeadlineAt_ = currentTime;
}

void ClassifierController::finishOperation(bool success, const String& reason) {
  result_.available = true;
  result_.success = success;
  result_.commandId = commandId_;
  result_.classificationCode = classificationCode_;
  result_.reason = success ? "classification_sorter_completed" : reason;
  commandId_ = 0;
  classificationCode_ = "";
  operationStartedAt_ = 0;
  completingWithError_ = false;
  pendingFailureReason_ = "";
}

bool ClassifierController::timeReached(uint32_t currentTime, uint32_t deadline) {
  return static_cast<int32_t>(currentTime - deadline) >= 0;
}
