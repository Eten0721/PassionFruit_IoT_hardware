#pragma once
#include "FakePlatform.h"
using TaskHandle_t = void*;
using BaseType_t = int;
constexpr int pdPASS = 1;
inline uint32_t pdMS_TO_TICKS(uint32_t value) { return value; }
