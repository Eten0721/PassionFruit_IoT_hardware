#include "CaptureController.h"

// Arduino only needs this thin entry point. Stateful capture, transport,
// sensor, and servo responsibilities live in their matching .h/.cpp modules.
CaptureController captureController;

void setup() {
  captureController.begin();
}

void loop() {
  captureController.tick();
}
