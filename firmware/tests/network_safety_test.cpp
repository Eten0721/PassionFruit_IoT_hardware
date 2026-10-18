#include "CaptureController.h"
#include <functional>
#include <iostream>

static int outputs(int pin, int value) {
  int count = 0;
  for (const auto& output : Fake::outputs) if (output.pin == pin && output.value == value) ++count;
  return count;
}
static void tick(CaptureController& controller, uint32_t at) {
  Fake::now = at;
  controller.tick();
  std::this_thread::sleep_for(std::chrono::milliseconds(2));
}
static void pump(CaptureController& controller, const std::function<bool()>& ready) {
  for (int i = 0; i < 250 && !ready(); ++i) tick(controller, millis() + 10);
  if (!ready()) {
    std::cerr << "pump at " << millis() << "\n";
    for (const auto& r : Fake::requestSnapshot()) std::cerr << r.method << " " << r.url << " body=" << r.body << "\n";
    for (const auto& o : Fake::outputs) std::cerr << "output " << o.pin << "=" << o.value << " at " << o.at << "\n";
  }
  assert(ready());
}
static void reset(uint32_t at = 100) {
  Fake::reset(at);
  Fake::nvs.clear();
  Fake::nvsFail = false;
  Fake::wireControl = true;
}
static uint32_t feed(CaptureController& controller, bool longRun = false) {
  const std::string profile = "timing_revision=1\nfirst_station_settle_ms=300\nservo_settle_ms=200\nfruit_settle_ms=350\nfinal_gate_return_delay_ms=300\nidle_command_poll_interval_ms=250\nfeeder_stop_us=1500\nfeeder_drive_us=1300\nfeeder_max_run_ms=10000\n";
  if (longRun) {
    Fake::enqueue({200, "command=none\n" + profile, "", false, "GET"});
    Fake::enqueue({200, "{}", "", false, "POST"});
  }
  Fake::enqueue({200, "command=feed_one\ncommand_id=1\nfeed_context=calibration\n", "", false, "GET"});
  controller.begin();
  pump(controller, [] { return outputs(23, 1300) == 1; });
  for (const auto& output : Fake::outputs) if (output.pin == 23 && output.value == 1300) return output.at;
  assert(false);
  return 0;
}
static void stopAtLeaseDespiteHeldWorker(uint32_t initial = 100) {
  reset(initial);
  {
    CaptureController controller;
    feed(controller, true);
    uint32_t requestAt = Fake::requestSnapshot().back().at;
    Fake::enqueue({200, "{}", "", true, "GET"});
    pump(controller, [] { return Fake::requestSnapshot().back().url.find("/heartbeat/") != std::string::npos; });
    const auto held = Fake::requestSnapshot().back();
    assert(held.url.find("/heartbeat/") != std::string::npos);
    tick(controller, requestAt + 5900);
    assert(outputs(23, 1500) == 1);
    tick(controller, requestAt + 6100);
    assert(outputs(23, 1500) == 2);
    const auto count = Fake::outputs.size();
    tick(controller, requestAt + 10000);
    assert(Fake::outputs.size() == count);
    Fake::unblock();
    for (int i = 0; i < 100; ++i) tick(controller, millis() + 10);
    assert(outputs(23, 1300) == 1);
  }
  // A new controller loads the durable lock and emits no automatic HOME.
  Fake::reset();
  Fake::wireControl = true;
  {
    CaptureController controller;
    controller.begin();
    assert(Fake::outputs.size() == 1);
    assert(Fake::outputs[0].pin == 23 && Fake::outputs[0].value == 1500);
  }
}
static void wifiLossPrecedesRemainingMotion() {
  reset();
  CaptureController controller;
  const uint32_t started = feed(controller);
  Fake::connected = false;
  tick(controller, started + 50);
  tick(controller, started + 1049);
  assert(outputs(23, 1500) == 1);
  tick(controller, started + 1050);
  assert(outputs(23, 1500) == 2);
  const auto count = Fake::outputs.size();
  Fake::connected = true;
  for (int i = 0; i < 30; ++i) tick(controller, millis() + 10);
  assert(Fake::outputs.size() == count);
}
static void sensorStopsBeforeLease() {
  for (const auto echo : {0UL, 290UL}) {
    reset();
    CaptureController controller;
    const uint32_t started = feed(controller);
    Fake::echoUS = echo;
    tick(controller, started + 50);
    assert(outputs(23, 1500) == 2);
    assert(outputs(23, 1300) == 1);
  }
}
static void nvsFailureBlocksFeed() {
  reset();
  CaptureController controller;
  Fake::nvsFail = true;
  Fake::enqueue({200, "command=feed_one\ncommand_id=1\nfeed_context=calibration\n"});
  controller.begin();
  for (int i = 0; i < 70; ++i) tick(controller, millis() + 50);
  assert(outputs(23, 1300) == 0);
  assert(outputs(25, 45) == 0);
}
static void malformedAndExpiredPermissionCannotDrive() {
  reset();
  Fake::wireControl = false;
  Fake::enqueue({200, "command=feed_one\ncommand_id=1\nfeed_context=calibration\n"});
  {
    CaptureController controller;
    controller.begin();
    for (int i = 0; i < 60; ++i) tick(controller, millis() + 50);
    assert(outputs(23, 1300) == 0);
  }
  reset();
  Fake::enqueue({200, "command=feed_one\ncommand_id=1\nfeed_context=calibration\n", "", true});
  {
    CaptureController controller;
    controller.begin();
    pump(controller, [] { return !Fake::requestSnapshot().empty(); });
    tick(controller, millis() + 7000);
    Fake::unblock();
    for (int i = 0; i < 100; ++i) tick(controller, millis() + 10);
    assert(outputs(23, 1300) == 0);
  }
}
static void deniedPermissionCancelsGatePhases() {
  for (const uint32_t phaseAt : {200UL, 500UL, 900UL}) {
    reset();
    CaptureController controller;
    Fake::enqueue({200, "command=start_sequence\ncommand_id=1\n"});
    controller.begin();
    pump(controller, [] { return Fake::requestSnapshot().size() >= 2; });
    Fake::controlAllowed = false;
    Fake::serverGeneration = 1;
    tick(controller, millis() + phaseAt);
    for (int i = 0; i < 150; ++i) tick(controller, millis() + 10);
    for (const auto& output : Fake::outputs) assert(output.value != 90 && "Denied control cannot release a gate");
  }
}
static void recoveryCommitsOnce() {
  reset();
  CaptureController controller;
  feed(controller);
  Fake::controlAllowed = false;
  Fake::serverGeneration = 1;
  for (int i = 0; i < 150; ++i) tick(controller, millis() + 10);
  const int homes = outputs(25, 45);
  Fake::recoveryAllowed = true;
  Fake::enqueue({200, "command=recover_network\ncommand_id=9\n", "", false, "GET"});
  pump(controller, [&] { return outputs(25, 45) == homes + 1; });
  Fake::enqueue({200, "{\"ok\":true,\"recovery_commit\":true}", "", false, "POST"});
  Fake::enqueue({200, "{\"ok\":true,\"recovery_committed\":true}", "", false, "POST"});
  for (int i = 0; i < 170; ++i) tick(controller, millis() + 10);
  assert(outputs(25, 45) == homes + 1);
  assert(outputs(23, 1300) == 1);
}

