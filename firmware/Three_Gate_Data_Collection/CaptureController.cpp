#include "CaptureController.h"

#include <Esp.h>

#include "Config.h"

void CaptureController::begin() {
  Serial.begin(115200);

  sensor_.begin();
  gates_.begin();
  gates_.moveAll(FirmwareConfig::kHomeAngle);
  feeder_.attach(FirmwareConfig::kFeederPin, 1000, 2000);
  feeder_.writeMicroseconds(FirmwareConfig::kFeederStopUS);

  const uint32_t currentTime = millis();
  classifier_.begin(currentTime);
  appliedTiming_ = defaultTimingConfig();
  motionPhase_ = MotionPhase::kBootHomeSettling;
  phaseDeadlineAt_ = currentTime + appliedTiming_.servoSettleMS;
  lastCommandPollAt_ = currentTime - FirmwareConfig::kIdleCommandPollIntervalMS;

  Serial.println();
  Serial.println("=== Three Gate Data Collection Firmware ===");
  Serial.println("All gates are moving to HOME_ANGLE; controller is non-blocking.");
  Serial.print("HC-SR04 pulse timeout: ");
  Serial.print(FirmwareConfig::kEchoPulseTimeoutUS);
  Serial.println(" us");
  Serial.print("Automatic station-1 fast path: ");
  Serial.println(
      FirmwareConfig::kEnableAutoStation1FastPath ? "enabled" : "disabled");

  api_.begin();
}

void CaptureController::tick() {
  const uint32_t currentTime = millis();
  api_.ensureWiFi(currentTime);

  classifier_.tick(currentTime);
  collectClassifierResult();
  advanceMotion(currentTime);
  if (applyPendingTimingConfig(currentTime)) {
    return;
  }
  handleSensor(currentTime);
  checkStartSequenceWaitTimeout(currentTime);

  if (flushPendingReport(currentTime)) {
    return;
  }
  if (processAutoTrigger(currentTime)) {
    return;
  }
  pollCommand(currentTime);
}

void CaptureController::advanceMotion(uint32_t currentTime) {
  if (!timeReached(currentTime, phaseDeadlineAt_)) {
    return;
  }

  switch (motionPhase_) {
    case MotionPhase::kBootHomeSettling:
      motionPhase_ = MotionPhase::kIdle;
      printTiming("gates_home_settled");
      return;

    case MotionPhase::kFeederDriving:
      feeder_.writeMicroseconds(activeTiming_.feederStopUS);
      lastFeedCommandId_ = activeCommand_.commandId;
      motionPhase_ = MotionPhase::kWaitingForReport;
      queueReport(
          "feed_cycle_completed",
          0,
          activeCommand_.commandId,
          "calibration_feed_cycle_completed",
          activeTiming_.revision,
          "",
          false);
      Serial.println("Feeder deadline reached; stop pulse written locally.");
      return;

    case MotionPhase::kStartHomeSettling:
      motionPhase_ = MotionPhase::kStartStation1Settling;
      phaseDeadlineAt_ = currentTime + activeTiming_.firstStationSettleMS;
      printTiming("station_1_settling_started");
      return;

    case MotionPhase::kStartStation1Settling:
      activeStationIndex_ = 1;
      motionPhase_ = MotionPhase::kWaitingForReport;
      queueReport(
          "station_1_ready",
          1,
          activeCommand_.commandId,
          "start_sequence_station_1_ready");
      return;

    case MotionPhase::kReleaseServoSettling:
      if (activeCommand_.stationIndex < FirmwareConfig::kGateCount) {
        motionPhase_ = MotionPhase::kNextStationSettling;
        phaseDeadlineAt_ = currentTime + activeTiming_.fruitSettleMS;
        printTiming("next_station_fruit_settling_started");
        return;
      }
      motionPhase_ = MotionPhase::kFinalFruitSettling;
      phaseDeadlineAt_ = currentTime + activeTiming_.fruitSettleMS;
      printTiming("final_gate_fruit_settling_started");
      return;

    case MotionPhase::kNextStationSettling: {
      const int nextStation = activeCommand_.stationIndex + 1;
      activeStationIndex_ = nextStation;
      motionPhase_ = MotionPhase::kWaitingForReport;
      queueReport(
          String("station_") + String(nextStation) + String("_ready"),
          nextStation,
          activeCommand_.commandId,
          "next_station_ready");
      return;
    }

    case MotionPhase::kFinalFruitSettling:
      motionPhase_ = MotionPhase::kFinalGateReturnDelay;
      phaseDeadlineAt_ = currentTime + activeTiming_.finalGateReturnDelayMS;
      return;

    case MotionPhase::kFinalGateReturnDelay:
      gates_.moveAll(activeCommand_.homeAngle);
      motionPhase_ = MotionPhase::kFinalHomeSettling;
      phaseDeadlineAt_ = currentTime + activeTiming_.servoSettleMS;
      printTiming("all_gates_returning_home");
      return;

    case MotionPhase::kFinalHomeSettling:
      sequenceActive_ = false;
      activeStationIndex_ = 0;
      motionPhase_ = MotionPhase::kWaitingForReport;
      queueReport(
          "capture_sequence_finished",
          0,
          activeCommand_.commandId,
          "sequence_finished");
      return;

    case MotionPhase::kIdle:
    case MotionPhase::kWaitingForReport:
    case MotionPhase::kWaitingForCommand:
      return;
  }
}

