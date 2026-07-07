#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <ESP32Servo.h>
#include <Ultrasonic.h>
#include "secrets.h"

/*
  ESP32 formal three-gate data collection firmware.

  Wiring:
  - HC-SR04 Trig -> GPIO 26
  - HC-SR04 Echo -> GPIO 27
  - Gate 1 SG90 signal -> GPIO 18
  - Gate 2 SG90 signal -> GPIO 19
  - Gate 3 SG90 signal -> GPIO 21
  - Servo GND and ESP32 GND must be connected together.

  Gate angles:
  - HOME_ANGLE = 0: initial blocking position.
  - RELEASE_ANGLE = 60: release position.

  Protocol:
  - HC-SR04 automatic trigger reports hcsr04_trigger to Django.
  - ESP32 polls Django commandUrl every commandPollIntervalMS.
  - ESP32 executes start_sequence or release_gate commands.
  - ESP32 reports station_N_ready and capture_sequence_finished through reportUrl.
*/

Ultrasonic ultrasonic(26, 27);

Servo gate1Servo;
Servo gate2Servo;
Servo gate3Servo;

const int gate1Pin = 18;
const int gate2Pin = 19;
const int gate3Pin = 21;

const int HOME_ANGLE = 0;
const int RELEASE_ANGLE = 60;

const float triggerDistanceCM = 5.0;
const float rearmDistanceCM = 8.0;

const unsigned long sensorReadIntervalMS = 50;
const unsigned long commandPollIntervalMS = 5000;
const unsigned long activeCommandPollIntervalMS = 1000;
const unsigned long distancePrintIntervalMS = 500;
const unsigned long triggerDebugIntervalMS = 1000;
const unsigned long cooldownMS = 3000;
const unsigned long servoSettleMS = 700;
const unsigned long fruitSettleMS = 700;
const unsigned long reportRetryIntervalMS = 1000;
const unsigned long autoTriggerReportRetryIntervalMS = 1000;
const unsigned long wifiConnectTimeoutMS = 15000;
const unsigned long httpTimeoutMS = 5000;
const unsigned long commandFailureBackoffMinMS = 1500;
const unsigned long commandFailureBackoffMaxMS = 10000;

unsigned long lastSensorReadMS = 0;
unsigned long lastCommandPollMS = 0;
unsigned long lastDistancePrintMS = 0;
unsigned long lastTriggerDebugMS = 0;
unsigned long lastTriggerTimeMS = 0;
unsigned long lastReportRetryMS = 0;
unsigned long commandBackoffUntilMS = 0;
int consecutiveCommandFailures = 0;

bool triggerArmed = true;
bool sequenceActive = false;
bool lastReportIgnored = false;
bool autoTriggerEnabled = false;
int lastConfirmedCommandId = 0;
String lastServerStatus = "";

bool pendingReport = false;
String pendingEvent = "";
int pendingStationIndex = 0;
int pendingCommandId = 0;
String pendingMessage = "";

bool autoTriggerReportPending = false;
unsigned long lastAutoTriggerReportMS = 0;

struct MotorCommand {
  String command;
  int commandId;
  int stationIndex;
  int homeAngle;
  int releaseAngle;
  int servoSettleMs;
  int fruitSettleMs;
  bool autoTriggerEnabled;
  String serverStatus;
};

bool connectWiFi(unsigned long timeoutMS);
void ensureWiFi();
float readDistanceCM();
void handleSensor(unsigned long currentTime);
void pollCommand();
void handleCommand(const MotorCommand& command);
void setAutoTriggerEnabled(bool enabled, const String& serverStatus);
unsigned long currentCommandPollInterval();
void registerCommandFailure(int httpCode);
void resetCommandFailures();
const char* httpErrorName(int httpCode);
MotorCommand parseCommand(const String& body);
String readValue(const String& body, const String& key);
bool postReport(const String& event, int stationIndex, int commandId, const String& message);
void queueReport(const String& event, int stationIndex, int commandId, const String& message);
void flushPendingReport(unsigned long currentTime);
void queueAutoTriggerReport();
void flushAutoTriggerReport(unsigned long currentTime);
void clearAutoTriggerReportPending(const String& reason);
void printTriggerDebug(const String& reason, float distanceCM, unsigned long currentTime, bool force);
void moveGate(int stationIndex, int angle);
void moveAllGates(int angle);
Servo* servoForStation(int stationIndex);

