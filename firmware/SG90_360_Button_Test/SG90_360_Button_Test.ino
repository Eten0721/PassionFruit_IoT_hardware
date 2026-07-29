#include <ESP32Servo.h>

/*
  ESP32 + 360-degree continuous-rotation SG90 button test.

  Wiring:
  - SG90 signal wire -> GPIO 23
  - SG90 power       -> external 4.8-6 V servo power supply
  - Servo power GND  -> ESP32 GND (common ground is required)
  - Button           -> onboard BOOT button (GPIO 0, active LOW)

  Safety:
  - Do not power the servo from the ESP32 3.3 V pin.
  - Do not hold BOOT while powering on or resetting the ESP32, or it may enter
    download mode instead of running this sketch.

  Calibration:
  - Adjust STOP_US until the servo does not creep.
  - Adjust RUN_MS until one press produces the required movement.
  - If 1700 us rotates in the wrong direction, try 1300 us for DRIVE_US.
*/

namespace {

constexpr int kServoPin = 23;
constexpr int kButtonPin = 0;

constexpr int STOP_US = 1500;
constexpr int DRIVE_US = 1200;
constexpr unsigned long RUN_MS = 183UL;

constexpr unsigned long kDebounceMS = 25UL;

Servo feederServo;

}  // namespace

void setup() {
  pinMode(kButtonPin, INPUT_PULLUP);

  feederServo.attach(kServoPin, 1000, 2000);
  feederServo.writeMicroseconds(STOP_US);
}

void loop() {
  if (digitalRead(kButtonPin) != LOW) {
    return;
  }

  delay(kDebounceMS);
  if (digitalRead(kButtonPin) != LOW) {
    return;
  }

  feederServo.writeMicroseconds(DRIVE_US);
  delay(RUN_MS);
  feederServo.writeMicroseconds(STOP_US);

  while (digitalRead(kButtonPin) == LOW) {
    delay(10);
  }
  delay(kDebounceMS);
}