void CaptureController::handleSensor(uint32_t currentTime) {
  if (currentTime - lastSensorReadAt_ < FirmwareConfig::kSensorReadIntervalMS) {
    return;
  }
  lastSensorReadAt_ = currentTime;

  uint32_t elapsedUS = 0;
  const float distanceCM = sensor_.readCentimeters(&elapsedUS);

  if (currentTime - lastDistancePrintAt_ >= FirmwareConfig::kDistancePrintIntervalMS) {
    Serial.print("Distance: ");
    Serial.print(distanceCM);
    Serial.print(" cm sensor_read_us=");
    Serial.println(elapsedUS);
    lastDistancePrintAt_ = currentTime;
  }

  if (elapsedUS > FirmwareConfig::kEchoPulseTimeoutUS + 100UL) {
    Serial.print("WARN sensor read exceeded bounded pulse window: ");
    Serial.println(elapsedUS);
  }

  if (
      !sequenceActive_ &&
      !triggerArmed_ &&
      (distanceCM <= 0.0F || distanceCM > FirmwareConfig::kRearmDistanceCM)) {
    triggerArmed_ = true;
    Serial.println("Sensor area cleared. Trigger rearmed.");
  }

  String blockedReason;
  if (!shouldStartAutoTrigger(distanceCM, currentTime, blockedReason)) {
    if (blockedReason.length() > 0) {
      printTriggerDebug(blockedReason, distanceCM, currentTime, false);
    }
    return;
  }

  startAutoTrigger(currentTime);
}

bool CaptureController::shouldStartAutoTrigger(
    float distanceCM,
    uint32_t currentTime,
    String& reason) const {
  reason = "";
  if (distanceCM <= 0.0F || distanceCM > FirmwareConfig::kTriggerDistanceCM) {
    return false;
  }
  if (!autoTriggerEnabled_) {
    reason = "server_auto_trigger_disabled";
    return false;
  }
  if (autoTrigger_.active()) {
    reason = "pending_hcsr04_trigger";
    return false;
  }
  if (sequenceActive_) {
    reason = "blocked_by_sequence_active";
    return false;
  }
  if (classifierCommandActive_ || classifier_.busy()) {
    reason = "classifier_busy";
    return false;
  }
  if (!triggerArmed_) {
    reason = "blocked_by_trigger_not_armed";
    return false;
  }
  if (motionPhase_ != MotionPhase::kIdle) {
    reason = "gates_not_settled";
    return false;
  }
  if (!gates_.atHome()) {
    reason = "gates_not_home";
    return false;
  }
  if (
      lastTriggerAt_ != 0 &&
      currentTime - lastTriggerAt_ < FirmwareConfig::kCooldownMS) {
    reason = "blocked_by_cooldown";
    return false;
  }
  return true;
}

void CaptureController::startAutoTrigger(uint32_t currentTime) {
  lastTriggerAt_ = currentTime;
  triggerArmed_ = false;
  setAutoTriggerEnabled(false, "local_hcsr04_trigger");

  autoTrigger_ = AutoTrigger();
  autoTrigger_.triggerId = createTriggerId(currentTime);
  snapshotActiveTiming();
  printTiming("hcsr04_trigger_detected");

  if (
      FirmwareConfig::kEnableAutoStation1FastPath &&
      gatesAreSafeForFastPath()) {
    autoTrigger_.phase = AutoTriggerPhase::kFastWaitingForStationSettle;
    autoTrigger_.nextAttemptAt =
        currentTime + activeTiming_.firstStationSettleMS;
    Serial.print("Fast station-1 path armed. trigger_id=");
    Serial.println(autoTrigger_.triggerId);
    return;
  }

  startLegacyFallback(currentTime, "local_fast_path_safety_not_met");
}

bool CaptureController::processAutoTrigger(uint32_t currentTime) {
  if (!autoTrigger_.active()) {
    return false;
  }

  switch (autoTrigger_.phase) {
    case AutoTriggerPhase::kFastWaitingForStationSettle:
      if (!timeReached(currentTime, autoTrigger_.nextAttemptAt)) {
        return true;
      }
      if (!gatesAreSafeForFastPath()) {
        startLegacyFallback(currentTime, "fast_path_safety_changed_before_send");
        return true;
      }
      autoTrigger_.phase = AutoTriggerPhase::kFastReportPending;
      autoTrigger_.nextAttemptAt = currentTime;
      sendFastPathReport(currentTime);
      return true;

    case AutoTriggerPhase::kFastReportPending:
      if (!timeReached(currentTime, autoTrigger_.nextAttemptAt)) {
        return true;
      }
      sendFastPathReport(currentTime);
      return true;

    case AutoTriggerPhase::kLegacyReportPending:
      if (!timeReached(currentTime, autoTrigger_.nextAttemptAt)) {
        // A timed-out legacy report may already have created start_sequence.
        // Allow fast command polling while retaining the idempotent retry.
        return !autoTrigger_.waitingForStartSequence;
      }
      sendLegacyTriggerReport(currentTime);
      return true;

    case AutoTriggerPhase::kLegacyWaitingForStartSequence:
      return false;

    case AutoTriggerPhase::kNone:
      return false;
  }
  return false;
}