void setup() {
  Serial.begin(115200);
  delay(1000);

  gate1Servo.attach(gate1Pin);
  gate2Servo.attach(gate2Pin);
  gate3Servo.attach(gate3Pin);
  moveAllGates(HOME_ANGLE);
  delay(servoSettleMS);

  Serial.println();
  Serial.println("=== Three Gate Data Collection Firmware ===");
  Serial.print("Command URL: ");
  Serial.println(commandUrl);
  Serial.print("Report URL: ");
  Serial.println(reportUrl);
  Serial.println("Auto trigger: disabled until first successful command polling.");
  Serial.print("Trigger distance: ");
  Serial.print(triggerDistanceCM);
  Serial.println(" cm");

  Serial.print("Connecting Wi-Fi: ");
  Serial.println(ssid);
  if (!connectWiFi(wifiConnectTimeoutMS)) {
    Serial.println("Wi-Fi initial connection timed out. Main loop will retry.");
  }
}

void loop() {
  const unsigned long currentTime = millis();
  ensureWiFi();
  flushPendingReport(currentTime);

  if (currentTime - lastSensorReadMS >= sensorReadIntervalMS) {
    lastSensorReadMS = currentTime;
    handleSensor(currentTime);
  }

  flushAutoTriggerReport(currentTime);

  const unsigned long pollInterval = currentCommandPollInterval();
  if (
    !pendingReport &&
    currentTime >= commandBackoffUntilMS &&
    currentTime - lastCommandPollMS >= pollInterval
  ) {
    lastCommandPollMS = currentTime;
    pollCommand();
  }
}

bool connectWiFi(unsigned long timeoutMS) {
  WiFi.begin(ssid, password);
  unsigned long startAttemptTime = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < timeoutMS) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.print("Wi-Fi connected. IP: ");
    Serial.println(WiFi.localIP());
    return true;
  }
  Serial.println();
  return false;
}

void ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.println("Wi-Fi disconnected. Reconnecting...");
  WiFi.disconnect();
  WiFi.begin(ssid, password);

  unsigned long startAttemptTime = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < 3000) {
    delay(200);
    Serial.print(".");
  }
  Serial.println();
}

float readDistanceCM() {
  long duration = ultrasonic.timing();
  return ultrasonic.convert(duration, Ultrasonic::CM);
}

void handleSensor(unsigned long currentTime) {
  const float distanceCM = readDistanceCM();

  if (currentTime - lastDistancePrintMS >= distancePrintIntervalMS) {
    Serial.print("Distance: ");
    Serial.print(distanceCM);
    Serial.println(" cm");
    lastDistancePrintMS = currentTime;
  }

  if (!sequenceActive && !triggerArmed && (distanceCM <= 0 || distanceCM > rearmDistanceCM)) {
    triggerArmed = true;
    Serial.println("Sensor area cleared. Trigger rearmed.");
  }

  if (distanceCM <= 0 || distanceCM > triggerDistanceCM) {
    return;
  }

  if (autoTriggerReportPending) {
    printTriggerDebug("pending_hcsr04_trigger_retry", distanceCM, currentTime, false);
    return;
  }

  if (sequenceActive) {
    printTriggerDebug("blocked_by_sequence_active", distanceCM, currentTime, false);
    return;
  }

  if (!triggerArmed) {
    printTriggerDebug("blocked_by_trigger_not_armed", distanceCM, currentTime, false);
    return;
  }

  if (currentTime - lastTriggerTimeMS < cooldownMS) {
    printTriggerDebug("blocked_by_cooldown", distanceCM, currentTime, false);
    return;
  }

  Serial.println("HC-SR04 trigger reached. Queueing hcsr04_trigger report.");
  lastTriggerTimeMS = currentTime;
  triggerArmed = false;
  setAutoTriggerEnabled(false, "local_hcsr04_trigger");
  queueAutoTriggerReport();
  printTriggerDebug("queued_hcsr04_trigger", distanceCM, currentTime, true);
}

