#include <ESP32Servo.h>
#include <Ultrasonic.h>

/*
  ESP32 + HC-SR04 + 3 x SG90 trigger test.

  Goal:
  - When HC-SR04 reads a distance <= 5 cm, move three SG90 servos in order.
  - Gate 1: GPIO 18, 0 -> 60 degrees, keep position.
  - Gate 2: GPIO 19, 0 -> 60 degrees, keep position.
  - Gate 3: GPIO 21, 0 -> 60 degrees, keep position.
  - Wait 0.5 seconds, then return all gates to 0 degrees.

  Wiring:
  - HC-SR04 Trig -> GPIO 26
  - HC-SR04 Echo -> GPIO 27
  - SG90 signal wires:
      Gate 1 -> GPIO 18
      Gate 2 -> GPIO 19
      Gate 3 -> GPIO 21
  - Servo GND and ESP32 GND must be connected together.

  Trigger protection:
  - triggerArmed prevents repeated triggers while an object stays within range.
  - The system rearms only after distance is invalid or greater than 8 cm.
  - cooldownMS adds extra protection against noisy sensor readings.
*/

Ultrasonic ultrasonic(26, 27);

Servo gate1Servo;
Servo gate2Servo;
Servo gate3Servo;

const int gate1Pin = 18;
const int gate2Pin = 19;
const int gate3Pin = 21;

const int HOME_ANGLE = 0;
const int TRIGGERED_ANGLE = 60;

const float triggerDistanceCM = 5.0;
const float rearmDistanceCM = 8.0;

const unsigned long servoMoveDelayMS = 700;
const unsigned long returnDelayMS = 500;
const unsigned long cooldownMS = 3000;
const unsigned long distancePrintIntervalMS = 500;

unsigned long lastTriggerTime = 0;
unsigned long lastPrintTime = 0;
bool triggerArmed = true;
bool sequenceRunning = false;

float readDistanceCM();
void runGateSequence();
void moveGate(const char* label, Servo& servo, int angle);
void moveAllGates(int angle);
void printPins();

void setup() {
  Serial.begin(115200);
  delay(1000);

  Serial.println();
  Serial.println("=== HC-SR04 Triple SG90 Trigger Test ===");
  printPins();

  gate1Servo.attach(gate1Pin);
  gate2Servo.attach(gate2Pin);
  gate3Servo.attach(gate3Pin);

  Serial.println("Initializing all gates to 0 degrees.");
  moveAllGates(HOME_ANGLE);
  delay(servoMoveDelayMS);
}

void loop() {
  const unsigned long currentTime = millis();
  const float distanceCM = readDistanceCM();

  if (currentTime - lastPrintTime >= distancePrintIntervalMS) {
    Serial.print("Distance: ");
    Serial.print(distanceCM);
    Serial.println(" cm");
    lastPrintTime = currentTime;
  }

  if (!sequenceRunning && !triggerArmed && (distanceCM <= 0 || distanceCM > rearmDistanceCM)) {
    triggerArmed = true;
    Serial.println("Sensor area cleared. Trigger rearmed.");
  }

  if (
    !sequenceRunning &&
    triggerArmed &&
    distanceCM > 0 &&
    distanceCM <= triggerDistanceCM &&
    currentTime - lastTriggerTime >= cooldownMS
  ) {
    Serial.println();
    Serial.println("Trigger distance reached. Starting gate sequence.");
    triggerArmed = false;
    lastTriggerTime = currentTime;
    runGateSequence();
  }

  delay(50);
}

float readDistanceCM() {
  long duration = ultrasonic.timing();
  return ultrasonic.convert(duration, Ultrasonic::CM);
}

void runGateSequence() {
  sequenceRunning = true;

  moveGate("Gate 1", gate1Servo, TRIGGERED_ANGLE);
  delay(servoMoveDelayMS);

  moveGate("Gate 2", gate2Servo, TRIGGERED_ANGLE);
  delay(servoMoveDelayMS);

  moveGate("Gate 3", gate3Servo, TRIGGERED_ANGLE);
  delay(servoMoveDelayMS);

  Serial.print("All gates are at ");
  Serial.print(TRIGGERED_ANGLE);
  Serial.println(" degrees. Waiting before return.");
  delay(returnDelayMS);

  Serial.println("Returning all gates to 0 degrees.");
  moveAllGates(HOME_ANGLE);
  delay(servoMoveDelayMS);

  Serial.println("Gate sequence completed. Waiting for sensor area to clear.");
  sequenceRunning = false;
}

void moveGate(const char* label, Servo& servo, int angle) {
  Serial.print(label);
  Serial.print(" -> ");
  Serial.print(angle);
  Serial.println(" degrees");
  servo.write(angle);
}

void moveAllGates(int angle) {
  gate1Servo.write(angle);
  gate2Servo.write(angle);
  gate3Servo.write(angle);
}

void printPins() {
  Serial.println("HC-SR04 Trig: GPIO 26");
  Serial.println("HC-SR04 Echo: GPIO 27");
  Serial.print("Gate 1 signal: GPIO ");
  Serial.println(gate1Pin);
  Serial.print("Gate 2 signal: GPIO ");
  Serial.println(gate2Pin);
  Serial.print("Gate 3 signal: GPIO ");
  Serial.println(gate3Pin);
}