void CaptureController::sendFastPathReport(uint32_t currentTime) {
  if (!gatesAreSafeForFastPath()) {
    startLegacyFallback(currentTime, "fast_path_safety_changed_during_send");
    return;
  }

  const HttpResult result = api_.postReport(
      "hcsr04_station_1_ready",
      1,
      0,
      "fast_path_station_1_ready",
      FirmwareConfig::kAutoTriggerReportTimeoutMS,
      autoTrigger_.triggerId,
      true,
      true,
      true,
      appliedTiming_.revision);
  printReportSummary("hcsr04_station_1_ready", result);
  const uint32_t afterRequestTime = millis();

  if (DjangoApiClient::isExplicitFastPathRejection(result)) {
    startLegacyFallback(afterRequestTime, "server_rejected_fast_path");
    return;
  }

  if (!result.isSuccess()) {
    autoTrigger_.phase = AutoTriggerPhase::kFastReportPending;
    autoTrigger_.nextAttemptAt =
        afterRequestTime + FirmwareConfig::kAutoTriggerReportRetryIntervalMS;
    printTiming("hcsr04_station_1_ready_retry_scheduled");
    Serial.println("Fast-path request was ambiguous; retrying the same trigger_id.");
    return;
  }

  MotorCommand startCommand;
  if (api_.parseStartSequenceFromResponse(result.body, startCommand)) {
    clearAutoTrigger("fast_path_response_contains_start_sequence");
    handleCommand(startCommand, afterRequestTime);
    return;
  }

  const bool ignored = DjangoApiClient::responseHasIgnored(result.body);
  if (!ignored || DjangoApiClient::responseHasCaptureRequest(result.body)) {
    sequenceActive_ = true;
    activeStationIndex_ = 1;
    motionPhase_ = MotionPhase::kWaitingForCommand;
    clearAutoTrigger("fast_path_station_1_capture_opened");
    printTiming("station_1_fast_path_report_success");
    return;
  }

  if (DjangoApiClient::responseSuggestsStartSequenceWaiting(result.body)) {
    autoTrigger_.phase = AutoTriggerPhase::kLegacyWaitingForStartSequence;
    startStartSequenceWait(afterRequestTime, "existing_start_sequence_after_fast_path");
    return;
  }

  clearActiveTiming();
  clearAutoTrigger("fast_path_ignored_by_server");
  Serial.println("Fast-path trigger was ignored by server; waiting for sensor rearm.");
}

void CaptureController::sendLegacyTriggerReport(uint32_t currentTime) {
  const HttpResult result = api_.postReport(
      "hcsr04_trigger",
      0,
      0,
      "distance_trigger",
      FirmwareConfig::kAutoTriggerReportTimeoutMS,
      autoTrigger_.triggerId,
      false,
      false,
      false,
      appliedTiming_.revision);
  printReportSummary("hcsr04_trigger", result);
  const uint32_t afterRequestTime = millis();

  if (!result.isSuccess()) {
    autoTrigger_.phase = AutoTriggerPhase::kLegacyReportPending;
    autoTrigger_.nextAttemptAt =
        afterRequestTime + FirmwareConfig::kAutoTriggerReportRetryIntervalMS;
    if (!autoTrigger_.waitingForStartSequence) {
      autoTrigger_.waitingForStartSequence = true;
      autoTrigger_.waitingStartedAt = afterRequestTime;
      lastCommandPollAt_ =
          afterRequestTime - FirmwareConfig::kStartSequenceCommandPollIntervalMS;
      printTiming("legacy_hcsr04_timeout_fast_command_poll_enabled");
    }
    Serial.println("Legacy trigger request failed; retaining trigger_id for retry.");
    return;
  }

  MotorCommand startCommand;
  if (api_.parseStartSequenceFromResponse(result.body, startCommand)) {
    clearAutoTrigger("legacy_report_response_contains_start_sequence");
    handleCommand(startCommand, afterRequestTime);
    return;
  }

  if (DjangoApiClient::responseHasIgnored(result.body)) {
    if (DjangoApiClient::responseSuggestsStartSequenceWaiting(result.body)) {
      autoTrigger_.phase = AutoTriggerPhase::kLegacyWaitingForStartSequence;
      startStartSequenceWait(afterRequestTime, "duplicate_trigger_waiting_start_sequence");
      return;
    }

    clearActiveTiming();
    clearAutoTrigger("legacy_trigger_ignored_by_server");
    Serial.println("Legacy trigger was ignored by server; waiting for sensor rearm.");
    return;
  }

  autoTrigger_.phase = AutoTriggerPhase::kLegacyWaitingForStartSequence;
  startStartSequenceWait(afterRequestTime, "legacy_trigger_accepted");
}

