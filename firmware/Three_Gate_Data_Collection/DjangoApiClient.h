#pragma once

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <atomic>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "ProtocolTypes.h"
#include "DiscoverySecureClient.h"
#include "WifiSetup.h"

class DjangoApiClient {
 public:
  DjangoApiClient();
  ~DjangoApiClient();
  DjangoApiClient(const DjangoApiClient&) = delete;
  DjangoApiClient& operator=(const DjangoApiClient&) = delete;

  void begin();
  bool wifiConnected() const;
  bool takeResult(HttpResult& result);
  void setControlState(uint32_t generation, bool interlocked, const String& motion, bool setupAllowed);
  bool heartbeat();
  void requestSetup();
  bool setupPending() const { return setupPending_.load(std::memory_order_acquire); }
  bool controllerReady() const { return controllerReady_.load(std::memory_order_acquire); }
  static String controlValue(const String& body, const String& key);

  // Submit once, then takeResult before submitting again; false means not queued.
  bool pollCommand(
      const String& feederState,
      const String& feederSensorState,
      int lastFeedCommandId,
      bool sorterCapable);
  bool postReport(
      const String& event,
      int stationIndex,
      int commandId,
      const String& message,
      uint32_t timeoutMS,
      const String& triggerId = "",
      bool includeStationSafetyFields = false,
      bool gatesHome = false,
      bool stationSettled = false,
      uint32_t timingRevision = 0,
      const String& classificationCode = "",
      bool includeStationIndex = true,
      uint32_t feederElapsedMS = 0,
      uint32_t feederMaxRunMS = 0,
      const String& feederStopReason = "");

  MotorCommand parseCommandText(const String& body) const;
  bool parseStartSequenceFromResponse(const String& body, MotorCommand& command) const;

  static bool responseHasIgnored(const String& response);
  static bool responseHasCaptureRequest(const String& response);
  static bool responseSuggestsStartSequenceWaiting(const String& response);
  static bool isExplicitFastPathRejection(const HttpResult& response);
  static String readJsonString(const String& body, const String& key);
  static int readJsonInt(const String& body, const String& key, int defaultValue);

 private:
  enum class RequestState : uint8_t { kIdle, kQueued, kRunning, kCompleted };
  struct Request {
    String url;
    String body;
    uint32_t timeoutMS = 0;
    bool post = false;
    uint32_t sentAt = 0;
    uint32_t generation = 0;
    String controlState;
  };

  // Release/acquire transfers ownership of the String payloads between tasks.
  std::atomic<RequestState> requestState_{RequestState::kIdle};
  Request request_;
  HttpResult result_;
  TaskHandle_t workerTask_ = nullptr;
  std::atomic<bool> stopping_{false};
  std::atomic<bool> stopped_{false};
  std::atomic<bool> wifiReady_{false};
  std::atomic<bool> setupPending_{false};
  std::atomic<bool> setupRequested_{false};
  std::atomic<bool> localSetupAllowed_{false};
  std::atomic<bool> controllerReady_{false};
  WifiSetup wifiSetup_;

  DiscoverySecureClient secureClient_;
  HTTPClient http_;
  bool wifiAttemptInProgress_;
  bool wifiWasConnected_;
  String wifiAddress_;
  uint32_t wifiAttemptStartedAt_;
  uint32_t nextWiFiAttemptAt_;
  String activeOrigin_;
  String bootId_;
  String controllerOrigin_;
  String serverRunId_;
  String discoverySource_;
  String discoveryReason_;
  uint32_t networkRevision_ = 0;
  uint32_t discoveryStartedAt_ = 0;
  uint32_t nextDiscoveryAt_ = 0;
  uint32_t nextProbeAt_ = 0;
  uint32_t lastProbeSuccessAt_ = 0;
  bool mdnsStarted_ = false;
  bool initialDiscoveryComplete_ = false;
  bool conflictDetected_ = false;
  bool manualAttempted_ = false;
  uint32_t controlGeneration_ = 0;
  String controlState_;

  bool ensureController(uint32_t currentTime);
  bool tryManualController();
  bool validateController(const String& origin, const String& runId,
                          const String& revision, uint32_t timeoutMS);
  void probeController();
  void resetController(uint32_t currentTime);
  String networkIdentity() const;
  static bool validOrigin(const String& origin);

  static void workerEntry(void* context);
  void runWorker();
  bool submit(const String& url, const String& body, uint32_t timeoutMS, bool post);
  bool ensureWiFi(uint32_t currentTime);
  void closeConnection();
  void startWiFiAttempt(uint32_t currentTime);
  HttpResult executeGet(const char* url, uint32_t timeoutMS);
  HttpResult executePost(const char* url, const String& body, uint32_t timeoutMS);
  bool prepareRequest(const char* url, uint32_t timeoutMS);
  void finishRequest(HttpResult& result);

  static bool timeReached(uint32_t currentTime, uint32_t deadline);
  static String urlOrigin(const char* url);
  static String encodeFormValue(const String& value);
  static String readTextValue(const String& body, const String& key);
  static String extractJsonObject(const String& body, const String& objectKey);
  static bool readJsonBool(const String& body, const String& key, bool defaultValue);
};
