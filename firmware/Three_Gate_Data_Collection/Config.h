#pragma once

#include <Arduino.h>

namespace FirmwareConfig {

constexpr uint8_t kGateCount = 3;
constexpr uint8_t kGatePins[kGateCount] = {18, 19, 21};
constexpr uint8_t kUltrasonicTrigPin = 26;
constexpr uint8_t kUltrasonicEchoPin = 27;
constexpr uint8_t kClassifierPin = 25;
constexpr uint8_t kFeederPin = 23;

constexpr int kHomeAngle = 0;
constexpr int kReleaseAngle = 90;
constexpr int kClassifierHomeAngle = 85;
constexpr int kClassifierHighAngle = 25;
constexpr int kClassifierMediumAngle = 55;
constexpr int kClassifierLowAngle = 115;
constexpr int kClassifierProcessingAngle = 145;

constexpr float kTriggerDistanceCM = 6.0F;
constexpr float kRearmDistanceCM = 8.0F;
constexpr uint32_t kEchoPulseTimeoutUS = 12000UL;

constexpr uint32_t kSensorReadIntervalMS = 50UL;
constexpr uint32_t kDistancePrintIntervalMS = 500UL;
constexpr uint32_t kTriggerDebugIntervalMS = 1000UL;
constexpr uint32_t kCooldownMS = 3000UL;

constexpr uint32_t kIdleCommandPollIntervalMS = 250UL;
constexpr uint32_t kFeederStopUS = 1500UL;
constexpr uint32_t kFeederDriveUS = 1300UL;
constexpr uint32_t kFeederMaxRunMS = 5000UL;
constexpr uint32_t kStartSequenceCommandPollIntervalMS = 100UL;
constexpr uint32_t kAwaitReleaseCommandPollIntervalMS = 50UL;

constexpr uint32_t kServoSettleMS = 200UL;
constexpr uint32_t kFruitSettleMS = 350UL;
constexpr uint32_t kFirstStationSettleMS = 300UL;
constexpr uint32_t kFinalGateReturnDelayMS = 300UL;
constexpr uint32_t kClassifierHoldMS = 1000UL;
constexpr uint32_t kClassifierHomeSettleMS = 500UL;
constexpr uint32_t kClassifierTimeoutMS = 5000UL;

constexpr uint32_t kReportRetryIntervalMS = 1000UL;
constexpr uint32_t kAutoTriggerReportRetryIntervalMS = 1000UL;
constexpr uint32_t kStartSequenceWaitLimitMS = 10000UL;

constexpr uint32_t kWifiConnectAttemptTimeoutMS = 15000UL;
constexpr uint32_t kWifiReconnectIntervalMS = 2000UL;
constexpr uint32_t kCommandHttpTimeoutMS = 1500UL;
constexpr uint32_t kAutoTriggerReportTimeoutMS = 1000UL;
constexpr uint32_t kReportHttpTimeoutMS = 5000UL;

constexpr uint32_t kCommandFailureBackoffMinMS = 1500UL;
constexpr uint32_t kCommandFailureBackoffMaxMS = 10000UL;

// Set this to false for an immediate firmware-only rollback to the legacy
// hcsr04_trigger -> start_sequence -> station_1_ready handshake.
constexpr bool kEnableAutoStation1FastPath = true;
constexpr bool kVerboseHttpResponseLog = false;

static_assert(kClassifierPin != kGatePins[0], "Classifier pin conflicts with Gate 1");
static_assert(kClassifierPin != kGatePins[1], "Classifier pin conflicts with Gate 2");
static_assert(kClassifierPin != kGatePins[2], "Classifier pin conflicts with Gate 3");
static_assert(kFeederPin != kClassifierPin, "Feeder pin conflicts with classifier");
static_assert(kFeederPin != kGatePins[0], "Feeder pin conflicts with Gate 1");
static_assert(kFeederPin != kGatePins[1], "Feeder pin conflicts with Gate 2");
static_assert(kFeederPin != kGatePins[2], "Feeder pin conflicts with Gate 3");

}  // namespace FirmwareConfig
