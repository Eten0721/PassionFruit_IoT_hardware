#include "CaptureController.h"
#include <functional>
#include <iostream>

static void tick(CaptureController& controller, uint32_t at) {
  Fake::now = at;
  controller.tick();
  std::this_thread::sleep_for(std::chrono::milliseconds(2));
}
static void pump(CaptureController& controller, const std::function<bool()>& ready) {
  for (int i = 0; i < 500 && !ready(); ++i) tick(controller, millis() + 10);
  assert(ready());
}
static void reset(bool stored = true, uint32_t at = 100) {
  Fake::reset(at);
  Fake::nvs.clear(); Fake::nvsFail = false;
  Fake::wifiNvs.clear(); Fake::wifiNvsFail = false; Fake::seedWifi = stored;
  Fake::bootLow = false; Fake::ap = false; Fake::apFail = false; Fake::noIP = false;
  Fake::scanInProgress = false;
  Fake::scanStopFail = false;
  Fake::setupRequests.clear(); Fake::setupResponses.clear();
  Fake::wireControl = true;
}
static std::string response() { return Fake::setupSnapshot().back().second; }
static void request(CaptureController& controller, const std::string& path, int method = HTTP_GET,
                    std::map<std::string, std::string> args = {}) {
  const auto before = Fake::setupSnapshot().size();
  Fake::setupRequest(path, method, args);
  pump(controller, [before] { return Fake::setupSnapshot().size() > before; });
}
static void longPress(CaptureController& controller) {
  Fake::bootLow = true;
  tick(controller, millis() + 1);
  tick(controller, millis() + 50);
  tick(controller, millis() + 3000);
}
static void save(CaptureController& controller, const std::string& ssid = "new-network") {
  request(controller, "/save", HTTP_POST, {{"ssid", ssid}, {"password", "test-password-only"}, {"origin", "https://192.0.2.1:8000"}});
}
static void initialSetupAndReboot() {
  reset(false);
  {
    CaptureController controller; controller.begin();
    pump(controller, [] { return Fake::ap.load(); });
    assert(Fake::requestSnapshot().empty());
    request(controller, "/");
    assert(response().find("https://cdn") == std::string::npos);
    assert(response().find("type=\"password\"") != std::string::npos);
    request(controller, "/scan", HTTP_POST);
    assert(Fake::scanAsync);
    // Direct input works before the asynchronous scan completes.
    const auto previousSSID = Fake::wifiSSID;
    Fake::scanStopFail = true;
    save(controller);
    assert(Fake::setupSnapshot().back().first == 503);
    assert(response() == "wifi_scan_stop_failed");
    assert(Fake::scanInProgress && Fake::ap && Fake::wifiNvs.empty() && Fake::wifiSSID == previousSSID);
    Fake::scanStopFail = false;
    save(controller);
    assert(!Fake::scanInProgress);
    assert(response() == "connecting");
    assert(Fake::wifiNvs.empty());
    Fake::noIP = true; Fake::connected = true;
    request(controller, "/status");
    assert(Fake::wifiNvs.empty());
    Fake::noIP = false;
    request(controller, "/status");
    pump(controller, [] { return !Fake::wifiNvs.empty(); });
    assert(Fake::ap);
    request(controller, "/status");
    assert(response().find("saved") == 0);
    assert(response().find("test-password-only") == std::string::npos);
    tick(controller, millis() + 3000);
    pump(controller, [] { return !Fake::ap; });
    assert(Fake::connected);
    pump(controller, [] { return !Fake::requestSnapshot().empty(); });
    assert(Fake::mdnsQueries > 0);
    assert(Fake::outputs.size() == 5); // Startup HOME and feeder stop only.
  }
  Fake::reset(); Fake::wireControl = true;
  {
    CaptureController controller; controller.begin();
    pump(controller, [] { return !Fake::requestSnapshot().empty(); });
    assert(!Fake::ap);
    assert(Fake::wifiSSID == "new-network");
    assert(Fake::mdnsQueries > 0);
  }
}
static void buttonAndOffline() {
  reset();
  CaptureController controller; controller.begin();
  pump(controller, [] { return !Fake::requestSnapshot().empty(); });
  for (int i = 0; i < 20; ++i) tick(controller, millis() + 10);
  Fake::bootLow = true;
  tick(controller, millis() + 1); tick(controller, millis() + 20);
  Fake::bootLow = false; tick(controller, millis() + 1); tick(controller, millis() + 50);
  assert(!Fake::ap);
  Fake::connected = false;
  longPress(controller);
  pump(controller, [] { return Fake::ap.load(); });
}
static void wrongPasswordAndSaveFailureKeepPrevious() {
  reset();
  const auto start = millis();
  CaptureController controller; controller.begin();
  pump(controller, [] { return !Fake::requestSnapshot().empty(); });
  for (int i = 0; i < 20; ++i) tick(controller, millis() + 10);
  longPress(controller);
  pump(controller, [] { return Fake::ap.load(); });
  const auto previous = Fake::wifiNvs;
  save(controller, "wrong-password-network");
  const auto attempted = millis();
  tick(controller, attempted + 14980);
  assert(Fake::ap && Fake::wifiNvs == previous);
  tick(controller, attempted + 15000);
  request(controller, "/status");
  request(controller, "/status");
  assert(response() == "wifi_connect_timeout");
  assert(Fake::wifiNvs == previous);
  assert(Fake::wifiSSID == "host-test");
  save(controller);
  Fake::wifiNvsFail = true; Fake::connected = true;
  request(controller, "/status");
  request(controller, "/status");
  assert(response() == "wifi_config_save_failed");
  assert(Fake::ap && Fake::wifiNvs == previous);
  Fake::wifiNvsFail = false;
  save(controller); Fake::connected = true;
  pump(controller, [previous] { return Fake::wifiNvs != previous; });
  tick(controller, millis() + 3000);
  pump(controller, [] { return !Fake::ap; });
  assert(millis() > start);
}
static void invalidConfigurationAndAPFailure() {
  reset(false);
  CaptureController controller; controller.begin();
  pump(controller, [] { return Fake::ap.load(); });
  for (const auto& origin : {"http://192.0.2.1", "https://user@192.0.2.1", "https://192.0.2.1/api/", "https://8.8.8.8", "https://192.0.2.1:65536"}) {
    request(controller, "/save", HTTP_POST, {{"ssid", "new"}, {"password", "test-password-only"}, {"origin", origin}});
    assert(Fake::setupSnapshot().back().first == 400);
    assert(response() == "wifi_configuration_invalid");
  }
  request(controller, "/save", HTTP_POST, {{"ssid", "new"}, {"password", "short"}});
  assert(Fake::setupSnapshot().back().first == 400);
  assert(Fake::wifiNvs.empty());
}
static void motionBlocksButton() {
  reset();
  Fake::enqueue({200, "command=feed_one\ncommand_id=1\nfeed_context=calibration\n"});
  CaptureController controller; controller.begin();
  pump(controller, [] {
    for (const auto& o : Fake::outputs) if (o.pin == 23 && o.value == 1300) return true;
    return false;
  });
  longPress(controller);
  for (int i = 0; i < 50; ++i) tick(controller, millis() + 10);
  assert(!Fake::ap);
}
static void centralDenialAndHeldBoot() {
  reset(); Fake::bootLow = true;
  CaptureController controller; controller.begin();
  for (int i = 0; i < 500; ++i) tick(controller, millis() + 10);
  assert(!Fake::ap);
  Fake::bootLow = false; tick(controller, millis() + 1); tick(controller, millis() + 50);
  Fake::setupAllowed = false;
  for (int i = 0; i < 50; ++i) tick(controller, millis() + 10);
  longPress(controller);
  for (int i = 0; i < 50; ++i) tick(controller, millis() + 10);
  assert(!Fake::ap);
}
static void apFailureAndRollover() {
  reset(false, UINT32_MAX - 2000); Fake::apFail = true; Fake::connected = false;
  CaptureController controller; controller.begin();
  for (int i = 0; i < 100; ++i) tick(controller, millis() + 10);
  assert(!Fake::ap && Fake::wifiNvs.empty());
  Fake::apFail = false;
  longPress(controller);
  pump(controller, [] { return Fake::ap.load(); });
  save(controller);
  Fake::connected = true;
  pump(controller, [] { return !Fake::wifiNvs.empty(); });
  tick(controller, millis() + 3000);
  pump(controller, [] { return !Fake::ap; });
}
static void connectedSTAWithoutDjangoCanSetup() {
  reset(); Fake::mdnsAddresses.clear();
  CaptureController controller; controller.begin();
  for (int i = 0; i < 70; ++i) tick(controller, millis() + 10);
  assert(Fake::connected);
  longPress(controller);
  pump(controller, [] { return Fake::ap.load(); });
}
static void rememberedControllerUnavailableCanSetup() {
  reset();
  CaptureController controller; controller.begin();
  pump(controller, [] { return !Fake::requestSnapshot().empty(); });
  for (int i = 0; i < 30; ++i) tick(controller, millis() + 10);
  for (int i = 0; i < 30; ++i) Fake::enqueue({-11, "", "", false, "GET"});
  longPress(controller);
  pump(controller, [] { return Fake::ap.load(); });
}
int main() {
  std::cout << std::unitbuf;
  std::cout << "initial setup and reboot\n"; initialSetupAndReboot();
  std::cout << "button and offline\n"; buttonAndOffline();
  std::cout << "connection and save failures\n"; wrongPasswordAndSaveFailureKeepPrevious();
  std::cout << "invalid configuration\n"; invalidConfigurationAndAPFailure();
  std::cout << "motion blocks button\n"; motionBlocksButton();
  std::cout << "central denial and held BOOT\n"; centralDenialAndHeldBoot();
  std::cout << "AP failure and rollover\n"; apFailureAndRollover();
  std::cout << "STA without Django\n"; connectedSTAWithoutDjangoCanSetup();
  std::cout << "remembered controller unavailable\n"; rememberedControllerUnavailableCanSetup();
  Fake::reset();
  std::cout << "wifi setup tests passed\n";
}
