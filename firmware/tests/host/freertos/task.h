#pragma once
#include "FreeRTOS.h"
inline int xTaskCreate(void (*entry)(void*), const char*, uint32_t, void* context, int, TaskHandle_t* handle) {
  if (Fake::failTaskCreation) return 0;
  *handle = context;
  Fake::workers.emplace_back([=] { entry(context); });
  return pdPASS;
}
inline void vTaskDelay(uint32_t) { std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
inline void vTaskDelete(TaskHandle_t) {}
