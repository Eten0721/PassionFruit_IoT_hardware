#include "CaptureController.h"
#include <iostream>
#include <functional>

#undef assert
#define assert(condition) ((condition) ? static_cast<void>(0) : Fake::fail(#condition, __LINE__))

static void tickAt(CaptureController& controller, uint32_t time) {
  Fake::now = time;
  const int sensorReads = Fake::sensorReads;
  controller.tick();
  assert(millis() == time + (Fake::sensorReads - sensorReads) * Fake::sensorLatencyMS && "HTTPS waiting must not advance the control clock");
  std::this_thread::sleep_for(std::chrono::milliseconds(2));
}

static void pumpUntil(CaptureController& controller, const std::function<bool()>& ready) {
  for (int attempt = 0; attempt < 200 && !ready(); ++attempt) tickAt(controller, millis() + 10);
  assert(ready());
}

static int outputCount(int pin, int value) {
  int count = 0;
  for (const auto& output : Fake::outputs) if (output.pin == pin && output.value == value) ++count;
  return count;
}

static uint32_t lastOutputAt(int pin, int value) {
  for (auto it = Fake::outputs.rbegin(); it != Fake::outputs.rend(); ++it) {
    if (it->pin == pin && it->value == value) return it->at;
  }
  assert(false && "Expected actuator output is missing");
  return 0;
}

static std::vector<Fake::Request> reports(const std::string& event) {
  std::vector<Fake::Request> matches;
  for (const auto& request : Fake::requestSnapshot()) {
    if (request.method == "POST" && request.body.find("event=" + event + "&") == 0) matches.push_back(request);
  }
  return matches;
}

static std::string command(const std::string& name, int id, const std::string& fields = "") {
  return "command=" + name + "\ncommand_id=" + std::to_string(id) + "\n" + fields;
}

static uint32_t startFeed(CaptureController& controller, const char* context = "calibration") {
  Fake::enqueue({200, command("feed_one", 1, std::string("feed_context=") + context + "\n")});
  controller.begin();
  pumpUntil(controller, [] { return outputCount(23, 1300) == 1; });
  return lastOutputAt(23, 1300);
}

static void holdWorker() {
  {
    std::lock_guard<std::mutex> lock(Fake::mutex);
    Fake::holdWiFi = true;
  }
  std::unique_lock<std::mutex> lock(Fake::mutex);
  assert(Fake::changed.wait_for(lock, std::chrono::seconds(1), [] { return Fake::workerInWiFi; }));
}

static void delayedCommandKeepsReadingSensor() {
  Fake::nvs.clear();
  Fake::reset();
  Fake::enqueue({200, "command=none\nauto_trigger_enabled=0\n", "", true});
  CaptureController controller;
  controller.begin();
  pumpUntil(controller, [] { return Fake::requestSnapshot().size() == 1; });
  const uint32_t started = millis();
  const int initialReads = Fake::sensorReads;
  for (uint32_t elapsed = 50; elapsed <= 400; elapsed += 50) tickAt(controller, started + elapsed);
  assert(Fake::sensorReads >= initialReads + 8);
  assert(Fake::requestSnapshot().size() == 1 && "Only one in-flight request is allowed");
  Fake::unblock();
}

static void stalledWorkerCannotDelayFeederStop(const std::string& reason, uint32_t initialTime = 100) {
  std::cout << "  " << reason << " start=" << initialTime << '\n';
  Fake::nvs.clear();
  Fake::reset(initialTime);
  CaptureController controller;
  const uint32_t started = startFeed(controller);
  holdWorker();
  if (reason == "max_run_timeout" || reason == "boundary") {
    tickAt(controller, started + 4999);
    assert(outputCount(23, 1500) == 1);
    if (reason == "boundary") Fake::echoUS = 290;
    tickAt(controller, started + 5000);
    assert(lastOutputAt(23, 1500) - started == 5000);
  } else {
    Fake::echoUS = reason == "hcsr04" ? 290 : 0;
    tickAt(controller, started + 50);
    assert(lastOutputAt(23, 1500) - started == 50);
  }
  assert(outputCount(23, 1300) == 1);
  const auto sensorReadsAtStop = Fake::sensorReads;
  tickAt(controller, millis() + 50);
  assert(Fake::sensorReads > sensorReadsAtStop);
  Fake::unblock();
  const std::string event = reason == "hcsr04" ? "feed_cycle_completed"
      : reason == "unavailable" ? "feeder_sensor_unavailable" : "feeder_max_run_timeout";
  pumpUntil(controller, [&] { return !reports(event).empty(); });
  const auto report = reports(event).front();
  assert(report.body.find("command_id=1") != std::string::npos);
  assert(report.body.find("feeder_elapsed_ms=" + std::string(reason == "hcsr04" || reason == "unavailable" ? "50" : "5000")) != std::string::npos);
}

