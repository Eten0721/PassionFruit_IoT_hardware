#pragma once

#include <atomic>
#include <cassert>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <string>
#include <thread>
#include <vector>
#include <iostream>

#undef assert
#define assert(condition) ((condition) ? static_cast<void>(0) : Fake::fail(#condition, __LINE__))

namespace Fake {
[[noreturn]] inline void fail(const char* condition, int line) {
  std::cerr << "FAIL line " << line << ": " << condition << '\n';
  std::_Exit(1);
}
inline std::atomic<uint32_t> now{100};
inline const auto controlThread = std::this_thread::get_id();
inline unsigned long echoUS = 5800;
inline int sensorReads = 0;
inline uint32_t sensorLatencyMS = 0;
struct Output { uint32_t at; int pin; int value; };
inline std::vector<Output> outputs;
struct Reply {
  int code = 200;
  std::string body;
  std::string connection = "";
  bool held = false;
  std::string method = "";
};
struct Request { std::string method; std::string url; std::string body; uint32_t at; uint32_t timeout; };
inline std::mutex mutex;
inline std::condition_variable changed;
inline std::deque<Reply> replies;
inline std::vector<Request> requests;
inline bool releaseHttp = false;
inline bool holdWiFi = false;
inline bool workerInWiFi = false;
inline bool connected = true;
inline bool failTaskCreation = false;
inline int connectionStops = 0;
inline int mdnsQueries = 0;
inline int mdnsStops = 0;
inline bool holdMDNS = false;
inline bool badInfo = false;
inline bool wrongIdentity = false;
inline bool infoFailure = false;
inline bool maliciousCommand = false;
inline bool probeFailure = false;
inline uint32_t infoDelayMS = 0;
inline uint32_t tlsWriteDelayMS = 0;
inline int tlsWriteChunk = 4096;
inline std::vector<std::string> mdnsAddresses = {"192.0.2.1"};
inline std::vector<Request> infoRequests;
inline std::vector<std::thread> workers;

inline void assertControlThread() { assert(std::this_thread::get_id() == controlThread); }
inline Reply http(const std::string& method, const std::string& url, const std::string& body, uint32_t timeout) {
  std::unique_lock<std::mutex> lock(mutex);
  if (url.find("/api/network/info/") != std::string::npos) {
    now += infoDelayMS;
    infoRequests.push_back({method, url, body, now.load(), timeout});
    const auto end = url.find('/', 8);
    const auto origin = url.substr(0, end);
    return {infoFailure ? -11 : 200, std::string("api_version=") + (badInfo ? "2" : "1") +
        "\nscheme=https\nselected=1\nserver_run_id=12345678-1234-1234-1234-123456789abc\nnetwork_revision=" + std::string(wrongIdentity ? "2" : "1") + "\nserver_url=" + origin +
        "\ncamera_url=" + origin + "/camera/\n"};
  }
  requests.push_back({method, url, body, now.load(), timeout});
  if (url.find("diagnostic=1") != std::string::npos || body.find("event=network_probe") == 0) {
    changed.notify_all();
    return {probeFailure ? -11 : 200,
        std::string("ok=1\ndiagnostic=1\ncontrol_allowed=0\ncommand=") + (maliciousCommand ? "feed_one" : "none") +
        "\ncommand_id=99\nauto_trigger_enabled=1\ndiagnostic_id=87654321-1234-1234-1234-123456789abc\n"
        "server_run_id=12345678-1234-1234-1234-123456789abc\nnetwork_revision=1\n"};
  }
  Reply reply;
  if (!replies.empty() && (replies.front().method.empty() || replies.front().method == method)) {
    reply = replies.front(); replies.pop_front();
  }
  else { reply.body = "command=none\nauto_trigger_enabled=0\n"; }
  changed.notify_all();
  if (reply.held) {
    if (std::this_thread::get_id() == controlThread) now += 5000;
    else changed.wait(lock, [] { return releaseHttp; });
  }
  return reply;
}
inline void reset(uint32_t start = 100) {
  for (auto& worker : workers) worker.join();
  workers.clear();
  now = start; echoUS = 5800; sensorReads = 0; sensorLatencyMS = 0; outputs.clear();
  std::lock_guard<std::mutex> lock(mutex);
  replies.clear(); requests.clear(); releaseHttp = false; holdWiFi = false;
  workerInWiFi = false; connected = true; failTaskCreation = false; connectionStops = 0;
  mdnsQueries = 0; mdnsStops = 0; holdMDNS = false; badInfo = false; wrongIdentity = false; infoFailure = false;
  maliciousCommand = false; probeFailure = false; infoDelayMS = 0; tlsWriteDelayMS = 0; tlsWriteChunk = 4096; mdnsAddresses = {"192.0.2.1"}; infoRequests.clear();
}
inline void unblock() {
  std::lock_guard<std::mutex> lock(mutex);
  releaseHttp = true; holdWiFi = false; holdMDNS = false; changed.notify_all();
}
inline void enqueue(Reply reply) {
  std::lock_guard<std::mutex> lock(mutex);
  replies.push_back(reply);
}
inline std::vector<Request> requestSnapshot() {
  std::lock_guard<std::mutex> lock(mutex);
  return requests;
}
}  // namespace Fake
