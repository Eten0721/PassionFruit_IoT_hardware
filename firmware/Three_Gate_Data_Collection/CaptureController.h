#pragma once

#include <Arduino.h>

#include "Config.h"
#include "DjangoApiClient.h"
#include "DistanceSensor.h"
#include "GateController.h"
#include "ProtocolTypes.h"

class CaptureController {
 public:
  void begin();
  void tick();

 private:
  enum class MotionPhase : uint8_t {
    kBootHomeSettling,
    kIdle,
    kStartHomeSettling,
    kStartStation1Settling,
    kReleaseServoSettling,
    kNextStationSettling,
    kFinalFruitSettling,
    kFinalGateReturnDelay,
    kFinalHomeSettling,
    kWaitingForReport,
    kWaitingForCommand,
  };

  enum class AutoTriggerPhase : uint8_t {
    kNone,
    kFastWaitingForStationSettle,
    kFastReportPending,
    kLegacyReportPending,
    kLegacyWaitingForStartSequence,
  };

  struct PendingReport {
    bool active;
    String event;
    int stationIndex;
    int commandId;
    String message;
    uint32_t timingRevision;
    uint32_t lastAttemptAt;

    PendingReport()
        : active(false),
          event(""),
          stationIndex(0),
          commandId(0),
          message(""),
          timingRevision(0),
          lastAttemptAt(0) {}
  };

  struct AutoTrigger {
    AutoTriggerPhase phase;
    String triggerId;
    uint32_t detectedAt;
    uint32_t nextAttemptAt;
    bool waitingForStartSequence;
    uint32_t waitingStartedAt;

    AutoTrigger()
        : phase(AutoTriggerPhase::kNone),
          triggerId(""),
          detectedAt(0),
          nextAttemptAt(0),
          waitingForStartSequence(false),
          waitingStartedAt(0) {}

    bool active() const {
      return phase != AutoTriggerPhase::kNone;
    }
  };

  DistanceSensor sensor_{FirmwareConfig::kUltrasonicTrigPin,
                         FirmwareConfig::kUltrasonicEchoPin};
  GateController gates_;
  DjangoApiClient api_;

  MotionPhase motionPhase_ = MotionPhase::kBootHomeSettling;
  uint32_t phaseDeadlineAt_ = 0;
  MotorCommand activeCommand_;
  int activeStationIndex_ = 0;
  bool sequenceActive_ = false;
  TimingConfig appliedTiming_;
  TimingConfig pendingTiming_;
  TimingConfig activeTiming_;
  bool hasPendingTiming_ = false;
  bool hasActiveTiming_ = false;

  PendingReport pendingReport_;
  AutoTrigger autoTrigger_;

  uint32_t lastSensorReadAt_ = 0;
  uint32_t lastDistancePrintAt_ = 0;
  uint32_t lastTriggerDebugAt_ = 0;
  uint32_t lastTriggerAt_ = 0;
  uint32_t lastCommandPollAt_ = 0;
  uint32_t commandBackoffUntilAt_ = 0;
  uint32_t triggerSequence_ = 0;
  int consecutiveCommandFailures_ = 0;
  int lastConfirmedCommandId_ = 0;
  int executingCommandId_ = 0;
  bool triggerArmed_ = true;
  bool autoTriggerEnabled_ = false;
  String lastServerStatus_;

  void advanceMotion(uint32_t currentTime);
  void handleSensor(uint32_t currentTime);
  bool shouldStartAutoTrigger(float distanceCM, uint32_t currentTime, String& reason) const;
  void startAutoTrigger(uint32_t currentTime);
  bool processAutoTrigger(uint32_t currentTime);
  void sendFastPathReport(uint32_t currentTime);
  void sendLegacyTriggerReport(uint32_t currentTime);
  void startLegacyFallback(uint32_t currentTime, const String& reason);
  void clearAutoTrigger(const String& reason);
  void startStartSequenceWait(uint32_t currentTime, const String& reason);
  void checkStartSequenceWaitTimeout(uint32_t currentTime);
  String createTriggerId(uint32_t currentTime);
  bool gatesAreSafeForFastPath() const;

  bool flushPendingReport(uint32_t currentTime);
  void queueReport(
      const String& event,
      int stationIndex,
      int commandId,
      const String& message,
      uint32_t timingRevision = 0);
  void handlePendingReportSuccess(const String& event);

  TimingConfig defaultTimingConfig() const;
  bool timingConfigIsValid(const TimingConfig& timing) const;
  bool timingConfigCanApply() const;
  void stageTimingConfig(const MotorCommand& command);
  bool applyPendingTimingConfig(uint32_t currentTime);
  void snapshotActiveTiming();
  void clearActiveTiming();

  void pollCommand(uint32_t currentTime);
  uint32_t currentCommandPollInterval() const;
  void handleCommand(const MotorCommand& command, uint32_t currentTime);
  void startSequence(const MotorCommand& command, uint32_t currentTime);
  void releaseGate(const MotorCommand& command, uint32_t currentTime);
  void queueMotorError(const MotorCommand& command, const String& reason);
  void setAutoTriggerEnabled(bool enabled, const String& serverStatus);
  void registerCommandFailure(int httpCode);
  void resetCommandFailures();

  void printReportSummary(const String& event, const HttpResult& result) const;
  void printTriggerDebug(
      const String& reason,
      float distanceCM,
      uint32_t currentTime,
      bool force);
  void printTiming(const String& eventName) const;
  static bool timeReached(uint32_t currentTime, uint32_t deadline);
  static const char* httpErrorName(int httpCode);
};