static Fake::Reply controlReply(const Fake::Request& request, const std::string& payload,
                               bool allowed, bool recovery, uint32_t generation) {
  return {200, payload + "\nboot_id=" + Fake::field(request.method == "POST" ? request.body : request.url, "boot_id") +
      "\nserver_run_id=12345678-1234-1234-1234-123456789abc\nnetwork_revision=1\nrecovery_generation=" +
      std::to_string(generation) + "\ncontrol_allowed=" + (allowed ? "1" : "0") +
      "\nrecovery_allowed=" + (recovery ? "1" : "0") + "\ncontrol_lease_ms=" + (allowed ? "6000" : "0") + "\n"};
}
static void slowHeartbeatCannotStarveStationReport() {
  reset();
  std::atomic<bool> stationReported{false};
  std::atomic<int> heartbeats{0};
  Fake::bridge = [&](const Fake::Request& request) {
    std::string payload = "command=none\n";
    if (request.url.find("/heartbeat/") != std::string::npos) {
      ++heartbeats;
      Fake::now += 1100;
    } else if (request.method == "GET") {
      payload = "command=start_sequence\ncommand_id=1\ntiming_revision=1\nfirst_station_settle_ms=1500\nservo_settle_ms=200\nfruit_settle_ms=350\nfinal_gate_return_delay_ms=300\nidle_command_poll_interval_ms=250\nfeeder_stop_us=1500\nfeeder_drive_us=1300\nfeeder_max_run_ms=5000\n";
    } else if (request.body.find("event=station_1_ready&") == 0) {
      stationReported = true;
    }
    return controlReply(request, payload, true, false, 0);
  };
  CaptureController controller;
  controller.begin();
  pump(controller, [&] { return stationReported.load(); });
  assert(heartbeats > 0 && heartbeats < 5);
  assert(outputs(23, 1300) == 0);
}
static void revokedRecoveryAckAllowsNewHumanCommand(bool advanceGeneration) {
  reset();
  CaptureController controller;
  feed(controller);
  Fake::controlAllowed = false;
  Fake::serverGeneration = 1;
  pump(controller, [] { return outputs(23, 1500) == 2; });
  std::atomic<int> completed{0};
  Fake::bridge = [&](const Fake::Request& request) {
    const uint32_t generation = advanceGeneration && completed > 0 ? 2 : 1;
    if (request.method == "GET") {
      return controlReply(request, "command=recover_network\ncommand_id=" + std::to_string(completed == 0 ? 9 : 10) + "\n", false, true, generation);
    }
    if (request.body.find("event=network_recovery_completed&") == 0) {
      if (++completed == 1) {
        auto reply = controlReply(request, "reason=network_recovery_ack_mismatch\n", false, false, generation);
        reply.code = 409;
        return reply;
      }
      return controlReply(request, "recovery_commit=1\n", false, true, generation);
    }
    if (request.body.find("event=network_recovery_committed&") == 0) {
      return controlReply(request, "recovery_committed=1\n", true, false, generation);
    }
    return controlReply(request, "ok=1\n", false, true, generation);
  };
  pump(controller, [&] { return completed == 2; });
  for (int i = 0; i < 80; ++i) tick(controller, millis() + 10);
  assert(outputs(25, 45) == 3);
  assert(outputs(23, 1300) == 1);
}
static void classifierLossCancelsEveryRemainingPhase() {
  for (const uint32_t phase : {0UL, 499UL, 500UL, 1499UL, 1500UL, 1999UL, 2000UL}) {
    reset();
    std::atomic<int> station{0};
    Fake::bridge = [&](const Fake::Request& request) {
      std::string payload = "command=none\n";
      if (request.method == "GET") {
        const int current = station;
        payload = current == 0 ? "command=start_sequence\ncommand_id=1\n"
            : current < 3 ? "command=release_gate\ncommand_id=" + std::to_string(current + 1) + "\nstation_index=" + std::to_string(current) + "\n"
            : "command=classify_fruit\ncommand_id=4\nclassification_code=high\n";
      } else if (request.body.find("event=station_") == 0) {
        station = request.body[14] - '0';
      }
      return controlReply(request, payload, true, false, 0);
    };
    CaptureController controller;
    controller.begin();
    pump(controller, [] { return outputs(25, 15) == 1; });
    const uint32_t started = millis();
    tick(controller, started + phase);
    Fake::connected = false;
    tick(controller, millis());
    tick(controller, millis() + 1000);
    const auto stopped = Fake::outputs.size();
    assert(Fake::outputs.back().pin == 23 && Fake::outputs.back().value == 1500);
    tick(controller, millis() + 10000);
    assert(Fake::outputs.size() == stopped);
  }
}
static void timingProfileAppliesServoAngles() {
  reset();
  const std::string profile = "timing_revision=2\nfirst_station_settle_ms=300\nservo_settle_ms=200\nfruit_settle_ms=350\nfinal_gate_return_delay_ms=300\nidle_command_poll_interval_ms=250\nfeeder_stop_us=1500\nfeeder_drive_us=1300\nfeeder_max_run_ms=5000\ngate1_home_angle=10\ngate2_home_angle=20\ngate3_home_angle=30\nsorter_home_angle=80\nsorter_high_angle=60\nsorter_medium_angle=75\nsorter_low_angle=105\nsorter_processing_angle=120\n";
  std::atomic<int> station{-1};
  Fake::bridge = [&](const Fake::Request& request) {
    std::string payload = "command=none\n" + profile;
    const int current = station;
    if (request.method == "GET" && current >= 0) {
      payload = current == 0 ? "command=start_sequence\ncommand_id=1\n"
          : current < 3 ? "command=release_gate\ncommand_id=" + std::to_string(current + 1) + "\nstation_index=" + std::to_string(current) + "\n"
          : "command=classify_fruit\ncommand_id=4\nclassification_code=high\n";
    } else if (request.body.find("event=timing_config_applied") == 0) {
      station = 0;
    } else if (request.body.find("event=station_") == 0) {
      station = request.body[14] - '0';
    }
    return controlReply(request, payload, true, false, 0);
  };
  CaptureController controller;
  controller.begin();
  // The full three-station sequence outlasts pump()'s 2.5 s budget.
  for (int i = 0; i < 2000 && outputs(25, 60) == 0; ++i) tick(controller, millis() + 10);
  assert(outputs(25, 60) == 1);
  assert(outputs(18, 10) >= 1 && outputs(19, 20) >= 1 && outputs(21, 30) >= 1);
  assert(outputs(25, 80) >= 1 && outputs(25, 15) == 0);
  pump(controller, [] { return outputs(25, 80) >= 2 && outputs(21, 30) >= 2; });
}
static void idleDeviceAdoptsServerRecoveryGeneration() {
  reset();
  Fake::controlAllowed = false;
  Fake::serverGeneration = 1;
  CaptureController controller;
  controller.begin();
  pump(controller, [] { return outputs(23, 1500) == 2; });
  Fake::recoveryAllowed = true;
  Fake::enqueue({200, "command=recover_network\ncommand_id=9\n", "", false, "GET"});
  pump(controller, [] { return outputs(25, 45) == 2; });
  assert(outputs(23, 1300) == 0);
}
static void rebootRestoresCalibratedStopAndNoHome() {
  reset();
  struct Record { uint32_t schema, generation; int command; uint32_t stop; uint8_t phase; } record{1, 7, 9, 1525, 2};
  const auto* bytes = reinterpret_cast<const uint8_t*>(&record);
  Fake::nvs.assign(bytes, bytes + sizeof(record));
  CaptureController controller;
  controller.begin();
  assert(Fake::outputs.size() == 1);
  assert(Fake::outputs[0].pin == 23 && Fake::outputs[0].value == 1525);
}
int main() {
  assert(DjangoApiClient::controlValue("{\"control_allowed\": true, \"boot_id\": \"board\"}", "control_allowed") == "1");
  assert(DjangoApiClient::controlValue("{\"recovery_generation\": 7, \"boot_id\": \"board\"}", "recovery_generation") == "7");
  stopAtLeaseDespiteHeldWorker();
  stopAtLeaseDespiteHeldWorker(0xfffff000UL);
  wifiLossPrecedesRemainingMotion();
  sensorStopsBeforeLease();
  nvsFailureBlocksFeed();
  malformedAndExpiredPermissionCannotDrive();
  deniedPermissionCancelsGatePhases();
  recoveryCommitsOnce();
  slowHeartbeatCannotStarveStationReport();
  revokedRecoveryAckAllowsNewHumanCommand(false);
  revokedRecoveryAckAllowsNewHumanCommand(true);
  rebootRestoresCalibratedStopAndNoHome();
  classifierLossCancelsEveryRemainingPhase();
  idleDeviceAdoptsServerRecoveryGeneration();
  timingProfileAppliesServoAngles();
  for (auto& worker : Fake::workers) worker.join();
  std::cout << "Firmware network safety contracts passed.\n";
}
