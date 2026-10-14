#pragma once
#include "FreeRTOS.h"
#include "WiFi.h"
inline int xTaskCreate(void (*entry)(void*), const char*, uint32_t, void* context, int, TaskHandle_t* handle) {
  if (Fake::failTaskCreation) return 0;
  *handle = context;
  Fake::workers.emplace_back([=] { entry(context); });
  return pdPASS;
}
inline void vTaskDelay(uint32_t) {
  WiFi.pollEvents();
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
}
inline void vTaskDelete(TaskHandle_t) {}
inline int xTaskCreatePinnedToCore(void (*entry)(void*), const char* name, uint32_t stack,
                                 void* context, int priority, TaskHandle_t* handle, BaseType_t core) {
  assert(core == 0);
  return xTaskCreate(entry, name, stack, context, priority, handle);
}
inline uint32_t uxTaskGetStackHighWaterMark(TaskHandle_t) { return 16384; }