void pollCommand() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Command polling skipped: Wi-Fi is not connected.");
    return;
  }

  WiFiClientSecure requestClient;
  requestClient.setInsecure();

  HTTPClient http;
  http.begin(requestClient, commandUrl);
  http.setReuse(false);
  http.setTimeout(httpTimeoutMS);

  int httpCode = http.GET();
  if (httpCode <= 0) {
    Serial.print("Command GET failed: ");
    Serial.println(httpCode);
    Serial.print("Command GET error: ");
    Serial.println(httpErrorName(httpCode));
    registerCommandFailure(httpCode);
    http.end();
    requestClient.stop();
    return;
  }

  String body = http.getString();
  body.trim();
  http.end();
  requestClient.stop();

  if (httpCode < 200 || httpCode >= 300) {
    Serial.print("Command GET HTTP ");
    Serial.println(httpCode);
    Serial.println(body);
    return;
  }
  resetCommandFailures();

  MotorCommand command = parseCommand(body);
  setAutoTriggerEnabled(command.autoTriggerEnabled, command.serverStatus);
  if (command.command == "none" || command.commandId <= 0) {
    return;
  }
  if (command.commandId == lastConfirmedCommandId) {
    return;
  }

  handleCommand(command);
}

void handleCommand(const MotorCommand& command) {
  Serial.print("Command #");
  Serial.print(command.commandId);
  Serial.print(": ");
  Serial.print(command.command);
  Serial.print(" station ");
  Serial.println(command.stationIndex);

  if (command.command == "start_sequence") {
    if (autoTriggerReportPending) {
      clearAutoTriggerReportPending("start_sequence_command_received");
    }
    setAutoTriggerEnabled(false, "sequence_active");
    sequenceActive = true;
    moveAllGates(command.homeAngle);
    delay(command.servoSettleMs);
    delay(command.fruitSettleMs);
    queueReport("station_1_ready", 1, command.commandId, "start_sequence_station_1_ready");
    return;
  }

  if (command.command == "release_gate") {
    if (command.stationIndex < 1 || command.stationIndex > 3) {
      queueReport("motor_error", command.stationIndex, command.commandId, "invalid_station_index");
      return;
    }

    moveGate(command.stationIndex, command.releaseAngle);
    delay(command.servoSettleMs);

    if (command.stationIndex < 3) {
      delay(command.fruitSettleMs);
      queueReport(
        String("station_") + String(command.stationIndex + 1) + String("_ready"),
        command.stationIndex + 1,
        command.commandId,
        "next_station_ready"
      );
      return;
    }

    delay(command.fruitSettleMs);
    moveAllGates(command.homeAngle);
    delay(command.servoSettleMs);
    sequenceActive = false;
    queueReport("capture_sequence_finished", 0, command.commandId, "sequence_finished");
    return;
  }

  queueReport("motor_error", command.stationIndex, command.commandId, "unknown_command");
}

void setAutoTriggerEnabled(bool enabled, const String& serverStatus) {
  if (serverStatus.length() > 0) {
    lastServerStatus = serverStatus;
  }
  if (autoTriggerEnabled == enabled) {
    return;
  }
  autoTriggerEnabled = enabled;
  Serial.print("Auto trigger: ");
  Serial.print(autoTriggerEnabled ? "enabled" : "disabled");
  if (lastServerStatus.length() > 0) {
    Serial.print(" (server_status=");
    Serial.print(lastServerStatus);
    Serial.print(")");
  }
  Serial.println();
}

