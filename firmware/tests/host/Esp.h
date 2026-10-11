#pragma once
#include "Arduino.h"
struct FakeEsp { uint64_t getEfuseMac() { return 1; } };
inline FakeEsp ESP;
