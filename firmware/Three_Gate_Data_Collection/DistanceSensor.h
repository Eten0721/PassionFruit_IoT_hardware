#pragma once

#include <Arduino.h>

class DistanceSensor {
 public:
  DistanceSensor(uint8_t trigPin, uint8_t echoPin);

  void begin();
  float readCentimeters(uint32_t* elapsedUS = nullptr) const;

 private:
  uint8_t trigPin_;
  uint8_t echoPin_;
};
