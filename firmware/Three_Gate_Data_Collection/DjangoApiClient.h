#pragma once

#include <Arduino.h>
#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include "ProtocolTypes.h"

class DjangoApiClient {
 public:
  DjangoApiClient();

  void begin();
  bool ensureWiFi(uint32_t currentTime);
  bool wifiConnected() const;
  void closeConnection();

  HttpResult pollCommand();
  HttpResult postReport(
      const String& event,
      int stationIndex,
      int commandId,
      const String& message,
      uint32_t timeoutMS,
      const String& triggerId = "",
      bool includeStationSafetyFields = false,
      bool gatesHome = false,
      bool stationSettled = false,
      uint32_t timingRevision = 0);

  MotorCommand parseCommandText(const String& body) const;
  bool parseStartSequenceFromResponse(const String& body, MotorCommand& command) const;

  static bool responseHasIgnored(const String& response);
  static bool responseHasCaptureRequest(const String& response);
  static bool responseSuggestsStartSequenceWaiting(const String& response);
  static bool isExplicitFastPathRejection(const HttpResult& response);
  static String readJsonString(const String& body, const String& key);
  static int readJsonInt(const String& body, const String& key, int defaultValue);

 private:
  WiFiClientSecure secureClient_;
  HTTPClient http_;
  bool wifiAttemptInProgress_;
  bool wifiWasConnected_;
  uint32_t wifiAttemptStartedAt_;
  uint32_t nextWiFiAttemptAt_;
  String activeOrigin_;

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