void CaptureController::startLegacyFallback(
    uint32_t currentTime,
    const String& reason) {
  autoTrigger_.phase = AutoTriggerPhase::kLegacyReportPending;
  autoTrigger_.nextAttemptAt = currentTime;
  autoTrigger_.waitingForStartSequence = false;
  autoTrigger_.waitingStartedAt = 0;
  Serial.print("Using legacy hcsr04_trigger fallback: ");
  Serial.println(reason);
  printTiming("legacy_hcsr04_trigger_queued");
}

void CaptureController::clearAutoTrigger(const String& reason) {
  if (!autoTrigger_.active()) {
    return;
  }
  Serial.print("Auto trigger state cleared: ");
  Serial.println(reason);
  autoTrigger_ = AutoTrigger();
}

void CaptureController::startStartSequenceWait(
    uint32_t currentTime,
    const String& reason) {
  autoTrigger_.waitingForStartSequence = true;
  autoTrigger_.waitingStartedAt = currentTime;
  if (autoTrigger_.phase != AutoTriggerPhase::kLegacyReportPending) {
    autoTrigger_.phase = AutoTriggerPhase::kLegacyWaitingForStartSequence;
  }
  lastCommandPollAt_ =
      currentTime - FirmwareConfig::kStartSequenceCommandPollIntervalMS;
  commandBackoffUntilAt_ = 0;
  consecutiveCommandFailures_ = 0;
  Serial.print("Fast start_sequence polling enabled: ");
  Serial.println(reason);
  printTiming("fast_poll_start_sequence_enabled");
}

void CaptureController::checkStartSequenceWaitTimeout(uint32_t currentTime) {
  if (!autoTrigger_.waitingForStartSequence ||
      autoTrigger_.waitingStartedAt == 0 ||
      currentTime - autoTrigger_.waitingStartedAt <=
          FirmwareConfig::kStartSequenceWaitLimitMS) {
    return;
  }

  printTiming("start_sequence_wait_timeout");
  if (autoTrigger_.phase == AutoTriggerPhase::kLegacyReportPending) {
    // Keep retrying the same trigger_id, but stop prioritising command polls
    // until a later ambiguous response asks for them again.
    autoTrigger_.waitingForStartSequence = false;
    autoTrigger_.waitingStartedAt = 0;
    return;
  }

  clearActiveTiming();
  clearAutoTrigger("start_sequence_wait_timeout");
}

String CaptureController::createTriggerId(uint32_t currentTime) {
  const uint32_t chipId = static_cast<uint32_t>(ESP.getEfuseMac());
  triggerSequence_ += 1;
  return String("esp32-") + String(static_cast<unsigned long>(chipId), HEX) +
         String("-") + String(static_cast<unsigned long>(triggerSequence_)) +
         String("-") + String(static_cast<unsigned long>(currentTime));
}

bool CaptureController::gatesAreSafeForFastPath() const {
  return motionPhase_ == MotionPhase::kIdle &&
         !sequenceActive_ &&
         gates_.atHome();
}

bool CaptureController::flushPendingReport(uint32_t currentTime) {
  if (!pendingReport_.active) {
    return false;
  }
  if (!api_.wifiConnected()) {
    return true;
  }
  if (
      pendingReport_.lastAttemptAt != 0 &&
      currentTime - pendingReport_.lastAttemptAt <
          FirmwareConfig::kReportRetryIntervalMS) {
    return true;
  }

  pendingReport_.lastAttemptAt = currentTime;
  const HttpResult result = api_.postReport(
      pendingReport_.event,
      pendingReport_.stationIndex,
      pendingReport_.commandId,
      pendingReport_.message,
      FirmwareConfig::kReportHttpTimeoutMS,
      "",
      false,
      false,
      false,
      pendingReport_.timingRevision,
      pendingReport_.classificationCode,
      pendingReport_.includeStationIndex);
  printReportSummary(pendingReport_.event, result);

  if (!result.isSuccess()) {
    Serial.println("ESP32 report failed. It will be retried without repeating motion.");
    return true;
  }

  const String event = pendingReport_.event;
  const int commandId = pendingReport_.commandId;
  pendingReport_ = PendingReport();
  if (commandId > 0) {
    lastConfirmedCommandId_ = commandId;
    if (executingCommandId_ == commandId) {
      executingCommandId_ = 0;
    }
  }
  printTiming(event + String("_report_success"));
  handlePendingReportSuccess(event);
  return true;
}

void CaptureController::queueReport(
    const String& event,
    int stationIndex,
    int commandId,
    const String& message,
    uint32_t timingRevision,
    const String& classificationCode,
    bool includeStationIndex) {
  if (pendingReport_.active) {
    Serial.println("WARN attempted to overwrite an unsent ESP32 report.");
    return;
  }

  pendingReport_.active = true;
  pendingReport_.event = event;
  pendingReport_.stationIndex = stationIndex;
  pendingReport_.commandId = commandId;
  pendingReport_.message = message;
  pendingReport_.timingRevision = timingRevision;
  pendingReport_.classificationCode = classificationCode;
  pendingReport_.includeStationIndex = includeStationIndex;
  pendingReport_.lastAttemptAt = 0;
  printTiming(event + String("_queued"));
}

