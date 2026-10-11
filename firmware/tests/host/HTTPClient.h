#pragma once
#include "WiFiClientSecure.h"
class HTTPClient {
 public:
  bool begin(WiFiClientSecure&, const char* url) { url_ = url; return true; }
  void setReuse(bool) {}
  void useHTTP10(bool) {}
  void setConnectTimeout(uint32_t) {}
  void setTimeout(uint32_t timeout) { timeout_ = timeout; }
  void collectHeaders(const char*[], int) {}
  void addHeader(const char*, const char*) {}
  int GET() { reply_ = Fake::http("GET", url_.c_str(), "", timeout_); return reply_.code; }
  int POST(const String& body) { reply_ = Fake::http("POST", url_.c_str(), body.c_str(), timeout_); return reply_.code; }
  String getString() { return reply_.body; }
  String header(const char*) { return reply_.connection; }
  void end() {}
 private:
  String url_;
  uint32_t timeout_ = 0;
  Fake::Reply reply_;
};