static void terminalReportRetryDoesNotRepeatFeed() {
  Fake::nvs.clear();
  Fake::reset();
  CaptureController controller;
  const uint32_t started = startFeed(controller);
  Fake::enqueue({-11, "", "", true});
  tickAt(controller, started + 5000);
  pumpUntil(controller, [] { return reports("feeder_max_run_timeout").size() == 1; });
  const auto sensorReads = Fake::sensorReads;
  for (int count = 0; count < 20; ++count) tickAt(controller, millis() + 50);
  assert(Fake::sensorReads == sensorReads + 20);
  assert(reports("feeder_max_run_timeout").size() == 1);
  assert(outputCount(23, 1500) == 2);
  Fake::enqueue({200, "{}", "close"});
  Fake::unblock();
  pumpUntil(controller, [] { return reports("feeder_max_run_timeout").size() == 2; });
  const auto retried = reports("feeder_max_run_timeout");
  assert(retried[0].body == retried[1].body);
  assert(retried[0].timeout == 5000 && retried[1].timeout == 5000);
  Fake::enqueue({200, command("feed_one", 1, "feed_context=calibration\n")});
  for (int count = 0; count < 40; ++count) tickAt(controller, millis() + 50);
  assert(outputCount(23, 1300) == 1);
  std::lock_guard<std::mutex> lock(Fake::mutex);
  assert(Fake::connectionStops >= 2);
}

static void triggerRetriesRetainId(bool legacy) {
  std::cout << "  trigger legacy=" << legacy << '\n';
  Fake::nvs.clear();
  Fake::reset();
  CaptureController controller;
  Fake::enqueue({200, "command=none\nauto_trigger_enabled=1\n"});
  controller.begin();
  pumpUntil(controller, [] { return !Fake::requestSnapshot().empty(); });
  for (int count = 0; count < 3; ++count) tickAt(controller, millis() + 10);
  if (legacy) Fake::enqueue({400, "{\"reason\":\"unknown_event\"}"});
  Fake::enqueue({-11, "", "", true});
  Fake::echoUS = 290;
  const std::string event = legacy ? "hcsr04_trigger" : "hcsr04_station_1_ready";
  pumpUntil(controller, [&] { return reports(event).size() == 1; });
  const auto reads = Fake::sensorReads;
  for (int count = 0; count < 20; ++count) tickAt(controller, millis() + 50);
  assert(Fake::sensorReads >= reads + 20);
  assert(reports(event).size() == 1);
  Fake::enqueue({200, legacy
      ? "{\"motor_command\":{\"command\":\"start_sequence\",\"command_id\":7}}"
      : "{\"capture_requested\":true}", "", false, "POST"});
  Fake::unblock();
  pumpUntil(controller, [&] { return reports(event).size() == 2; });
  const auto retried = reports(event);
  assert(retried[0].body == retried[1].body);
  assert(retried[0].body.find("trigger_id=esp32-") != std::string::npos);
  assert(retried[0].timeout == 1000);
  assert(outputCount(23, 1300) == 0);
  if (legacy) pumpUntil(controller, [] { return !reports("station_1_ready").empty(); });
}

static void lateFruitWhileTerminalReportIsHeld() {
  Fake::nvs.clear();
  Fake::reset();
  CaptureController controller;
  const uint32_t started = startFeed(controller, "production");
  Fake::enqueue({200, "{}", "", true});
  tickAt(controller, started + 5000);
  pumpUntil(controller, [] { return !reports("feeder_max_run_timeout").empty(); });
  Fake::echoUS = 290;
  for (int count = 0; count < 10; ++count) tickAt(controller, millis() + 50);
  assert(reports("hcsr04_station_1_ready").empty());
  Fake::enqueue({200, "{}"});
  Fake::unblock();
  pumpUntil(controller, [] { return !reports("hcsr04_station_1_ready").empty(); });
  assert(outputCount(23, 1300) == 1);
}

static void unavailableWorkerDoesNotStartMotion() {
  Fake::nvs.clear();
  Fake::reset();
  Fake::failTaskCreation = true;
  CaptureController controller;
  controller.begin();
  for (int count = 0; count < 20; ++count) tickAt(controller, millis() + 50);
  assert(Fake::requestSnapshot().empty());
  assert(outputCount(23, 1300) == 0);
  assert(Fake::sensorReads >= 20);
}

