#include "DjangoApiClient.h"

#include <ctype.h>

#include "Config.h"
#include "secrets.h"

namespace {

const char* kResponseHeaders[] = {"Connection"};

}  // namespace

DjangoApiClient::DjangoApiClient()
    : wifiAttemptInProgress_(false),
      wifiWasConnected_(false),
      wifiAttemptStartedAt_(0),
      nextWiFiAttemptAt_(0),
      activeOrigin_("") {}

void DjangoApiClient::begin() {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);

  secureClient_.setInsecure();
  bootId_ = WiFi.macAddress() + "-" + String(millis(), HEX);
  startWiFiAttempt(millis());
}

bool DjangoApiClient::ensureWiFi(uint32_t currentTime) {
  if (WiFi.status() == WL_CONNECTED) {
    if (!wifiWasConnected_) {
      Serial.print("Wi-Fi connected. IP: ");
      Serial.println(WiFi.localIP());
    }
    wifiAttemptInProgress_ = false;
    wifiWasConnected_ = true;
    return true;
  }

  if (wifiWasConnected_) {
    Serial.println("Wi-Fi disconnected. Scheduling non-blocking reconnect.");
    closeConnection();
  }
  wifiWasConnected_ = false;

  if (
      wifiAttemptInProgress_ &&
      !timeReached(
          currentTime,
          wifiAttemptStartedAt_ + FirmwareConfig::kWifiConnectAttemptTimeoutMS)) {
    return false;
  }

  if (!wifiAttemptInProgress_ && !timeReached(currentTime, nextWiFiAttemptAt_)) {
    return false;
  }

  if (wifiAttemptInProgress_) {
    Serial.println("Wi-Fi connection attempt timed out. Retrying asynchronously.");
    wifiAttemptInProgress_ = false;
    nextWiFiAttemptAt_ = currentTime + FirmwareConfig::kWifiReconnectIntervalMS;
    return false;
  }

  startWiFiAttempt(currentTime);
  return false;
}

bool DjangoApiClient::wifiConnected() const {
  return WiFi.status() == WL_CONNECTED;
}

void DjangoApiClient::closeConnection() {
  secureClient_.stop();
  activeOrigin_ = "";
}

HttpResult DjangoApiClient::pollCommand(
    const String& feederState,
    int lastFeedCommandId) {
  String url(commandUrl);
  url += url.indexOf('?') >= 0 ? "&" : "?";
  url += "boot_id=" + encodeFormValue(bootId_);
  url += "&capability=feeder_v1";
  url += "&feeder_state=" + encodeFormValue(feederState);
  url += "&last_feed_command_id=" + String(lastFeedCommandId);
  return executeGet(url.c_str(), FirmwareConfig::kCommandHttpTimeoutMS);
}

HttpResult DjangoApiClient::postReport(
    const String& event,
    int stationIndex,
    int commandId,
    const String& message,
    uint32_t timeoutMS,
    const String& triggerId,
    bool includeStationSafetyFields,
    bool gatesHome,
    bool stationSettled,
    uint32_t timingRevision,
    const String& classificationCode,
    bool includeStationIndex) {
  String body;
  body.reserve(160 + ((event.length() + message.length() + triggerId.length()) * 3));
  body = "event=" + encodeFormValue(event);
  if (includeStationIndex) {
    body += "&station_index=" + String(stationIndex);
  }
  body += "&command_id=" + String(commandId);
  body += "&message=" + encodeFormValue(message);

  if (classificationCode.length() > 0) {
    body += "&classification_code=" + encodeFormValue(classificationCode);
  }

  if (triggerId.length() > 0) {
    body += "&trigger_id=" + encodeFormValue(triggerId);
  }
  if (includeStationSafetyFields) {
    body += "&gates_home=" + String(gatesHome ? 1 : 0);
    body += "&station_settled=" + String(stationSettled ? 1 : 0);
  }
  if (timingRevision > 0) {
    body += "&timing_revision=" + String(timingRevision);
  }

  return executePost(reportUrl, body, timeoutMS);
}

