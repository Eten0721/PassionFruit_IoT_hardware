#include "DjangoApiClient.h"

#include <ctype.h>
#include <ESPmDNS.h>

#if __has_include("network.local.h")
#include "network.local.h"
#else
const char* manualDjangoOrigin = "";
#endif
#include <esp_system.h>

#include "Config.h"
#include "secrets.h"

namespace {

const char* kResponseHeaders[] = {"Connection"};

bool decimal(const String& value) {
  if (value.length() == 0 || value.length() > 9) return false;
  for (unsigned int i = 0; i < value.length(); ++i) {
    if (!isdigit(static_cast<unsigned char>(value.charAt(i)))) return false;
  }
  return value.toInt() > 0;
}

bool runIdentity(const String& value) {
  if (value.length() != 36) return false;
  for (unsigned int i = 0; i < value.length(); ++i) {
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (value.charAt(i) != '-') return false;
    } else if (!isxdigit(static_cast<unsigned char>(value.charAt(i)))) return false;
  }
  return true;
}

}  // namespace

DjangoApiClient::DjangoApiClient()
    : wifiAttemptInProgress_(false),
      wifiWasConnected_(false),
      wifiAttemptStartedAt_(0),
      nextWiFiAttemptAt_(0),
      activeOrigin_("") {}

void DjangoApiClient::begin() {
  if (workerTask_ != nullptr) {
    return;
  }
  if (xTaskCreate(workerEntry, "django-network", 8192, this, 1, &workerTask_) != pdPASS) {
    workerTask_ = nullptr;
    Serial.println("Network worker initialization failed: network_worker_unavailable");
  }
}

