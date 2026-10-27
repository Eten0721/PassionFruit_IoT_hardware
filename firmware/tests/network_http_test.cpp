#include "CaptureController.h"
#include <iomanip>
#include <sstream>

static std::string hex(const std::string& value) {
  if (value.empty()) return "-";
  std::ostringstream stream;
  for (unsigned char ch : value) stream << std::hex << std::setw(2) << std::setfill('0') << static_cast<int>(ch);
  return stream.str();
}
static std::string unhex(const std::string& value) {
  std::string decoded;
  if (value == "-") return decoded;
  assert(value.size() % 2 == 0);
  for (size_t i = 0; i < value.size(); i += 2) decoded += static_cast<char>(std::stoi(value.substr(i, 2), nullptr, 16));
  return decoded;
}
static int count(int pin, int value) {
  int found = 0;
  for (const auto& output : Fake::outputs) if (output.pin == pin && output.value == value) ++found;
  return found;
}
int main(int argc, char** argv) {
  Fake::reset();
  Fake::nvs.clear();
  const bool provision = argc > 1 && std::string(argv[1]) == "--setup";
  const bool finishWait = argc > 1 && std::string(argv[1]) == "--finish-wait";
  Fake::seedWifi = !provision;
  Fake::wifiNvs.clear();
  std::atomic<int> action{0};
  std::atomic<bool> committed{false};
  Fake::bridge = [&](const Fake::Request& request) {
    std::cout << "REQUEST " << request.method << " " << request.url << " " << hex(request.body) << std::endl;
    int status, instruction;
    std::string body;
    assert(static_cast<bool>(std::cin >> status >> body >> instruction));
    if (request.body.find("event=network_recovery_committed&") == 0 && status == 200) committed = true;
    action = instruction;
    return Fake::Reply{status, unhex(body)};
  };
  bool lost = false;
  uint32_t fruitAt = 0;
  int observedFeeds = 0;
  {
    CaptureController controller;
    controller.begin();
    if (provision) {
      for (int i = 0; i < 500 && !Fake::ap; ++i) {
        Fake::now += 10; controller.tick();
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
      }
      assert(Fake::ap);
      Fake::setupRequest("/save", HTTP_POST, {{"ssid", "provisioned-network"}, {"password", "host-only-password"}});
      for (int i = 0; i < 500 && Fake::wifiNvs.empty(); ++i) {
        if (Fake::wifiSSID == "provisioned-network") Fake::connected = true;
        Fake::now += 10; controller.tick();
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
      }
      assert(Fake::ap && !Fake::wifiNvs.empty() && Fake::connected);
      Fake::setupRequest("/exit", HTTP_POST);
      for (int i = 0; i < 500 && Fake::ap; ++i) {
        if (Fake::wifiSSID == "provisioned-network") Fake::connected = true;
        Fake::now += 10; controller.tick();
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
      }
      assert(!Fake::ap && !Fake::wifiNvs.empty() && Fake::connected);
    }
    for (int i = 0; i < 2500; ++i) {
      const int instruction = action.exchange(0);
      if (instruction == 1) Fake::echoUS = 5800;
      if (instruction == 2 || instruction == 3) {
        // Drive the real local link-loss guard before rejoining the controller.
        Fake::connected = false;
        controller.tick();
        Fake::now += 1000;
        controller.tick();
        if (instruction == 3) {
          Fake::infoRevision = 2;
          Fake::mdnsAddresses = {"192.0.2.3"};
        }
        Fake::connected = true;
        Fake::echoUS = 5800;
        lost = true;
      }
      const int feeds = count(23, 1300);
      if (finishWait && Fake::ap) {
        assert(committed && feeds == 1 && "Finishing the wait must not feed again");
        assert(count(25, 45) == 2 && "The existing recovery must home exactly once");
        std::cout << "PASS" << std::endl;
        break;
      }
      if (feeds == 3) {
        assert(lost && committed && "New feed must follow both commits");
        assert(count(25, 45) == 2 && "Recovery must home exactly once");
        std::cout << "PASS" << std::endl;
        break;
      }
      assert(feeds <= 2 || committed);
      if (!finishWait && !lost && feeds > observedFeeds) fruitAt = millis() + 100;
      observedFeeds = feeds;
      if (fruitAt && static_cast<int32_t>(millis() - fruitAt) >= 0) {
        Fake::echoUS = 290;
        fruitAt = 0;
      }
      Fake::now += 20;
      controller.tick();
      std::this_thread::sleep_for(std::chrono::milliseconds(4));
    }
    assert(count(23, 1300) == (finishWait ? 1 : 3));
    if (finishWait) assert(Fake::ap);
  }
  for (auto& worker : Fake::workers) worker.join();
}