void CaptureController::handlePendingReportSuccess(const String& event) {
  if (event.startsWith("station_")) {
    motionPhase_ = MotionPhase::kWaitingForCommand;
    lastCommandPollAt_ =
        millis() - FirmwareConfig::kAwaitReleaseCommandPollIntervalMS;
    commandBackoffUntilAt_ = 0;
    return;
  }
  if (event == "capture_sequence_finished") {
    motionPhase_ = MotionPhase::kIdle;
    activeCommand_ = MotorCommand();
    clearActiveTiming();
    return;
  }
  if (event == "feed_cycle_completed") {
    motionPhase_ = MotionPhase::kIdle;
    activeCommand_ = MotorCommand();
    clearActiveTiming();
    return;
  }
  if (event == "classification_sorter_completed" ||
      event == "classification_sorter_failed") {
    classifierCommandActive_ = false;
    activeCommand_ = MotorCommand();
    return;
  }
}

void CaptureController::collectClassifierResult() {
  if (!classifier_.hasResult() || pendingReport_.active) {
    return;
  }
  const ClassifierController::Result result = classifier_.takeResult();
  queueReport(
      result.success ? "classification_sorter_completed"
                     : "classification_sorter_failed",
      0,
      result.commandId,
      result.reason,
      0,
      result.classificationCode,
      false);
}

TimingConfig CaptureController::defaultTimingConfig() const {
  TimingConfig timing;
  timing.revision = 0;
  timing.firstStationSettleMS = FirmwareConfig::kFirstStationSettleMS;
  timing.servoSettleMS = FirmwareConfig::kServoSettleMS;
  timing.fruitSettleMS = FirmwareConfig::kFruitSettleMS;
  timing.finalGateReturnDelayMS = FirmwareConfig::kFinalGateReturnDelayMS;
  timing.idleCommandPollIntervalMS = FirmwareConfig::kIdleCommandPollIntervalMS;
  timing.feederStopUS = FirmwareConfig::kFeederStopUS;
  timing.feederDriveUS = FirmwareConfig::kFeederDriveUS;
  timing.feederRunMS = FirmwareConfig::kFeederRunMS;
  timing.fruitArrivalWarningMS = FirmwareConfig::kFruitArrivalWarningMS;
  return timing;
}

bool CaptureController::timingConfigIsValid(const TimingConfig& timing) const {
  constexpr uint32_t kMinimumSettleMS = 50UL;
  constexpr uint32_t kMaximumSettleMS = 3000UL;
  return timing.firstStationSettleMS >= kMinimumSettleMS &&
         timing.firstStationSettleMS <= kMaximumSettleMS &&
         timing.servoSettleMS >= kMinimumSettleMS &&
         timing.servoSettleMS <= kMaximumSettleMS &&
         timing.fruitSettleMS >= kMinimumSettleMS &&
         timing.fruitSettleMS <= kMaximumSettleMS &&
         timing.finalGateReturnDelayMS <= kMaximumSettleMS &&
         timing.idleCommandPollIntervalMS >= 100UL &&
         timing.idleCommandPollIntervalMS <= 5000UL &&
         timing.idleCommandPollIntervalMS % 50UL == 0 &&
         timing.feederStopUS >= 1400UL && timing.feederStopUS <= 1600UL &&
         timing.feederStopUS % 5UL == 0 &&
         timing.feederDriveUS >= 1000UL && timing.feederDriveUS <= 2000UL &&
         timing.feederDriveUS % 10UL == 0 &&
         abs(static_cast<int>(timing.feederDriveUS) -
             static_cast<int>(timing.feederStopUS)) >= 100 &&
         timing.feederRunMS >= 50UL && timing.feederRunMS <= 500UL &&
         timing.feederRunMS % 5UL == 0 &&
         timing.fruitArrivalWarningMS >= 1000UL &&
         timing.fruitArrivalWarningMS <= 30000UL &&
         timing.fruitArrivalWarningMS % 500UL == 0;
}

bool CaptureController::timingConfigCanApply() const {
  return motionPhase_ == MotionPhase::kIdle && !sequenceActive_ &&
         !classifierCommandActive_ && !classifier_.busy() &&
         !autoTrigger_.active() && !pendingReport_.active && gates_.atHome();
}

void CaptureController::stageTimingConfig(const MotorCommand& command) {
  if (!command.hasTimingConfig) {
    return;
  }
  if (!timingConfigIsValid(command.timing)) {
    Serial.println("Ignoring invalid timing configuration from Django.");
    return;
  }
  if (command.timing.revision <= appliedTiming_.revision) {
    return;
  }

  pendingTiming_ = command.timing;
  hasPendingTiming_ = true;
}

