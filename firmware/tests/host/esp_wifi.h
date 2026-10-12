#pragma once
#include "WiFi.h"
constexpr int ESP_OK = 0;
inline int esp_wifi_scan_stop() {
  if (Fake::scanStopFail) return 1;
  Fake::scanInProgress = false;
  return ESP_OK;
}