MotorCommand DjangoApiClient::parseCommandText(const String& body) const {
  MotorCommand command;
  const String autoTriggerValue = readTextValue(body, "auto_trigger_enabled");
  const String homeAngleValue = readTextValue(body, "home_angle");
  const String releaseAngleValue = readTextValue(body, "release_angle");
  const String servoSettleValue = readTextValue(body, "servo_settle_ms");
  const String fruitSettleValue = readTextValue(body, "fruit_settle_ms");
  const String timingRevisionValue = readTextValue(body, "timing_revision");
  const String firstStationSettleValue =
      readTextValue(body, "first_station_settle_ms");
  const String finalGateReturnDelayValue =
      readTextValue(body, "final_gate_return_delay_ms");
  const String idleCommandPollIntervalValue =
      readTextValue(body, "idle_command_poll_interval_ms");
  const String feederStopValue = readTextValue(body, "feeder_stop_us");
  const String feederDriveValue = readTextValue(body, "feeder_drive_us");
  const String feederRunValue = readTextValue(body, "feeder_run_ms");
  const String fruitArrivalWarningValue =
      readTextValue(body, "fruit_arrival_warning_ms");

  command.command = readTextValue(body, "command");
  command.commandId = readTextValue(body, "command_id").toInt();
  command.stationIndex = readTextValue(body, "station_index").toInt();
  command.homeAngle = homeAngleValue.toInt();
  command.releaseAngle = releaseAngleValue.toInt();
  command.autoTriggerEnabled = autoTriggerValue == "1" || autoTriggerValue == "true";
  command.hasAutoTriggerEnabled = autoTriggerValue.length() > 0;
  command.serverStatus = readTextValue(body, "server_status");
  command.classificationCode = readTextValue(body, "classification_code");
  command.feedContext = readTextValue(body, "feed_context");

  if (command.command.length() == 0) {
    command.command = "none";
  }
  if (homeAngleValue.length() == 0) {
    command.homeAngle = FirmwareConfig::kHomeAngle;
  }
  if (releaseAngleValue.length() == 0) {
    command.releaseAngle = FirmwareConfig::kReleaseAngle;
  }
  if (timingRevisionValue.length() > 0 &&
      firstStationSettleValue.length() > 0 &&
      servoSettleValue.length() > 0 &&
      fruitSettleValue.length() > 0 &&
      finalGateReturnDelayValue.length() > 0 &&
      idleCommandPollIntervalValue.length() > 0 &&
      feederStopValue.length() > 0 &&
      feederDriveValue.length() > 0 &&
      feederRunValue.length() > 0 &&
      fruitArrivalWarningValue.length() > 0) {
    const long revision = timingRevisionValue.toInt();
    const long firstStationSettleMS = firstStationSettleValue.toInt();
    const long servoSettleMS = servoSettleValue.toInt();
    const long fruitSettleMS = fruitSettleValue.toInt();
    const long finalGateReturnDelayMS = finalGateReturnDelayValue.toInt();
    const long idleCommandPollIntervalMS = idleCommandPollIntervalValue.toInt();
    const long feederStopUS = feederStopValue.toInt();
    const long feederDriveUS = feederDriveValue.toInt();
    const long feederRunMS = feederRunValue.toInt();
    const long fruitArrivalWarningMS = fruitArrivalWarningValue.toInt();
    if (revision >= 0 && firstStationSettleMS > 0 && servoSettleMS > 0 &&
        fruitSettleMS > 0 && finalGateReturnDelayMS >= 0 &&
        idleCommandPollIntervalMS > 0 && feederStopUS > 0 &&
        feederDriveUS > 0 && feederRunMS > 0 && fruitArrivalWarningMS > 0) {
      command.timing.revision = static_cast<uint32_t>(revision);
      command.timing.firstStationSettleMS =
          static_cast<uint32_t>(firstStationSettleMS);
      command.timing.servoSettleMS = static_cast<uint32_t>(servoSettleMS);
      command.timing.fruitSettleMS = static_cast<uint32_t>(fruitSettleMS);
      command.timing.finalGateReturnDelayMS =
          static_cast<uint32_t>(finalGateReturnDelayMS);
      command.timing.idleCommandPollIntervalMS =
          static_cast<uint32_t>(idleCommandPollIntervalMS);
      command.timing.feederStopUS = static_cast<uint32_t>(feederStopUS);
      command.timing.feederDriveUS = static_cast<uint32_t>(feederDriveUS);
      command.timing.feederRunMS = static_cast<uint32_t>(feederRunMS);
      command.timing.fruitArrivalWarningMS =
          static_cast<uint32_t>(fruitArrivalWarningMS);
      command.hasTimingConfig = true;
    }
  }
  return command;
}