DjangoApiClient::~DjangoApiClient() {
  if (workerTask_ == nullptr) {
    return;
  }
  stopping_.store(true, std::memory_order_release);
  while (!stopped_.load(std::memory_order_acquire)) {
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void DjangoApiClient::workerEntry(void* context) {
  static_cast<DjangoApiClient*>(context)->runWorker();
  vTaskDelete(nullptr);
}

void DjangoApiClient::runWorker() {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);

  secureClient_.setInsecure();
  bootId_ = WiFi.macAddress() + "-" + String(esp_random(), HEX);
  startWiFiAttempt(millis());

  while (!stopping_.load(std::memory_order_acquire)) {
    ensureWiFi(millis());
    if (!wifiConnected() || !ensureController(millis())) {
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    if (FirmwareConfig::kNetworkDiagnosticsOnly) {
      if (timeReached(millis(), nextProbeAt_)) probeController();
      vTaskDelay(pdMS_TO_TICKS(10));
      continue;
    }
    if (requestState_.load(std::memory_order_acquire) == RequestState::kQueued) {
      requestState_.store(RequestState::kRunning, std::memory_order_relaxed);
      const String identity = networkIdentity() + "&network_capability=local_network_v1&" + request_.controlState;
      const String run = serverRunId_;
      const uint32_t revision = networkRevision_;
      result_ = request_.post
          ? executePost((controllerOrigin_ + request_.url).c_str(), request_.body + "&" + identity, request_.timeoutMS)
          : executeGet((controllerOrigin_ + request_.url + "&" + identity).c_str(), request_.timeoutMS);
      result_.sentAt = request_.sentAt;
      result_.generation = request_.generation;
      result_.serverRunId = run;
      result_.networkRevision = revision;
      result_.bootId = bootId_;
      if (result_.isSuccess() && controlValue(result_.body, "server_run_id") == run
          && controlValue(result_.body, "network_revision") == String(revision)) {
        lastProbeSuccessAt_ = millis();
      } else if (millis() - lastProbeSuccessAt_ >= 6000UL) {
        resetController(millis());
      }
      requestState_.store(RequestState::kCompleted, std::memory_order_release);
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  closeConnection();
  if (mdnsStarted_) MDNS.end();
  stopped_.store(true, std::memory_order_release);
}


void DjangoApiClient::resetController(uint32_t currentTime) {
  closeConnection();
  controllerOrigin_ = "";
  serverRunId_ = "";
  networkRevision_ = 0;
  initialDiscoveryComplete_ = false;
  conflictDetected_ = false;
  manualAttempted_ = false;
  discoveryStartedAt_ = currentTime;
  nextDiscoveryAt_ = currentTime;
  nextProbeAt_ = currentTime;
  lastProbeSuccessAt_ = currentTime;
}

bool DjangoApiClient::validOrigin(const String& origin) {
  if (!origin.startsWith("https://")) return false;
  const String hostPort = origin.substring(8);
  const int colon = hostPort.indexOf(':');
  const String host = colon < 0 ? hostPort : hostPort.substring(0, colon);
  const String port = colon < 0 ? String("443") : hostPort.substring(colon + 1);
  IPAddress address;
  if (!address.fromString(host) || address.toString() != host || !decimal(port) || port.toInt() > 65535) return false;
  if (address[0] == 0 || address[0] == 127 || address[0] >= 224 ||
      (address[0] == 169 && address[1] == 254)) return false;
  const uint32_t mask = static_cast<uint32_t>(WiFi.subnetMask());
  const uint32_t ip = static_cast<uint32_t>(address);
  return mask != 0 && (ip & mask) == (static_cast<uint32_t>(WiFi.localIP()) & mask)
      && (ip & ~mask) != 0 && (ip & ~mask) != ~mask;
}

bool DjangoApiClient::validateController(
    const String& origin, const String& runId, const String& revision, uint32_t timeoutMS) {
  if (!validOrigin(origin)) {
    discoveryReason_ = "network_info_invalid";
    return false;
  }
  const HttpResult info = executeGet((origin + "/api/network/info/?format=text").c_str(), timeoutMS);
  if (!info.isSuccess()) {
    discoveryReason_ = "network_info_unavailable";
    return false;
  }
  const String actualRun = readTextValue(info.body, "server_run_id");
  const String actualRevision = readTextValue(info.body, "network_revision");
  if (readTextValue(info.body, "api_version") != "1" ||
      readTextValue(info.body, "scheme") != "https" ||
      readTextValue(info.body, "selected") != "1" ||
      readTextValue(info.body, "server_url") != origin ||
      readTextValue(info.body, "camera_url") != origin + "/camera/" ||
      !runIdentity(actualRun) || !decimal(actualRevision) ||
      (runId.length() > 0 && (runId != actualRun || revision != actualRevision))) {
    discoveryReason_ = "network_info_invalid";
    return false;
  }
  controllerOrigin_ = origin;
  serverRunId_ = actualRun;
  networkRevision_ = actualRevision.toInt();
  return true;
}

bool DjangoApiClient::ensureController(uint32_t currentTime) {
  if (controllerOrigin_.length() > 0) {
    if (discoverySource_ == "manual" && timeReached(currentTime, nextDiscoveryAt_)) {
      // Healthy manual controllers stay selected while bounded mDNS browsing continues.
      if (!mdnsStarted_) mdnsStarted_ = MDNS.begin("pf-esp32-" + String(esp_random(), HEX));
      if (mdnsStarted_) MDNS.queryService("pf-iot", "tcp");
      nextDiscoveryAt_ = millis() + FirmwareConfig::kDiscoveryRetryMS;
    }
    return true;
  }
  if (!timeReached(currentTime, nextDiscoveryAt_)) return false;
  if (!manualAttempted_ && !conflictDetected_ &&
      currentTime - discoveryStartedAt_ >= FirmwareConfig::kDiscoveryWindowMS) {
    initialDiscoveryComplete_ = true;
    manualAttempted_ = true;
    nextDiscoveryAt_ = currentTime + FirmwareConfig::kDiscoveryRetryMS;
    return tryManualController();
  }
  nextDiscoveryAt_ = currentTime + FirmwareConfig::kDiscoveryRetryMS;
  if (!mdnsStarted_) mdnsStarted_ = MDNS.begin("pf-esp32-" + String(esp_random(), HEX));
  const uint32_t elapsed = currentTime - discoveryStartedAt_;
  const bool firstWindowExpired = !initialDiscoveryComplete_ && elapsed >= FirmwareConfig::kDiscoveryWindowMS;
  const bool finishInitialWithoutQuery = firstWindowExpired && !conflictDetected_;
  if (firstWindowExpired) initialDiscoveryComplete_ = true;
  const uint32_t deadline = initialDiscoveryComplete_
      ? currentTime + FirmwareConfig::kDiscoveryWindowMS
      : discoveryStartedAt_ + FirmwareConfig::kDiscoveryWindowMS;
  if (!initialDiscoveryComplete_ && !firstWindowExpired && deadline - currentTime < 3000UL) {
    nextDiscoveryAt_ = deadline;
    return false;
  }
  const int count = mdnsStarted_ && !finishInitialWithoutQuery ? MDNS.queryService("pf-iot", "tcp") : 0;
  discoveryReason_ = mdnsStarted_ ? "controller_not_found" : "network_discovery_unavailable";
  String chosenOrigin, chosenRun;
  uint32_t chosenRevision = 0;
  int valid = 0;
  // queryService in core 3.3.11 is bounded to 3 s and 20 answers.
  for (int i = 0; i < count; ++i) {
    if (timeReached(millis(), deadline)) {
      discoveryReason_ = "discovery_timeout";
      controllerOrigin_ = "";
      return false;
    }
    if (MDNS.txt(i, "api_version") != "1" || MDNS.txt(i, "scheme") != "https" ||
        MDNS.txt(i, "info_path") != "/api/network/info/" ||
        !runIdentity(MDNS.txt(i, "server_run_id")) || !decimal(MDNS.txt(i, "network_revision"))) continue;
    const String origin = "https://" + MDNS.address(i).toString() + ":" + String(MDNS.port(i));
    const uint32_t validationStartedAt = millis();
    if (timeReached(validationStartedAt, deadline)) {
      controllerOrigin_ = "";
      discoveryReason_ = "discovery_timeout";
      return false;
    }
    // Reserve TCP + TLS + header/body waits before starting another validation.
    if (deadline - validationStartedAt < 7000UL) {
      nextDiscoveryAt_ = deadline;
      discoveryReason_ = "discovery_timeout";
      controllerOrigin_ = "";
      return false;
    }
    secureClient_.setDiscoveryDeadline(deadline);
    const bool verified = validateController(origin, MDNS.txt(i, "server_run_id"), MDNS.txt(i, "network_revision"),
                                             FirmwareConfig::kCommandHttpTimeoutMS);
    secureClient_.clearDiscoveryDeadline();
    if (!verified) {
      if (timeReached(millis(), deadline)) {
        initialDiscoveryComplete_ = true;
        controllerOrigin_ = "";
        discoveryReason_ = "discovery_timeout";
        return false;
      }
      continue;
    }
    if (timeReached(millis(), deadline)) {
      controllerOrigin_ = "";
      discoveryReason_ = "discovery_timeout";
      return false;
    }
    if (origin == chosenOrigin && serverRunId_ == chosenRun) continue;
    ++valid;
    if (valid > 1) {
      conflictDetected_ = true;
      initialDiscoveryComplete_ = true;
      const String secondOrigin = controllerOrigin_;
      const String secondIdentity = networkIdentity();
      const String firstIdentity = "boot_id=" + encodeFormValue(bootId_) +
          "&server_run_id=" + encodeFormValue(chosenRun) + "&network_revision=" + String(chosenRevision);
      controllerOrigin_ = "";
      discoveryReason_ = "multiple_controllers_found";
      const String observation = "event=network_discovery_status&discovery_reason=multiple_controllers_found&";
      executePost((chosenOrigin + "/api/esp32/report/").c_str(), observation + firstIdentity,
                  FirmwareConfig::kCommandHttpTimeoutMS);
      executePost((secondOrigin + "/api/esp32/report/").c_str(), observation + secondIdentity,
                  FirmwareConfig::kCommandHttpTimeoutMS);
      Serial.println(discoveryReason_);
      return false;
    }
    chosenOrigin = origin;
    chosenRun = serverRunId_;
    chosenRevision = networkRevision_;
  }
  conflictDetected_ = false;
  controllerOrigin_ = chosenOrigin;
  if (valid == 1) {
    serverRunId_ = chosenRun;
    networkRevision_ = chosenRevision;
    discoverySource_ = "mdns";
    discoveryReason_ = "";
    lastProbeSuccessAt_ = millis();
    Serial.println("Controller discovered: " + controllerOrigin_);
    return true;
  }
  if (millis() - discoveryStartedAt_ < FirmwareConfig::kDiscoveryWindowMS) return false;
  initialDiscoveryComplete_ = true;
  manualAttempted_ = true;
  return tryManualController();
}

bool DjangoApiClient::tryManualController() {
  if (String(manualDjangoOrigin).length() > 0 &&
      validateController(manualDjangoOrigin, "", "", FirmwareConfig::kCommandHttpTimeoutMS)) {
    discoverySource_ = "manual";
    lastProbeSuccessAt_ = millis();
    Serial.println("Manual controller verified: " + controllerOrigin_);
    return true;
  }
  Serial.println("Controller discovery: " + discoveryReason_);
  return false;
}

String DjangoApiClient::networkIdentity() const {
  return "boot_id=" + encodeFormValue(bootId_) + "&server_run_id=" + encodeFormValue(serverRunId_) +
      "&network_revision=" + String(networkRevision_);
}

void DjangoApiClient::probeController() {
  const String identity = networkIdentity();
  const String url = controllerOrigin_ + "/api/esp32/command/?diagnostic=1&" + identity +
      "&discovery_source=" + discoverySource_ + "&discovery_reason=" + discoveryReason_;
  const HttpResult command = executeGet(url.c_str(), FirmwareConfig::kCommandHttpTimeoutMS);
  const String id = readTextValue(command.body, "diagnostic_id");
  bool ok = command.isSuccess() && runIdentity(id) &&
      readTextValue(command.body, "server_run_id") == serverRunId_ &&
      readTextValue(command.body, "network_revision") == String(networkRevision_) &&
      readTextValue(command.body, "diagnostic") == "1" &&
      readTextValue(command.body, "control_allowed") == "0" &&
      readTextValue(command.body, "command") == "none";
  if (ok) {
    const HttpResult report = executePost((controllerOrigin_ + "/api/esp32/report/").c_str(),
        "event=network_probe&" + identity + "&diagnostic_id=" + encodeFormValue(id),
        FirmwareConfig::kCommandHttpTimeoutMS);
    ok = report.isSuccess() && readTextValue(report.body, "diagnostic_id") == id &&
        readTextValue(report.body, "server_run_id") == serverRunId_ &&
        readTextValue(report.body, "network_revision") == String(networkRevision_) &&
        readTextValue(report.body, "control_allowed") == "0";
  }
  if (ok) lastProbeSuccessAt_ = millis();
  else Serial.println("Controller diagnostic failed: network_probe_failed");
  if (!ok && millis() - lastProbeSuccessAt_ >= 6000UL) resetController(millis());
  nextProbeAt_ = millis() + FirmwareConfig::kNetworkProbeIntervalMS;
}

bool DjangoApiClient::submit(
    const String& url, const String& body, uint32_t timeoutMS, bool post) {
  if (workerTask_ == nullptr ||
      requestState_.load(std::memory_order_acquire) != RequestState::kIdle) {
    return false;
  }
  request_.url = url;
  request_.body = body;
  request_.timeoutMS = timeoutMS;
  request_.post = post;
  request_.sentAt = millis();
  request_.generation = controlGeneration_;
  request_.controlState = controlState_;
  requestState_.store(RequestState::kQueued, std::memory_order_release);
  return true;
}

bool DjangoApiClient::takeResult(HttpResult& result) {
  if (requestState_.load(std::memory_order_acquire) != RequestState::kCompleted) {
    return false;
  }
  result = result_;
  requestState_.store(RequestState::kIdle, std::memory_order_release);
  return true;
}

void DjangoApiClient::setControlState(uint32_t generation, bool interlocked, const String& motion, bool setupAllowed) {
  controlGeneration_ = generation;
  controlState_ = "recovery_generation=" + String(generation) + "&network_interlocked=" + String(interlocked ? 1 : 0)
      + "&motion=" + encodeFormValue(motion) + "&setup_allowed=" + String(setupAllowed ? 1 : 0);
}

bool DjangoApiClient::heartbeat() {
  return submit("/api/esp32/heartbeat/?format=text", "", FirmwareConfig::kCommandHttpTimeoutMS, false);
}

String DjangoApiClient::controlValue(const String& body, const String& key) {
  if (!body.startsWith("{")) return readTextValue(body, key);
  const String value = readJsonString(body, key);
  if (value.length() > 0) return value;
  const int number = readJsonInt(body, key, -1);
  if (number >= 0) return String(number);
  if (body.indexOf("\"" + key + "\"") >= 0) return readJsonBool(body, key, false) ? "1" : "0";
  return "";
}

bool DjangoApiClient::ensureWiFi(uint32_t currentTime) {
  if (WiFi.status() == WL_CONNECTED) {
    const String address = WiFi.localIP().toString();
    if (!wifiWasConnected_ || address != wifiAddress_) {
      resetController(currentTime);
      Serial.print("Wi-Fi connected. IP: ");
      Serial.println(WiFi.localIP());
    }
    wifiAttemptInProgress_ = false;
    wifiWasConnected_ = true;
    wifiAddress_ = address;
    wifiReady_.store(true, std::memory_order_release);
    return true;
  }

  if (wifiWasConnected_) {
    Serial.println("Wi-Fi disconnected. Scheduling non-blocking reconnect.");
    closeConnection();
    resetController(currentTime);
    if (mdnsStarted_) MDNS.end();
    mdnsStarted_ = false;
  }
  wifiWasConnected_ = false;
  wifiReady_.store(false, std::memory_order_release);

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
  return wifiReady_.load(std::memory_order_acquire);
}

void DjangoApiClient::closeConnection() {
  secureClient_.stop();
  activeOrigin_ = "";
}

bool DjangoApiClient::pollCommand(
    const String& feederState,
    const String& feederSensorState,
    int lastFeedCommandId,
    bool sorterCapable) {
  if (FirmwareConfig::kNetworkDiagnosticsOnly || !wifiConnected()) {
    return false;
  }
  String url("/api/esp32/command/?format=text");
  url += url.indexOf('?') >= 0 ? "&" : "?";
  url += "boot_id=" + encodeFormValue(bootId_);
  url += "&capability=feeder_v1";
  if (sorterCapable) {
    url += "&sorter_capability=gate3_sorter_v1";
  }
  url += "&feeder_state=" + encodeFormValue(feederState);
  url += "&feeder_sensor_state=" + encodeFormValue(feederSensorState);
  url += "&last_feed_command_id=" + String(lastFeedCommandId);
  return submit(url, "", FirmwareConfig::kCommandHttpTimeoutMS, false);
}

bool DjangoApiClient::postReport(
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
    bool includeStationIndex,
    uint32_t feederElapsedMS,
    uint32_t feederMaxRunMS,
    const String& feederStopReason) {
  if (FirmwareConfig::kNetworkDiagnosticsOnly) return false;
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
  if (feederStopReason.length() > 0) {
    body += "&feeder_elapsed_ms=" + String(feederElapsedMS);
    body += "&feeder_max_run_ms=" + String(feederMaxRunMS);
    body += "&feeder_stop_reason=" + encodeFormValue(feederStopReason);
  }

  return submit("/api/esp32/report/", body, timeoutMS, true);
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
  const String feederMaxRunValue = readTextValue(body, "feeder_max_run_ms");

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
      feederMaxRunValue.length() > 0) {
    const long revision = timingRevisionValue.toInt();
    const long firstStationSettleMS = firstStationSettleValue.toInt();
    const long servoSettleMS = servoSettleValue.toInt();
    const long fruitSettleMS = fruitSettleValue.toInt();
    const long finalGateReturnDelayMS = finalGateReturnDelayValue.toInt();
    const long idleCommandPollIntervalMS = idleCommandPollIntervalValue.toInt();
    const long feederStopUS = feederStopValue.toInt();
    const long feederDriveUS = feederDriveValue.toInt();
    const long feederMaxRunMS = feederMaxRunValue.toInt();
    if (revision >= 0 && firstStationSettleMS > 0 && servoSettleMS > 0 &&
        fruitSettleMS > 0 && finalGateReturnDelayMS >= 0 &&
        idleCommandPollIntervalMS > 0 && feederStopUS > 0 &&
        feederDriveUS > 0 && feederMaxRunMS > 0) {
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
      command.timing.feederMaxRunMS = static_cast<uint32_t>(feederMaxRunMS);
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
  const int feederMaxRunMS = readJsonInt(motorCommand, "feeder_max_run_ms", -1);
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
      feederStopUS > 0 && feederDriveUS > 0 && feederMaxRunMS > 0) {
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
    command.timing.feederMaxRunMS = static_cast<uint32_t>(feederMaxRunMS);
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

  int valueStart = colonIndex + 1;
  while (valueStart < body.length() && isspace(static_cast<unsigned char>(body.charAt(valueStart)))) ++valueStart;
  if (valueStart >= body.length() || body.charAt(valueStart) != '"') {
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
  secureClient_.boundRequest(timeoutMS * 4 + 1000UL);
  if (!wifiConnected()) {
    secureClient_.clearDiscoveryDeadline();
    result.statusCode = -4;
    result.elapsedMS = millis() - startedAt;
    return result;
  }
  if (!prepareRequest(url, timeoutMS)) {
    secureClient_.clearDiscoveryDeadline();
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
  secureClient_.clearDiscoveryDeadline();
  return result;
}

HttpResult DjangoApiClient::executePost(
    const char* url,
    const String& body,
    uint32_t timeoutMS) {
  HttpResult result;
  const uint32_t startedAt = millis();
  secureClient_.boundRequest(timeoutMS * 4 + 1000UL);
  if (!wifiConnected()) {
    secureClient_.clearDiscoveryDeadline();
    result.statusCode = -4;
    result.elapsedMS = millis() - startedAt;
    return result;
  }
  if (!prepareRequest(url, timeoutMS)) {
    secureClient_.clearDiscoveryDeadline();
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
  secureClient_.clearDiscoveryDeadline();
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
  int startIndex = body.startsWith(prefix) ? 0 : body.indexOf("\n" + prefix);
  if (startIndex < 0) return "";
  if (startIndex != 0 || !body.startsWith(prefix)) ++startIndex;
  if (body.indexOf("\n" + prefix, startIndex + prefix.length()) >= 0) return "";

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