unsigned long currentCommandPollInterval() {
  if (sequenceActive || pendingReport || autoTriggerReportPending) {
    return activeCommandPollIntervalMS;
  }
  return commandPollIntervalMS;
}

void registerCommandFailure(int httpCode) {
  consecutiveCommandFailures += 1;
  unsigned long backoffMS = commandFailureBackoffMinMS;
  if (consecutiveCommandFailures >= 2) {
    backoffMS = commandFailureBackoffMaxMS;
  }
  commandBackoffUntilMS = millis() + backoffMS;

  Serial.print("Command polling backoff: ");
  Serial.print(backoffMS);
  Serial.print(" ms after ");
  Serial.print(consecutiveCommandFailures);
  Serial.println(" consecutive failure(s).");
}

void resetCommandFailures() {
  consecutiveCommandFailures = 0;
  commandBackoffUntilMS = 0;
}

const char* httpErrorName(int httpCode) {
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
      return "UNKNOWN";
  }
}

MotorCommand parseCommand(const String& body) {
  MotorCommand command;
  const String autoTriggerValue = readValue(body, "auto_trigger_enabled");
  command.command = readValue(body, "command");
  command.commandId = readValue(body, "command_id").toInt();
  command.stationIndex = readValue(body, "station_index").toInt();
  command.homeAngle = readValue(body, "home_angle").toInt();
  command.releaseAngle = readValue(body, "release_angle").toInt();
  command.servoSettleMs = readValue(body, "servo_settle_ms").toInt();
  command.fruitSettleMs = readValue(body, "fruit_settle_ms").toInt();
  command.autoTriggerEnabled = autoTriggerValue == "1" || autoTriggerValue == "true";
  command.serverStatus = readValue(body, "server_status");

  if (command.homeAngle == 0 && readValue(body, "home_angle") == "") {
    command.homeAngle = HOME_ANGLE;
  }
  if (command.releaseAngle == 0 && readValue(body, "release_angle") == "") {
    command.releaseAngle = RELEASE_ANGLE;
  }
  if (command.servoSettleMs <= 0) {
    command.servoSettleMs = servoSettleMS;
  }
  if (command.fruitSettleMs <= 0) {
    command.fruitSettleMs = fruitSettleMS;
  }
  return command;
}

String readValue(const String& body, const String& key) {
  const String prefix = key + "=";
  int start = body.indexOf(prefix);
  if (start < 0) {
    return "";
  }
  start += prefix.length();
  int end = body.indexOf('\n', start);
  if (end < 0) {
    end = body.length();
  }
  String value = body.substring(start, end);
  value.trim();
  return value;
}

bool postReport(const String& event, int stationIndex, int commandId, const String& message) {
  lastReportIgnored = false;
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Report skipped: Wi-Fi is not connected.");
    return false;
  }

  WiFiClientSecure requestClient;
  requestClient.setInsecure();

  HTTPClient http;
  http.begin(requestClient, reportUrl);
  http.setReuse(false);
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");
  http.setTimeout(httpTimeoutMS);

  String body = "event=" + event;
  body += "&station_index=" + String(stationIndex);
  body += "&command_id=" + String(commandId);
  body += "&message=" + message;

  int httpCode = http.POST(body);
  String response = "";
  if (httpCode > 0) {
    response = http.getString();
    response.trim();
  }
  http.end();
  requestClient.stop();

  Serial.print("Report ");
  Serial.print(event);
  Serial.print(" HTTP ");
  Serial.println(httpCode);
  if (response.length() > 0) {
    Serial.println(response);
  }
  lastReportIgnored = response.indexOf("\"ignored\": true") >= 0 || response.indexOf("\"ignored\":true") >= 0;

  return httpCode >= 200 && httpCode < 300;
}

void queueReport(const String& event, int stationIndex, int commandId, const String& message) {
  pendingReport = true;
  pendingEvent = event;
  pendingStationIndex = stationIndex;
  pendingCommandId = commandId;
  pendingMessage = message;
  lastReportRetryMS = 0;
}