bool CaptureController::applyPendingTimingConfig(uint32_t currentTime) {
  if (!hasPendingTiming_ || !timingConfigCanApply()) {
    return false;
  }

  if (pendingTiming_.revision <= appliedTiming_.revision) {
    hasPendingTiming_ = false;
    return false;
  }

  appliedTiming_ = pendingTiming_;
  hasPendingTiming_ = false;
  Serial.print("Timing configuration applied. revision=");
  Serial.print(appliedTiming_.revision);
  Serial.print(" first=");
  Serial.print(appliedTiming_.firstStationSettleMS);
  Serial.print(" servo=");
  Serial.print(appliedTiming_.servoSettleMS);
  Serial.print(" fruit=");
  Serial.print(appliedTiming_.fruitSettleMS);
  Serial.print(" final=");
  Serial.print(appliedTiming_.finalGateReturnDelayMS);
  Serial.print(" idle_poll=");
  Serial.println(appliedTiming_.idleCommandPollIntervalMS);
  queueReport(
      "timing_config_applied",
      0,
      0,
      "timing_config_applied",
      appliedTiming_.revision);
  printTiming("timing_config_applied_queued");
  return true;
}

void CaptureController::snapshotActiveTiming() {
  activeTiming_ = appliedTiming_;
  hasActiveTiming_ = true;
}

void CaptureController::clearActiveTiming() {
  activeTiming_ = TimingConfig();
  hasActiveTiming_ = false;
}

void CaptureController::pollCommand(uint32_t currentTime) {
  if (!api_.wifiConnected()) {
    return;
  }
  const bool waitingForStartSequence = autoTrigger_.waitingForStartSequence;
  const bool waitingForRelease = motionPhase_ == MotionPhase::kWaitingForCommand;
  const bool idle = motionPhase_ == MotionPhase::kIdle;
  if (!waitingForStartSequence && !waitingForRelease && !idle) {
    return;
  }
  if (
      commandBackoffUntilAt_ != 0 &&
      !timeReached(currentTime, commandBackoffUntilAt_)) {
    return;
  }
  if (commandBackoffUntilAt_ != 0) {
    commandBackoffUntilAt_ = 0;
  }

  const uint32_t interval = currentCommandPollInterval();
  if (currentTime - lastCommandPollAt_ < interval) {
    return;
  }
  lastCommandPollAt_ = currentTime;

  const HttpResult result = api_.pollCommand("idle", lastFeedCommandId_);
  if (!result.isSuccess()) {
    Serial.print("Command GET failed: ");
    Serial.print(result.statusCode);
    Serial.print(" error=");
    Serial.print(httpErrorName(result.statusCode));
    Serial.print(" request_ms=");
    Serial.println(result.elapsedMS);
    registerCommandFailure(result.statusCode);
    return;
  }

  resetCommandFailures();
  const MotorCommand command = api_.parseCommandText(result.body);
  if (command.hasAutoTriggerEnabled) {
    setAutoTriggerEnabled(command.autoTriggerEnabled, command.serverStatus);
  }
  stageTimingConfig(command);
  if (applyPendingTimingConfig(currentTime)) {
    return;
  }
  if (command.command == "none" || command.commandId <= 0) {
    return;
  }

  handleCommand(command, millis());
}

uint32_t CaptureController::currentCommandPollInterval() const {
  if (autoTrigger_.waitingForStartSequence) {
    return FirmwareConfig::kStartSequenceCommandPollIntervalMS;
  }
  if (motionPhase_ == MotionPhase::kWaitingForCommand) {
    return FirmwareConfig::kAwaitReleaseCommandPollIntervalMS;
  }
  return appliedTiming_.idleCommandPollIntervalMS;
}

void CaptureController::handleCommand(
    const MotorCommand& command,
    uint32_t currentTime) {
  stageTimingConfig(command);
  if (command.commandId == lastConfirmedCommandId_ ||
      command.commandId == executingCommandId_) {
    return;
  }

  Serial.print("Command #");
  Serial.print(command.commandId);
  Serial.print(": ");
  Serial.print(command.command);
  Serial.print(" station ");
  Serial.println(command.stationIndex);

  if (classifierCommandActive_ && command.command != "classify_fruit") {
    logCommandRejected(command, "classifier_busy");
    return;
  }

  if (command.command == "classify_fruit") {
    if (sequenceActive_ || motionPhase_ != MotionPhase::kIdle) {
      queueClassifierFailure(command, "capture_sequence_active");
      return;
    }
    if (classifierCommandActive_ || classifier_.busy()) {
      logCommandRejected(command, "classifier_busy");
      return;
    }
    startClassifier(command, currentTime);
    return;
  }

  if (command.command == "feed_one") {
    if (command.feedContext != "calibration" || sequenceActive_ ||
        classifierCommandActive_ || classifier_.busy() ||
        motionPhase_ != MotionPhase::kIdle) {
      queueMotorError(command, "calibration_feed_not_safe");
      return;
    }
    startFeeder(command, currentTime);
    return;
  }

  if (command.command == "start_sequence") {
    if (sequenceActive_) {
      queueMotorError(command, "start_sequence_while_sequence_active");
      return;
    }
    startSequence(command, currentTime);
    return;
  }

  if (command.command == "release_gate") {
    releaseGate(command, currentTime);
    return;
  }

  queueMotorError(command, "unknown_command");
}