bool DjangoApiClient::parseStartSequenceFromResponse(
    const String& body,
    MotorCommand& command) const {
  const String motorCommand = extractJsonObject(body, "motor_command");
  if (motorCommand.length() == 0) {
    return false;
  }

  if (readJsonString(motorCommand, "command") != "start_sequence") {
    return false;
  }

  const int commandId = readJsonInt(motorCommand, "command_id", 0);
  if (commandId <= 0) {
    return false;
  }

  command.command = "start_sequence";
  command.commandId = commandId;
  command.stationIndex = readJsonInt(motorCommand, "station_index", 1);
  command.homeAngle = readJsonInt(
      motorCommand, "home_angle", FirmwareConfig::kHomeAngle);
  command.releaseAngle = readJsonInt(
      motorCommand, "release_angle", FirmwareConfig::kReleaseAngle);
  int servoSettleMS = readJsonInt(
      motorCommand, "servo_settle_ms", FirmwareConfig::kServoSettleMS);
  int fruitSettleMS = readJsonInt(
      motorCommand, "fruit_settle_ms", FirmwareConfig::kFruitSettleMS);
  const int timingRevision = readJsonInt(motorCommand, "timing_revision", -1);
  const int firstStationSettleMS = readJsonInt(
      motorCommand, "first_station_settle_ms", -1);
  const int finalGateReturnDelayMS = readJsonInt(
      motorCommand, "final_gate_return_delay_ms", -1);
  const int idleCommandPollIntervalMS = readJsonInt(
      motorCommand, "idle_command_poll_interval_ms", -1);
  const int feederStopUS = readJsonInt(motorCommand, "feeder_stop_us", -1);
  const int feederDriveUS = readJsonInt(motorCommand, "feeder_drive_us", -1);
  const int feederRunMS = readJsonInt(motorCommand, "feeder_run_ms", -1);
  const int fruitArrivalWarningMS = readJsonInt(
      motorCommand, "fruit_arrival_warning_ms", -1);
  command.serverStatus = "waiting_esp32_start";

  if (command.stationIndex <= 0) {
    command.stationIndex = 1;
  }
  if (servoSettleMS <= 0) {
    servoSettleMS = FirmwareConfig::kServoSettleMS;
  }
  if (fruitSettleMS <= 0) {
    fruitSettleMS = FirmwareConfig::kFruitSettleMS;
  }
  if (timingRevision >= 0 && firstStationSettleMS > 0 &&
      servoSettleMS > 0 && fruitSettleMS > 0 &&
      finalGateReturnDelayMS >= 0 && idleCommandPollIntervalMS > 0 &&
      feederStopUS > 0 && feederDriveUS > 0 && feederRunMS > 0 &&
      fruitArrivalWarningMS > 0) {
    command.timing.revision = static_cast<uint32_t>(timingRevision);
    command.timing.firstStationSettleMS =
        static_cast<uint32_t>(firstStationSettleMS);
    command.timing.servoSettleMS =
        static_cast<uint32_t>(servoSettleMS);
    command.timing.fruitSettleMS =
        static_cast<uint32_t>(fruitSettleMS);
    command.timing.finalGateReturnDelayMS =
        static_cast<uint32_t>(finalGateReturnDelayMS);
    command.timing.idleCommandPollIntervalMS =
        static_cast<uint32_t>(idleCommandPollIntervalMS);
    command.timing.feederStopUS = static_cast<uint32_t>(feederStopUS);
    command.timing.feederDriveUS = static_cast<uint32_t>(feederDriveUS);
    command.timing.feederRunMS = static_cast<uint32_t>(feederRunMS);
    command.timing.fruitArrivalWarningMS =
        static_cast<uint32_t>(fruitArrivalWarningMS);
    command.hasTimingConfig = true;
  }
  return true;
}

bool DjangoApiClient::responseHasIgnored(const String& response) {
  return readJsonBool(response, "ignored", false);
}

bool DjangoApiClient::responseHasCaptureRequest(const String& response) {
  return readJsonBool(response, "capture_requested", false);
}

bool DjangoApiClient::responseSuggestsStartSequenceWaiting(const String& response) {
  const String reason = readJsonString(response, "reason");
  if (reason == "duplicate_trigger_waiting_start_sequence") {
    return true;
  }
  return response.indexOf("waiting_esp32_start") >= 0 &&
         response.indexOf("start_sequence") >= 0;
}