void flushPendingReport(unsigned long currentTime) {
  if (!pendingReport) {
    return;
  }
  if (lastReportRetryMS != 0 && currentTime - lastReportRetryMS < reportRetryIntervalMS) {
    return;
  }
  lastReportRetryMS = currentTime;
  if (!postReport(pendingEvent, pendingStationIndex, pendingCommandId, pendingMessage)) {
    Serial.println("Report failed. It will be retried.");
    return;
  }

  if (pendingCommandId > 0) {
    lastConfirmedCommandId = pendingCommandId;
  }
  pendingReport = false;
  pendingEvent = "";
  pendingStationIndex = 0;
  pendingCommandId = 0;
  pendingMessage = "";
}

void queueAutoTriggerReport() {
  autoTriggerReportPending = true;
  lastAutoTriggerReportMS = 0;
}

void flushAutoTriggerReport(unsigned long currentTime) {
  if (!autoTriggerReportPending) {
    return;
  }
  if (lastAutoTriggerReportMS != 0 && currentTime - lastAutoTriggerReportMS < autoTriggerReportRetryIntervalMS) {
    return;
  }

  lastAutoTriggerReportMS = currentTime;
  if (!postReport("hcsr04_trigger", 0, 0, "distance_trigger")) {
    Serial.println("Auto trigger report failed. It will be retried.");
    return;
  }

  if (lastReportIgnored) {
    clearAutoTriggerReportPending("server_ignored_trigger");
    Serial.println("Auto trigger was ignored by server. Waiting for sensor area to clear.");
    return;
  }

  clearAutoTriggerReportPending("server_accepted_trigger");
  Serial.println("Auto trigger accepted. Waiting for start_sequence command.");
}

void clearAutoTriggerReportPending(const String& reason) {
  if (!autoTriggerReportPending) {
    return;
  }
  autoTriggerReportPending = false;
  lastAutoTriggerReportMS = 0;
  Serial.print("Auto trigger pending cleared: ");
  Serial.println(reason);
}

void printTriggerDebug(const String& reason, float distanceCM, unsigned long currentTime, bool force) {
  if (!force && lastTriggerDebugMS != 0 && currentTime - lastTriggerDebugMS < triggerDebugIntervalMS) {
    return;
  }
  lastTriggerDebugMS = currentTime;

  Serial.print("HC-SR04 trigger debug: reason=");
  Serial.print(reason);
  Serial.print(" distance=");
  Serial.print(distanceCM);
  Serial.print(" cm auto_trigger_enabled=");
  Serial.print(autoTriggerEnabled ? "1" : "0");
  Serial.print(" server_status=");
  if (lastServerStatus.length() > 0) {
    Serial.print(lastServerStatus);
  } else {
    Serial.print("unknown");
  }
  Serial.print(" trigger_armed=");
  Serial.print(triggerArmed ? "1" : "0");
  Serial.print(" sequence_active=");
  Serial.print(sequenceActive ? "1" : "0");
  Serial.print(" pending_hcsr04_trigger=");
  Serial.println(autoTriggerReportPending ? "1" : "0");
}

void moveGate(int stationIndex, int angle) {
  Servo* servo = servoForStation(stationIndex);
  if (servo == nullptr) {
    Serial.println("Invalid gate index.");
    return;
  }
  Serial.print("Gate ");
  Serial.print(stationIndex);
  Serial.print(" -> ");
  Serial.print(angle);
  Serial.println(" degrees");
  servo->write(angle);
}

void moveAllGates(int angle) {
  gate1Servo.write(angle);
  gate2Servo.write(angle);
  gate3Servo.write(angle);
}

Servo* servoForStation(int stationIndex) {
  if (stationIndex == 1) {
    return &gate1Servo;
  }
  if (stationIndex == 2) {
    return &gate2Servo;
  }
  if (stationIndex == 3) {
    return &gate3Servo;
  }
  return nullptr;
}