void CaptureController::startFeeder(
    const MotorCommand& command,
    uint32_t currentTime) {
  setAutoTriggerEnabled(false, "calibration_feed_active");
  snapshotActiveTiming();
  activeCommand_ = command;
  executingCommandId_ = command.commandId;
  feeder_.writeMicroseconds(activeTiming_.feederDriveUS);
  motionPhase_ = MotionPhase::kFeederDriving;
  phaseDeadlineAt_ = currentTime + activeTiming_.feederRunMS;
  Serial.print("Calibration feeder started. command_id=");
  Serial.print(command.commandId);
  Serial.print(" run_ms=");
  Serial.println(activeTiming_.feederRunMS);
}

void CaptureController::startClassifier(
    const MotorCommand& command,
    uint32_t currentTime) {
  classifierCommandActive_ = true;
  executingCommandId_ = command.commandId;
  activeCommand_ = command;
  setAutoTriggerEnabled(false, "classifier_busy");

  String reason;
  if (!classifier_.start(
          command.commandId,
          command.classificationCode,
          currentTime,
          reason)) {
    queueClassifierFailure(command, reason.length() > 0 ? reason : "classifier_busy");
    return;
  }

  Serial.print("Classifier command #");
  Serial.print(command.commandId);
  Serial.print(" code=");
  Serial.println(command.classificationCode);
}

void CaptureController::queueClassifierFailure(
    const MotorCommand& command,
    const String& reason) {
  classifierCommandActive_ = true;
  executingCommandId_ = command.commandId;
  activeCommand_ = command;
  setAutoTriggerEnabled(false, "classifier_error");
  logCommandRejected(command, reason);
  queueReport(
      "classification_sorter_failed",
      0,
      command.commandId,
      reason,
      0,
      command.classificationCode,
      false);
}

void CaptureController::logCommandRejected(
    const MotorCommand& command,
    const String& reason) const {
  Serial.print("Command rejected: id=");
  Serial.print(command.commandId);
  Serial.print(" type=");
  Serial.print(command.command);
  Serial.print(" reason=");
  Serial.println(reason);
}

void CaptureController::startSequence(
    const MotorCommand& command,
    uint32_t currentTime) {
  clearAutoTrigger("start_sequence_command_received");
  setAutoTriggerEnabled(false, "sequence_active");
  if (!hasActiveTiming_) {
    snapshotActiveTiming();
  }
  activeCommand_ = command;
  activeStationIndex_ = 1;
  sequenceActive_ = true;
  executingCommandId_ = command.commandId;
  printTiming("start_sequence_received");

  if (motionPhase_ == MotionPhase::kBootHomeSettling) {
    motionPhase_ = MotionPhase::kStartStation1Settling;
    phaseDeadlineAt_ += activeTiming_.firstStationSettleMS;
    return;
  }

  if (!gates_.allAtAngle(command.homeAngle)) {
    gates_.moveAll(command.homeAngle);
    motionPhase_ = MotionPhase::kStartHomeSettling;
    phaseDeadlineAt_ = currentTime + activeTiming_.servoSettleMS;
    return;
  }

  motionPhase_ = MotionPhase::kStartStation1Settling;
  phaseDeadlineAt_ = currentTime + activeTiming_.firstStationSettleMS;
  printTiming("station_1_settling_started");
}

void CaptureController::releaseGate(
    const MotorCommand& command,
    uint32_t currentTime) {
  if (command.stationIndex < 1 ||
      command.stationIndex > FirmwareConfig::kGateCount) {
    queueMotorError(command, "invalid_station_index");
    return;
  }
  if (!sequenceActive_ || motionPhase_ != MotionPhase::kWaitingForCommand) {
    queueMotorError(command, "release_gate_not_expected");
    return;
  }
  if (command.stationIndex != activeStationIndex_) {
    queueMotorError(command, "release_gate_station_mismatch");
    return;
  }
  if (!gates_.moveGate(command.stationIndex, command.releaseAngle)) {
    queueMotorError(command, "gate_index_out_of_range");
    return;
  }

  if (!hasActiveTiming_) {
    snapshotActiveTiming();
  }
  activeCommand_ = command;
  executingCommandId_ = command.commandId;
  motionPhase_ = MotionPhase::kReleaseServoSettling;
  phaseDeadlineAt_ = currentTime + activeTiming_.servoSettleMS;
  printTiming(
      String("release_gate_") + String(command.stationIndex) + String("_received"));
}

void CaptureController::queueMotorError(
    const MotorCommand& command,
    const String& reason) {
  Serial.print("Motor command rejected: ");
  Serial.println(reason);
  gates_.moveAll(FirmwareConfig::kHomeAngle);
  setAutoTriggerEnabled(false, "motor_error");
  sequenceActive_ = false;
  clearActiveTiming();
  activeStationIndex_ = 0;
  activeCommand_ = MotorCommand();
  executingCommandId_ = command.commandId;
  motionPhase_ = MotionPhase::kBootHomeSettling;
  phaseDeadlineAt_ = millis() + appliedTiming_.servoSettleMS;
  queueReport("motor_error", command.stationIndex, command.commandId, reason);
}

