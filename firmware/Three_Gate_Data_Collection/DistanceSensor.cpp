#include "DistanceSensor.h"

#include "Config.h"

DistanceSensor::DistanceSensor(uint8_t trigPin, uint8_t echoPin)
    : trigPin_(trigPin), echoPin_(echoPin) {}

void DistanceSensor::begin() {
  pinMode(trigPin_, OUTPUT);
  pinMode(echoPin_, INPUT);
  digitalWrite(trigPin_, LOW);
}

float DistanceSensor::readCentimeters(uint32_t* elapsedUS) const {
  const uint32_t startedAt = micros();

  digitalWrite(trigPin_, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin_, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin_, LOW);

  const unsigned long durationUS = pulseIn(
      echoPin_, HIGH, FirmwareConfig::kEchoPulseTimeoutUS);
  if (elapsedUS != nullptr) {
    *elapsedUS = micros() - startedAt;
  }

  if (durationUS == 0) {
    return 0.0F;
  }

  return static_cast<float>(durationUS) / 58.0F;
}
