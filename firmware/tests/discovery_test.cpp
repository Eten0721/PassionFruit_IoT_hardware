#include "CaptureController.h"
#include <functional>
#include <iostream>
extern const char* manualDjangoOrigin;

static void pump(CaptureController& controller, const std::function<bool()>& ready) {
  for (int i = 0; i < 400 && !ready(); ++i) {
    Fake::now += 50;
    controller.tick();
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  assert(ready());
}

static int queryCount() {
  std::lock_guard<std::mutex> lock(Fake::mutex);
  return Fake::mdnsQueries;
}

static void noMotion() {
  for (const auto& output : Fake::outputs) {
    assert((output.pin == 23 && output.value == 1500) ||
           (output.pin == 25 && output.value == 85) ||
           (output.pin != 23 && output.pin != 25 && output.value == 0));
  }
}

static void roundTrip() {
  Fake::reset();
  {
    CaptureController controller;
    controller.begin();
    pump(controller, [] { return Fake::requestSnapshot().size() >= 2; });
    const auto requests = Fake::requestSnapshot();
    assert(requests[0].url.find("https://192.0.2.1:8443/api/esp32/command/?diagnostic=1") == 0);
    assert(requests[0].url.find("discovery_source=mdns") != std::string::npos);
    assert(requests[0].url.find("local_network_v1") == std::string::npos);
    assert(requests[1].url == "https://192.0.2.1:8443/api/esp32/report/");
    assert(requests[1].body.find("event=network_probe&") == 0);
    noMotion();
  }
  std::lock_guard<std::mutex> lock(Fake::mutex);
  assert(Fake::mdnsStops == 1);
}

static void conflict() {
  Fake::reset();
  Fake::mdnsAddresses = {"192.0.2.1", "192.0.2.3"};
  CaptureController controller;
  controller.begin();
  pump(controller, [] { return queryCount() >= 4; });
  for (const auto& request : Fake::requestSnapshot()) {
    assert(request.method == "POST");
    assert(request.body.find("event=network_discovery_status&discovery_reason=multiple_controllers_found&") == 0);
    assert(request.url.find("192.0.2.9") == std::string::npos);
  }
  assert(millis() >= 15000);
  noMotion();
}

static void fallback(bool invalidInfo, uint32_t start = 100) {
  Fake::reset(start);
  Fake::mdnsAddresses.clear();
  Fake::badInfo = invalidInfo;
  {
    CaptureController controller;
    controller.begin();
    pump(controller, [] { return queryCount() >= 1; });
    assert(Fake::requestSnapshot().empty());
    Fake::now = start + 15000;
    if (invalidInfo) {
      pump(controller, [] { return queryCount() >= 2; });
      assert(Fake::requestSnapshot().empty());
    } else {
      pump(controller, [] { return Fake::requestSnapshot().size() >= 2; });
      const auto requests = Fake::requestSnapshot();
      assert(requests[0].url.find("https://192.0.2.9:9443/") == 0);
      assert(requests[0].url.find("discovery_source=manual") != std::string::npos);
      assert(requests[0].url.find("discovery_reason=controller_not_found") != std::string::npos);
      const int queries = queryCount();
      Fake::now += 6000;
      pump(controller, [queries] { return queryCount() > queries; });
      for (const auto& request : Fake::requestSnapshot()) assert(request.url.find("https://192.0.2.9:9443/") == 0);
    }
    noMotion();
  }
  Fake::reset();
  CaptureController restarted;
  restarted.begin();
  pump(restarted, [] { return Fake::requestSnapshot().size() >= 2; });
  assert(Fake::requestSnapshot()[0].url.find("https://192.0.2.1:8443/") == 0);
}

static void invalidInfoAndMaliciousCommand() {
  Fake::reset();
  Fake::badInfo = true;
  {
    CaptureController controller;
    controller.begin();
    pump(controller, [] { return queryCount() >= 2; });
    assert(Fake::requestSnapshot().empty());
    noMotion();
  }
  Fake::reset();
  Fake::maliciousCommand = true;
  CaptureController controller;
  controller.begin();
  pump(controller, [] { return Fake::requestSnapshot().size() >= 3; });
  for (const auto& request : Fake::requestSnapshot()) assert(request.method == "GET");
  noMotion();
}

static void discoveryCannotBlockSensor() {
  Fake::reset();
  Fake::holdMDNS = true;
  CaptureController controller;
  controller.begin();
  pump(controller, [] { return queryCount() == 1; });
  const int reads = Fake::sensorReads;
  for (int i = 0; i < 20; ++i) {
    Fake::now += 50;
    const auto time = millis();
    controller.tick();
    assert(millis() == time);
  }
  assert(Fake::sensorReads == reads + 20);
  assert(Fake::requestSnapshot().empty());
  noMotion();
  Fake::unblock();
}

static void lateInfoIsNotSelected() {
  Fake::reset();
  Fake::infoDelayMS = 15001;
  CaptureController controller;
  controller.begin();
  pump(controller, [] { return queryCount() >= 2; });
  for (const auto& request : Fake::requestSnapshot()) assert(request.url.find("192.0.2.1") == std::string::npos);
  noMotion();
}

static void invalidManualOrigins() {
  const char* original = manualDjangoOrigin;
  for (const char* value : {"http://192.0.2.9:9443", "https://192.0.2.9:0", "https://192.0.2.9:65536",
       "https://user@192.0.2.9:9443", "https://192.0.2.9:9443/api/", "https://198.51.100.9:9443"}) {
    Fake::reset();
    Fake::mdnsAddresses.clear();
    manualDjangoOrigin = value;
    CaptureController controller;
    controller.begin();
    pump(controller, [] { return queryCount() >= 1; });
    Fake::now += 15000;
    pump(controller, [] { return queryCount() >= 3; });
    assert(Fake::requestSnapshot().empty());
    std::lock_guard<std::mutex> lock(Fake::mutex);
    assert(Fake::infoRequests.empty());
  }
  manualDjangoOrigin = original;
}

static void identityMismatchAndWiFiRejoin() {
  Fake::reset();
  Fake::wrongIdentity = true;
  {
    CaptureController controller;
    controller.begin();
    pump(controller, [] { return queryCount() >= 2; });
    assert(Fake::requestSnapshot().empty());
  }
  Fake::reset();
  CaptureController controller;
  controller.begin();
  pump(controller, [] { return Fake::requestSnapshot().size() >= 2; });
  {
    std::lock_guard<std::mutex> lock(Fake::mutex);
    Fake::connected = false;
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(25));
  {
    std::lock_guard<std::mutex> lock(Fake::mutex);
    Fake::mdnsAddresses = {"192.0.2.3"};
    Fake::connected = true;
  }
  pump(controller, [] {
    const auto requests = Fake::requestSnapshot();
    return !requests.empty() && requests.back().url.find("https://192.0.2.3:8443/") == 0;
  });
  noMotion();
}

static void absoluteReadDeadline() {
  Fake::reset(UINT32_MAX - 50);
  DiscoverySecureClient client;
  client.setDiscoveryDeadline(millis() + 100);
  assert(client.available() == 1);
  Fake::now += 99;
  assert(client.read() == 65);
  Fake::now += 1;
  assert(client.available() == 0);
  assert(client.read() == -1);
  assert(client.connected() == 0);
  client.clearDiscoveryDeadline();
  assert(client.connected() == 1);
  client.setDiscoveryDeadline(millis() + 100);
  Fake::tlsWriteDelayMS = 25;
  Fake::tlsWriteChunk = 1;
  const uint8_t body[16] = {};
  assert(client.write(body, sizeof(body)) == 4);
  assert(client.connected() == 0);
}

int main() {
  std::cout << std::unitbuf;
  std::cout << "round trip\n"; roundTrip();
  std::cout << "conflict\n"; conflict();
  std::cout << "fallback\n"; fallback(false);
  std::cout << "bad fallback\n"; fallback(true);
  std::cout << "rollover\n"; fallback(false, UINT32_MAX - 1000);
  std::cout << "invalid info\n"; invalidInfoAndMaliciousCommand();
  std::cout << "held discovery\n"; discoveryCannotBlockSensor();
  std::cout << "late info\n"; lateInfoIsNotSelected();
  std::cout << "invalid origin\n"; invalidManualOrigins();
  std::cout << "identity and Wi-Fi rejoin\n"; identityMismatchAndWiFiRejoin();
  absoluteReadDeadline();
  Fake::reset();
  std::cout << "PASS: discovery, conflict, fallback, restart, rollover, identity, HTTP 2xx isolation, cleanup and sensor ownership\n";
}