void CaptureController::setAutoTriggerEnabled(
    bool enabled,
    const String& serverStatus) {
  if (serverStatus.length() > 0) {
    lastServerStatus_ = serverStatus;
  }
  if (autoTriggerEnabled_ == enabled) {
    return;
  }

  autoTriggerEnabled_ = enabled;
  Serial.print("Auto trigger: ");
  Serial.print(autoTriggerEnabled_ ? "enabled" : "disabled");
  if (lastServerStatus_.length() > 0) {
    Serial.print(" (server_status=");
    Serial.print(lastServerStatus_);
    Serial.print(")");
  }
  Serial.println();
}

void CaptureController::registerCommandFailure(int httpCode) {
  consecutiveCommandFailures_ += 1;
  uint32_t backoffMS = FirmwareConfig::kCommandFailureBackoffMinMS;
  if (autoTrigger_.waitingForStartSequence) {
    backoffMS = FirmwareConfig::kStartSequenceCommandPollIntervalMS;
  } else if (motionPhase_ == MotionPhase::kWaitingForCommand) {
    backoffMS = FirmwareConfig::kAwaitReleaseCommandPollIntervalMS;
  } else if (consecutiveCommandFailures_ >= 2) {
    backoffMS = FirmwareConfig::kCommandFailureBackoffMaxMS;
  }

  commandBackoffUntilAt_ = millis() + backoffMS;
  Serial.print("Command polling backoff: ");
  Serial.print(backoffMS);
  Serial.print(" ms after ");
  Serial.print(consecutiveCommandFailures_);
  Serial.print(" failure(s), last_error=");
  Serial.println(httpErrorName(httpCode));
}

void CaptureController::resetCommandFailures() {
  consecutiveCommandFailures_ = 0;
  commandBackoffUntilAt_ = 0;
}

void CaptureController::printReportSummary(
    const String& event,
    const HttpResult& result) const {
  Serial.print("Report ");
  Serial.print(event);
  Serial.print(" HTTP ");
  Serial.print(result.statusCode);
  Serial.print(" request_ms=");
  Serial.print(result.elapsedMS);
  Serial.print(" body_length=");
  Serial.print(result.body.length());
  Serial.print(" ignored=");
  Serial.print(DjangoApiClient::responseHasIgnored(result.body) ? "1" : "0");
  Serial.print(" start_sequence=");
  Serial.println(result.body.indexOf("start_sequence") >= 0 ? "1" : "0");

  if (FirmwareConfig::kVerboseHttpResponseLog && result.body.length() > 0) {
    Serial.println(result.body);
  }
}

void CaptureController::printTriggerDebug(
    const String& reason,
    float distanceCM,
    uint32_t currentTime,
    bool force) {
  if (!force &&
      lastTriggerDebugAt_ != 0 &&
      currentTime - lastTriggerDebugAt_ < FirmwareConfig::kTriggerDebugIntervalMS) {
    return;
  }
  lastTriggerDebugAt_ = currentTime;

  Serial.print("HC-SR04 trigger debug: reason=");
  Serial.print(reason);
  Serial.print(" distance=");
  Serial.print(distanceCM);
  Serial.print(" cm auto_trigger_enabled=");
  Serial.print(autoTriggerEnabled_ ? "1" : "0");
  Serial.print(" server_status=");
  Serial.print(lastServerStatus_.length() > 0 ? lastServerStatus_ : "unknown");
  Serial.print(" trigger_armed=");
  Serial.print(triggerArmed_ ? "1" : "0");
  Serial.print(" sequence_active=");
  Serial.print(sequenceActive_ ? "1" : "0");
  Serial.print(" pending_hcsr04_trigger=");
  Serial.println(autoTrigger_.active() ? "1" : "0");
}

void CaptureController::printTiming(const String& eventName) const {
  Serial.print("TIMING ");
  Serial.print(eventName);
  Serial.print(" ms=");
  Serial.println(millis());
}

bool CaptureController::timeReached(uint32_t currentTime, uint32_t deadline) {
  return static_cast<int32_t>(currentTime - deadline) >= 0;
}

const char* CaptureController::httpErrorName(int httpCode) {
  switch (httpCode) {
    case -1:
      return "CONNECTION_REFUSED";
    case -2:
      return "SEND_HEADER_FAILED";
    case -3:
      return "SEND_PAYLOAD_FAILED";
    case -4:
      return "NOT_CONNECTED";
    case -5:
      return "CONNECTION_LOST";
    case -6:
      return "NO_STREAM";
    case -7:
      return "NO_HTTP_SERVER";
    case -8:
      return "TOO_LESS_RAM";
    case -9:
      return "ENCODING";
    case -10:
      return "STREAM_WRITE";
    case -11:
      return "READ_TIMEOUT";
    default:
      return "HTTP_ERROR";
  }
}
