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
inline std::vector<std::thread> workers;

inline void assertControlThread() { assert(std::this_thread::get_id() == controlThread); }
inline Reply http(const std::string& method, const std::string& url, const std::string& body, uint32_t timeout) {
  std::unique_lock<std::mutex> lock(mutex);
  requests.push_back({method, url, body, now.load(), timeout});
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
}
inline void unblock() {
  std::lock_guard<std::mutex> lock(mutex);
  releaseHttp = true; holdWiFi = false; changed.notify_all();
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