bool DjangoApiClient::isExplicitFastPathRejection(const HttpResult& response) {
  // A 4xx protocol response is deterministic rather than an ambiguous
  // transport timeout. This lets a newly flashed controller safely fall back
  // when it reaches an older Django server that does not know the fast event.
  if (response.statusCode == 400 || response.statusCode == 404 ||
      response.statusCode == 405 || response.statusCode == 422) {
    return true;
  }
  if (readJsonBool(response.body, "fallback_to_legacy", false)) {
    return true;
  }

  const String reason = readJsonString(response.body, "reason");
  return reason == "fast_path_unsupported" ||
         reason == "fast_path_not_available" ||
         reason == "hcsr04_station_1_ready_not_supported" ||
         reason == "unknown_event" || reason == "invalid_event";
}

String DjangoApiClient::readJsonString(const String& body, const String& key) {
  const String pattern = "\"" + key + "\"";
  const int keyIndex = body.indexOf(pattern);
  if (keyIndex < 0) {
    return "";
  }

  const int colonIndex = body.indexOf(':', keyIndex + pattern.length());
  if (colonIndex < 0) {
    return "";
  }

  const int valueStart = body.indexOf('"', colonIndex + 1);
  if (valueStart < 0) {
    return "";
  }
  const int valueEnd = body.indexOf('"', valueStart + 1);
  if (valueEnd < 0) {
    return "";
  }
  return body.substring(valueStart + 1, valueEnd);
}

int DjangoApiClient::readJsonInt(
    const String& body,
    const String& key,
    int defaultValue) {
  const String pattern = "\"" + key + "\"";
  const int keyIndex = body.indexOf(pattern);
  if (keyIndex < 0) {
    return defaultValue;
  }

  const int colonIndex = body.indexOf(':', keyIndex + pattern.length());
  if (colonIndex < 0) {
    return defaultValue;
  }

  int index = colonIndex + 1;
  while (index < body.length()) {
    const char current = body.charAt(index);
    if (current != ' ' && current != '"' && current != '\t') {
      break;
    }
    index += 1;
  }

  int sign = 1;
  if (index < body.length() && body.charAt(index) == '-') {
    sign = -1;
    index += 1;
  }

  long value = 0;
  bool hasDigit = false;
  while (index < body.length()) {
    const char current = body.charAt(index);
    if (current < '0' || current > '9') {
      break;
    }
    hasDigit = true;
    value = (value * 10L) + static_cast<long>(current - '0');
    index += 1;
  }

  return hasDigit ? static_cast<int>(value * sign) : defaultValue;
}

void DjangoApiClient::startWiFiAttempt(uint32_t currentTime) {
  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  Serial.println("Starting non-blocking Wi-Fi connection attempt.");
  WiFi.begin(ssid, password);
  wifiAttemptInProgress_ = true;
  wifiAttemptStartedAt_ = currentTime;
  nextWiFiAttemptAt_ = currentTime + FirmwareConfig::kWifiReconnectIntervalMS;
}

HttpResult DjangoApiClient::executeGet(const char* url, uint32_t timeoutMS) {
  HttpResult result;
  const uint32_t startedAt = millis();
  if (!wifiConnected()) {
    result.statusCode = -4;
    result.elapsedMS = millis() - startedAt;
    return result;
  }
  if (!prepareRequest(url, timeoutMS)) {
    result.statusCode = -1;
    result.elapsedMS = millis() - startedAt;
    closeConnection();
    return result;
  }

  result.statusCode = http_.GET();
  if (result.statusCode > 0) {
    result.body = http_.getString();
    result.body.trim();
  }
  result.elapsedMS = millis() - startedAt;
  finishRequest(result);
  return result;
}

HttpResult DjangoApiClient::executePost(
    const char* url,
    const String& body,
    uint32_t timeoutMS) {
  HttpResult result;
  const uint32_t startedAt = millis();
  if (!wifiConnected()) {
    result.statusCode = -4;
    result.elapsedMS = millis() - startedAt;
    return result;
  }
  if (!prepareRequest(url, timeoutMS)) {
    result.statusCode = -1;
    result.elapsedMS = millis() - startedAt;
    closeConnection();
    return result;
  }

  http_.addHeader("Content-Type", "application/x-www-form-urlencoded");
  result.statusCode = http_.POST(body);
  if (result.statusCode > 0) {
    result.body = http_.getString();
    result.body.trim();
  }
  result.elapsedMS = millis() - startedAt;
  finishRequest(result);
  return result;
}

