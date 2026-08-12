#include <Arduino.h>
#include <ESP32Servo.h>

namespace {

constexpr uint8_t kServoPin = 23;
constexpr uint16_t kAttachMinUS = 1000;
constexpr uint16_t kAttachMaxUS = 2000;
constexpr uint16_t kDefaultStopUS = 1500;
constexpr uint16_t kMinimumStopUS = 1400;
constexpr uint16_t kMaximumStopUS = 1600;
constexpr uint32_t kMinimumRunMS = 100;
constexpr uint32_t kMaximumRunMS = 3000;
constexpr size_t kMaximumCommandLength = 64;

Servo feeder;
String commandBuffer;
uint16_t stopPulseUS = kDefaultStopUS;
uint16_t activePulseUS = kDefaultStopUS;
uint32_t stopAtMS = 0;
bool running = false;

bool timeReached(uint32_t currentTime, uint32_t deadline) {
  return static_cast<int32_t>(currentTime - deadline) >= 0;
}

void printHelp() {
  Serial.println();
  Serial.println("Commands:");
  Serial.println("  status                 Show the current output state");
  Serial.println("  stop                   Stop immediately");
  Serial.println("  stop <1400..1600 us>   Set and apply the neutral pulse (5 us step)");
  Serial.println("  run <1000..2000 us> <100..3000 ms>");
  Serial.println("                         Run once, then stop automatically");
  Serial.println("  help                   Show this help");
  Serial.println();
  Serial.println("Start with: run 1300 1000");
  Serial.println("Opposite direction example: run 1700 1000");
}

void stopMotor(const char* reason) {
  feeder.writeMicroseconds(stopPulseUS);
  activePulseUS = stopPulseUS;
  stopAtMS = 0;
  running = false;
  Serial.print("STOP reason=");
  Serial.print(reason);
  Serial.print(" pulse_us=");
  Serial.println(stopPulseUS);
}

void printStatus() {
  Serial.print("STATUS pin=");
  Serial.print(kServoPin);
  Serial.print(" running=");
  Serial.print(running ? "yes" : "no");
  Serial.print(" output_us=");
  Serial.print(activePulseUS);
  Serial.print(" stop_us=");
  Serial.println(stopPulseUS);
}

bool parseUnsigned(const String& text, uint32_t& value) {
  if (text.length() == 0) {
    return false;
  }
  for (size_t index = 0; index < text.length(); ++index) {
    if (!isDigit(text[index])) {
      return false;
    }
  }
  value = static_cast<uint32_t>(text.toInt());
  return true;
}

void handleStopCommand(const String& argument) {
  if (argument.length() == 0) {
    stopMotor("serial_command");
    return;
  }

  uint32_t requestedStopUS = 0;
  if (!parseUnsigned(argument, requestedStopUS) ||
      requestedStopUS < kMinimumStopUS ||
      requestedStopUS > kMaximumStopUS ||
      requestedStopUS % 5 != 0) {
    Serial.println("ERROR stop pulse must be 1400..1600 us in 5 us steps");
    return;
  }

  stopPulseUS = static_cast<uint16_t>(requestedStopUS);
  stopMotor("neutral_updated");
}

void handleRunCommand(const String& arguments) {
  if (running) {
    Serial.println("ERROR motor is already running; send stop first");
    return;
  }

  const int separator = arguments.indexOf(' ');
  if (separator <= 0 || separator >= static_cast<int>(arguments.length()) - 1) {
    Serial.println("ERROR usage: run <pulse_us> <duration_ms>");
    return;
  }

  const String pulseText = arguments.substring(0, separator);
  String durationText = arguments.substring(separator + 1);
  durationText.trim();
  uint32_t pulseUS = 0;
  uint32_t durationMS = 0;
  if (!parseUnsigned(pulseText, pulseUS) ||
      !parseUnsigned(durationText, durationMS)) {
    Serial.println("ERROR pulse and duration must be integers");
    return;
  }
  if (pulseUS < kAttachMinUS || pulseUS > kAttachMaxUS || pulseUS % 10 != 0) {
    Serial.println("ERROR run pulse must be 1000..2000 us in 10 us steps");
    return;
  }
  if (pulseUS == stopPulseUS) {
    Serial.println("ERROR run pulse must differ from the stop pulse");
    return;
  }
  if (durationMS < kMinimumRunMS || durationMS > kMaximumRunMS) {
    Serial.println("ERROR duration must be 100..3000 ms");
    return;
  }

  activePulseUS = static_cast<uint16_t>(pulseUS);
  feeder.writeMicroseconds(activePulseUS);
  stopAtMS = millis() + durationMS;
  running = true;
  Serial.print("RUN pulse_us=");
  Serial.print(activePulseUS);
  Serial.print(" duration_ms=");
  Serial.println(durationMS);
}

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) {
    return;
  }

  const int separator = command.indexOf(' ');
  String name = separator < 0 ? command : command.substring(0, separator);
  String arguments = separator < 0 ? "" : command.substring(separator + 1);
  name.toLowerCase();
  arguments.trim();

  if (name == "help") {
    printHelp();
    return;
  }
  if (name == "status") {
    printStatus();
    return;
  }
  if (name == "stop") {
    handleStopCommand(arguments);
    return;
  }
  if (name == "run") {
    handleRunCommand(arguments);
    return;
  }

  Serial.println("ERROR unknown command; send help");
}

void readSerialCommands() {
  while (Serial.available() > 0) {
    const char input = static_cast<char>(Serial.read());
    if (input == '\r' || input == '\n') {
      if (commandBuffer.length() > 0) {
        handleCommand(commandBuffer);
        commandBuffer = "";
      }
      continue;
    }

    if (commandBuffer.length() >= kMaximumCommandLength) {
      commandBuffer = "";
      Serial.println("ERROR command too long");
      continue;
    }
    commandBuffer += input;
  }
}

void enforceRunDeadline() {
  if (running && timeReached(millis(), stopAtMS)) {
    stopMotor("duration_elapsed");
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  feeder.attach(kServoPin, kAttachMinUS, kAttachMaxUS);
  feeder.writeMicroseconds(stopPulseUS);
  delay(100);

  Serial.println();
  Serial.println("=== MG996R 360-degree test ===");
  Serial.println("Boot is safe: the motor starts with the stop pulse only.");
  printStatus();
  printHelp();
}

void loop() {
  enforceRunDeadline();
  readSerialCommands();
  enforceRunDeadline();
}
