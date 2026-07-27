#include <ESP32Servo.h>

/*
  ESP32 + MG996R position test.

  Wiring:
  - MG996R signal wire -> GPIO 25
  - MG996R power wires -> external servo power supply
  - External power supply GND -> ESP32 GND (common ground is required)

  Safety:
  - Do not power the MG996R from the ESP32 5 V or 3.3 V pin.
  - Disconnect power immediately if the servo hits a mechanical stop, becomes
    unusually hot, jitters heavily, or causes the ESP32 to restart.

  Serial Monitor:
  - Baud rate: 115200
  - Line ending: Newline or Both NL & CR
  - Accepted angles: any whole number from 0 to 180
*/

namespace {

constexpr int kServoPin = 25;
constexpr int kStartupAngle = 90;
constexpr int kMinimumAngle = 0;
constexpr int kMaximumAngle = 180;
constexpr unsigned long kPositionHoldMS = 2000UL;

Servo mg996rServo;

void printPrompt();
void printAllowedRange();
bool parseAngle(const String& input, int& angle);
bool isAngleInRange(int angle);
void moveServoTo(int angle);

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("=== ESP32 MG996R Position Test ===");
  Serial.print("Signal pin: GPIO ");
  Serial.println(kServoPin);
  Serial.println("Power the servo externally and connect all grounds together.");

  mg996rServo.attach(kServoPin);
  Serial.print("Startup position: ");
  Serial.print(kStartupAngle);
  Serial.println(" degrees");
  mg996rServo.write(kStartupAngle);
  delay(kPositionHoldMS);

  printAllowedRange();
  printPrompt();
}

void loop() {
  if (!Serial.available()) {
    return;
  }

  String input = Serial.readStringUntil('\n');
  input.trim();

  if (input.length() == 0) {
    Serial.println("Invalid input: enter one angle as a number.");
    printPrompt();
    return;
  }

  int angle = 0;
  if (!parseAngle(input, angle)) {
    Serial.print("Invalid input: '");
    Serial.print(input);
    Serial.println("' is not a non-negative whole number.");
    printAllowedRange();
    printPrompt();
    return;
  }

  if (!isAngleInRange(angle)) {
    Serial.print("Angle out of range: ");
    Serial.print(input);
    Serial.println(". The servo was not moved.");
    printAllowedRange();
    printPrompt();
    return;
  }

  moveServoTo(angle);
  printPrompt();
}

namespace {

void printPrompt() {
  Serial.println("Enter an angle, then press Enter:");
}

void printAllowedRange() {
  Serial.println("Allowed range: any whole number from 0 to 180 degrees");
}

bool parseAngle(const String& input, int& angle) {
  int parsedAngle = 0;

  for (size_t index = 0; index < input.length(); ++index) {
    if (!isDigit(input[index])) {
      return false;
    }

    parsedAngle = (parsedAngle * 10) + (input[index] - '0');
    if (parsedAngle > kMaximumAngle) {
      angle = parsedAngle;
      return true;
    }
  }

  angle = parsedAngle;
  return true;
}

bool isAngleInRange(int angle) {
  return angle >= kMinimumAngle && angle <= kMaximumAngle;
}

void moveServoTo(int angle) {
  Serial.print("Moving MG996R to ");
  Serial.print(angle);
  Serial.println(" degrees...");
  mg996rServo.write(angle);
  delay(kPositionHoldMS);
  Serial.println("Observation period complete.");
}

}  // namespace