bool DjangoApiClient::prepareRequest(const char* url, uint32_t timeoutMS) {
  const String requestOrigin = urlOrigin(url);
  if (activeOrigin_.length() > 0 && activeOrigin_ != requestOrigin) {
    closeConnection();
  }

  secureClient_.setInsecure();
  secureClient_.setTimeout(timeoutMS);
  secureClient_.setHandshakeTimeout((timeoutMS + 999UL) / 1000UL);

  if (!http_.begin(secureClient_, url)) {
    return false;
  }

  http_.setReuse(true);
  http_.useHTTP10(false);
  // Use the operation deadline for both TLS/HTTP connection setup and body
  // reads. A shorter independent connect timeout can reject a healthy cold
  // TLS handshake before the documented request timeout expires.
  http_.setConnectTimeout(timeoutMS);
  http_.setTimeout(timeoutMS);
  http_.collectHeaders(kResponseHeaders, 1);
  http_.addHeader("Connection", "keep-alive");
  activeOrigin_ = requestOrigin;
  return true;
}

void DjangoApiClient::finishRequest(HttpResult& result) {
  String connectionHeader = http_.header("Connection");
  connectionHeader.toLowerCase();
  const bool serverClosed = connectionHeader.indexOf("close") >= 0;
  const bool transportFailed = result.statusCode <= 0;
  const bool socketClosed = !secureClient_.connected();

  http_.end();

  if (serverClosed || transportFailed || socketClosed) {
    closeConnection();
  }
}

bool DjangoApiClient::timeReached(uint32_t currentTime, uint32_t deadline) {
  return static_cast<int32_t>(currentTime - deadline) >= 0;
}

String DjangoApiClient::urlOrigin(const char* url) {
  const String value(url);
  const int schemeEnd = value.indexOf("://");
  const int hostStart = schemeEnd >= 0 ? schemeEnd + 3 : 0;
  const int pathStart = value.indexOf('/', hostStart);
  return pathStart >= 0 ? value.substring(0, pathStart) : value;
}

String DjangoApiClient::encodeFormValue(const String& value) {
  const char hexDigits[] = "0123456789ABCDEF";
  String encoded;
  encoded.reserve(value.length() * 3);

  for (uint16_t index = 0; index < value.length(); index += 1) {
    const uint8_t current = static_cast<uint8_t>(value.charAt(index));
    if (isalnum(current) || current == '-' || current == '_' || current == '.') {
      encoded += static_cast<char>(current);
      continue;
    }
    encoded += '%';
    encoded += hexDigits[(current >> 4) & 0x0F];
    encoded += hexDigits[current & 0x0F];
  }
  return encoded;
}

String DjangoApiClient::readTextValue(const String& body, const String& key) {
  const String prefix = key + "=";
  const int startIndex = body.indexOf(prefix);
  if (startIndex < 0) {
    return "";
  }

  const int valueStart = startIndex + prefix.length();
  int valueEnd = body.indexOf('\n', valueStart);
  if (valueEnd < 0) {
    valueEnd = body.length();
  }
  String value = body.substring(valueStart, valueEnd);
  value.trim();
  return value;
}

String DjangoApiClient::extractJsonObject(
    const String& body,
    const String& objectKey) {
  const String pattern = "\"" + objectKey + "\"";
  const int keyIndex = body.indexOf(pattern);
  if (keyIndex < 0) {
    return "";
  }

  const int objectStart = body.indexOf('{', keyIndex + pattern.length());
  if (objectStart < 0) {
    return "";
  }

  int depth = 0;
  for (int index = objectStart; index < body.length(); index += 1) {
    const char current = body.charAt(index);
    if (current == '{') {
      depth += 1;
    } else if (current == '}') {
      depth -= 1;
      if (depth == 0) {
        return body.substring(objectStart, index + 1);
      }
    }
  }
  return "";
}

bool DjangoApiClient::readJsonBool(
    const String& body,
    const String& key,
    bool defaultValue) {
  const String pattern = "\"" + key + "\"";
  const int keyIndex = body.indexOf(pattern);
  if (keyIndex < 0) {
    return defaultValue;
  }

  const int colonIndex = body.indexOf(':', keyIndex + pattern.length());
  if (colonIndex < 0) {
    return defaultValue;
  }

  int index = colonIndex + 1;
  while (index < body.length() &&
         (body.charAt(index) == ' ' || body.charAt(index) == '\t')) {
    index += 1;
  }
  if (body.startsWith("true", index) || body.startsWith("1", index)) {
    return true;
  }
  if (body.startsWith("false", index) || body.startsWith("0", index)) {
    return false;
  }
  return defaultValue;
}
