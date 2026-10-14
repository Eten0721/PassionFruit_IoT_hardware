#pragma once
#include "Arduino.h"
#include "IPAddress.h"
#include <map>
constexpr int HTTP_GET = 0;
constexpr int HTTP_POST = 1;
namespace Fake {
struct SetupRequest { std::string path; int method; std::map<std::string, std::string> args; };
inline std::deque<SetupRequest> setupRequests;
inline std::vector<std::pair<int, std::string>> setupResponses;
inline bool setupServer = false;
inline std::string setupClientIP = "192.168.4.1";
inline void setupRequest(const std::string& path, int method = HTTP_GET, std::map<std::string, std::string> args = {}) {
  std::lock_guard<std::mutex> lock(mutex);
  setupRequests.push_back({path, method, args});
}
inline std::vector<std::pair<int, std::string>> setupSnapshot() {
  std::lock_guard<std::mutex> lock(mutex);
  return setupResponses;
}
}
class WebServer {
 public:
  struct Client { IPAddress localIP() { return IPAddress(Fake::setupClientIP.c_str()); } };
  Client client() { return {}; }
  explicit WebServer(int) {}
  void on(const char* path, int method, std::function<void()> handler) { handlers_[{path, method}] = handler; }
  void begin() { Fake::setupServer = true; }
  void stop() { Fake::setupServer = false; }
  void sendHeader(const char*, const char*) {}
  void send(int code, const char*, const String& body) {
    std::lock_guard<std::mutex> lock(Fake::mutex);
    Fake::setupResponses.push_back({code, body.c_str()});
  }
  void send_P(int code, const char* type, const char* body) { send(code, type, body); }
  String arg(const char* name) { return current_.args[name]; }
  void handleClient() {
    {
      std::lock_guard<std::mutex> lock(Fake::mutex);
      if (Fake::setupRequests.empty()) return;
      current_ = Fake::setupRequests.front();
      Fake::setupRequests.pop_front();
    }
    const auto handler = handlers_.find({current_.path, current_.method});
    assert(handler != handlers_.end());
    handler->second();
  }
 private:
  Fake::SetupRequest current_;
  std::map<std::pair<std::string, int>, std::function<void()>> handlers_;
};
