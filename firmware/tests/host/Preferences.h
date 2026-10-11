#pragma once
#include "FakePlatform.h"
#include <cstring>
namespace Fake {
inline std::vector<uint8_t> nvs;
inline bool nvsFail = false;
}
class Preferences {
 public:
  bool begin(const char*, bool) { return !Fake::nvsFail; }
  size_t getBytesLength(const char*) { return Fake::nvs.size(); }
  size_t getBytes(const char*, void* target, size_t size) {
    if (Fake::nvs.size() != size) return 0;
    std::memcpy(target, Fake::nvs.data(), size);
    return size;
  }
  size_t putBytes(const char*, const void* source, size_t size) {
    Fake::assertControlThread();
    if (Fake::nvsFail) return 0;
    const auto* bytes = static_cast<const uint8_t*>(source);
    Fake::nvs.assign(bytes, bytes + size);
    return size;
  }
  void end() {}
};
