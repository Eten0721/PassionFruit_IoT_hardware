#include <ESP32Servo.h>

/*
  ESP32 + 3 x SG90 power stability test.

  Wiring:
  - SG90 red wire   -> 5V
  - SG90 brown wire -> GND
  - SG90 orange wire:
      Gate 1 -> GPIO 18
      Gate 2 -> GPIO 19
      Gate 3 -> GPIO 21

  Important:
  - Servo GND and ESP32 GND must be connected together.
  - If the ESP32 reboots, Serial Monitor disconnects, servos jitter heavily,
    or the power bank shuts down, the microUSB supply is probably not stable
    enough for three SG90 servos.
  - If the test is unstable, power the SG90 servos from a separate 5V supply
    and keep the ESP32 GND and servo power GND common.
*/

Servo gate1Servo;
Servo gate2Servo;
Servo gate3Servo;

const int gate1Pin = 18;
const int gate2Pin = 19;
const int gate3Pin = 21;

const int BLOCK_ANGLE = 45;
const int RELEASE_ANGLE = 0;

const unsigned long SINGLE_MOVE_DELAY_MS = 700;
const unsigned long GROUP_MOVE_DELAY_MS = 1200;
const unsigned long LOOP_DELAY_MS = 2000;

void moveSingleGate(const char* label, Servo& servo);
void moveAllGates(int angle);
void printGatePins();

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("=== ESP32 SG90 Triple Servo Power Test ===");
  printGatePins();

  gate1Servo.attach(gate1Pin);
  gate2Servo.attach(gate2Pin);
  gate3Servo.attach(gate3Pin);

  Serial.print("Initial position: BLOCK ");
  Serial.print(BLOCK_ANGLE);
  Serial.println(" degrees");
  moveAllGates(BLOCK_ANGLE);
  delay(GROUP_MOVE_DELAY_MS);
}

void loop() {
  Serial.println();
  Serial.println("--- Single servo test: 45 -> 0 -> 45 ---");
  moveSingleGate("Gate 1", gate1Servo);
  moveSingleGate("Gate 2", gate2Servo);
  moveSingleGate("Gate 3", gate3Servo);

  Serial.println();
  Serial.println("--- Group servo test: all gates 45 -> 0 -> 45 ---");
  Serial.print("All gates release: ");
  Serial.print(RELEASE_ANGLE);
  Serial.println(" degrees");
  moveAllGates(RELEASE_ANGLE);
  delay(GROUP_MOVE_DELAY_MS);

  Serial.print("All gates block: ");
  Serial.print(BLOCK_ANGLE);
  Serial.println(" degrees");
  moveAllGates(BLOCK_ANGLE);
  delay(GROUP_MOVE_DELAY_MS);

  Serial.println("One test cycle completed. Watch for reset, jitter, or power-bank shutdown.");
  delay(LOOP_DELAY_MS);
}

void moveSingleGate(const char* label, Servo& servo) {
  Serial.print(label);
  Serial.print(" release: ");
  Serial.print(RELEASE_ANGLE);
  Serial.println(" degrees");
  servo.write(RELEASE_ANGLE);
  delay(SINGLE_MOVE_DELAY_MS);

  Serial.print(label);
  Serial.print(" block: ");
  Serial.print(BLOCK_ANGLE);
  Serial.println(" degrees");
  servo.write(BLOCK_ANGLE);
  delay(SINGLE_MOVE_DELAY_MS);
}

void moveAllGates(int angle) {
  gate1Servo.write(angle);
  gate2Servo.write(angle);
  gate3Servo.write(angle);
}

void printGatePins() {
  Serial.print("Gate 1 signal pin: GPIO ");
  Serial.println(gate1Pin);
  Serial.print("Gate 2 signal pin: GPIO ");
  Serial.println(gate2Pin);
  Serial.print("Gate 3 signal pin: GPIO ");
  Serial.println(gate3Pin);
}
