#pragma once

#include <Arduino.h>

namespace FirmwareConfig {

constexpr uint8_t kGateCount = 3;
constexpr uint8_t kGatePins[kGateCount] = {18, 19, 21};
constexpr uint8_t kUltrasonicTrigPin = 26;
constexpr uint8_t kUltrasonicEchoPin = 27;

constexpr int kHomeAngle = 0;
constexpr int kReleaseAngle = 90;

constexpr float kTriggerDistanceCM = 6.0F;
constexpr float kRearmDistanceCM = 8.0F;
constexpr uint32_t kEchoPulseTimeoutUS = 12000UL;

constexpr uint32_t kSensorReadIntervalMS = 50UL;
constexpr uint32_t kDistancePrintIntervalMS = 500UL;
constexpr uint32_t kTriggerDebugIntervalMS = 1000UL;
constexpr uint32_t kCooldownMS = 3000UL;

constexpr uint32_t kIdleCommandPollIntervalMS = 5000UL;
constexpr uint32_t kStartSequenceCommandPollIntervalMS = 100UL;
constexpr uint32_t kActiveCommandPollIntervalMS = 120UL;

constexpr uint32_t kServoSettleMS = 200UL;
constexpr uint32_t kFruitSettleMS = 200UL;
constexpr uint32_t kFirstStationSettleMS = 200UL;
constexpr uint32_t kFinalGateReturnDelayMS = 200UL;

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

}  // namespace FirmwareConfig