static void captureAndSorterKeepExistingPhases() {
  Fake::nvs.clear();
  Fake::reset();
  Fake::enqueue({200, command("start_sequence", 1)});
  Fake::enqueue({200, "{}", "", true});
  Fake::enqueue({200, command("release_gate", 2, "station_index=1\n")});
  Fake::enqueue({200, "{}"});
  Fake::enqueue({200, command("release_gate", 3, "station_index=2\n")});
  Fake::enqueue({200, "{}"});
  Fake::enqueue({200, command("classify_fruit", 4, "classification_code=high\n")});
  Fake::enqueue({-11, ""});
  Fake::enqueue({200, "{}"});
  Fake::enqueue({200, command("classify_fruit", 4, "classification_code=high\n")});
  CaptureController controller;
  controller.begin();
  pumpUntil(controller, [] { return !reports("station_1_ready").empty(); });
  const auto reads = Fake::sensorReads;
  for (int count = 0; count < 10; ++count) tickAt(controller, millis() + 50);
  assert(Fake::sensorReads >= reads + 10);
  assert(outputCount(18, 90) == 0);
  Fake::unblock();
  pumpUntil(controller, [] { return outputCount(25, 15) == 1; });
  const auto sorterStarted = lastOutputAt(25, 15);
  tickAt(controller, sorterStarted + 499);
  assert(outputCount(21, 90) == 0);
  tickAt(controller, sorterStarted + 500);
  assert(lastOutputAt(21, 90) == sorterStarted + 500);
  tickAt(controller, sorterStarted + 1499);
  assert(outputCount(25, 45) == 1);
  tickAt(controller, sorterStarted + 1500);
  assert(lastOutputAt(25, 45) == sorterStarted + 1500);
  assert(lastOutputAt(18, 0) == sorterStarted + 1500);
  assert(lastOutputAt(19, 0) == sorterStarted + 1500);
  assert(lastOutputAt(21, 0) == sorterStarted + 1500);
  tickAt(controller, sorterStarted + 2000);
  pumpUntil(controller, [] { return reports("classification_sorter_completed").size() == 2; });
  const auto retried = reports("classification_sorter_completed");
  assert(retried[0].body == retried[1].body);
  for (int count = 0; count < 30; ++count) tickAt(controller, millis() + 50);
  assert(outputCount(25, 15) == 1 && outputCount(21, 90) == 1);
}

static void sensorLatencyCannotExpireNewLegacyWait(bool timeout) {
  Fake::nvs.clear();
  Fake::reset();
  Fake::enqueue({200, "command=none\nauto_trigger_enabled=1\n"});
  Fake::enqueue({400, "{\"reason\":\"unknown_event\"}", "", false, "POST"});
  Fake::enqueue({timeout ? -11 : 200, "{}", "", true, "POST"});
  CaptureController controller;
  controller.begin();
  pumpUntil(controller, [] { return !Fake::requestSnapshot().empty(); });
  for (int count = 0; count < 3; ++count) tickAt(controller, millis() + 10);
  Fake::echoUS = 290;
  pumpUntil(controller, [] { return !reports("hcsr04_trigger").empty(); });
  Fake::sensorLatencyMS = 12;
  Fake::unblock();
  std::this_thread::sleep_for(std::chrono::milliseconds(20));
  tickAt(controller, millis() + 50);
  tickAt(controller, millis() + 100);
  int gets = 0;
  for (const auto& request : Fake::requestSnapshot()) if (request.method == "GET") ++gets;
  assert(gets >= 2 && "A new legacy wait must keep 100 ms command polling despite sensor latency");
}

int main() {
  std::cout << std::unitbuf;
  std::cout << "Running delayed command\n";
  delayedCommandKeepsReadingSensor();
  std::cout << "Running feeder stops\n";
  stalledWorkerCannotDelayFeederStop("max_run_timeout");
  stalledWorkerCannotDelayFeederStop("hcsr04");
  stalledWorkerCannotDelayFeederStop("unavailable");
  stalledWorkerCannotDelayFeederStop("boundary");
  stalledWorkerCannotDelayFeederStop("max_run_timeout", UINT32_MAX - 2000);
  std::cout << "Running report retry\n";
  terminalReportRetryDoesNotRepeatFeed();
  std::cout << "Running trigger retries\n";
  triggerRetriesRetainId(false);
  triggerRetriesRetainId(true);
  lateFruitWhileTerminalReportIsHeld();
  unavailableWorkerDoesNotStartMotion();
  captureAndSorterKeepExistingPhases();
  sensorLatencyCannotExpireNewLegacyWait(false);
  sensorLatencyCannotExpireNewLegacyWait(true);
  Fake::nvs.clear();
  Fake::reset();
  std::cout << "PASS: 14 control/network scenarios; sensor latency, exact max-run, rollover, retries, gate/sorter phases and actuator ownership\n";
}
