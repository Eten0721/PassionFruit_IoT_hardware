#pragma once
#include "IPAddress.h"

struct FakeMDNS {
  std::vector<std::string> results;
  bool begin(const String&) { return true; }
  void end() { std::lock_guard<std::mutex> lock(Fake::mutex); ++Fake::mdnsStops; }
  int queryService(const char* service, const char* protocol) {
    assert(std::this_thread::get_id() != Fake::controlThread);
    assert(std::string(service) == "pf-iot" && std::string(protocol) == "tcp");
    std::unique_lock<std::mutex> lock(Fake::mutex);
    ++Fake::mdnsQueries;
    Fake::changed.notify_all();
    Fake::changed.wait(lock, [] { return !Fake::holdMDNS; });
    results = Fake::mdnsAddresses;
    return results.size();
  }
  IPAddress address(int i) { return IPAddress(results.at(i).c_str()); }
  uint16_t port(int) { return 8443; }
  String txt(int, const char* key) {
    const std::string name(key);
    if (name == "api_version") return "1";
    if (name == "scheme") return "https";
    if (name == "info_path") return "/api/network/info/";
    if (name == "server_run_id") return "12345678-1234-1234-1234-123456789abc";
    if (name == "network_revision") return "1";
    return "";
  }
};
inline FakeMDNS MDNS;
