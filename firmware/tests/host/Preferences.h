#pragma once
#include "FakePlatform.h"
#include <cstring>
namespace Fake {
inline std::vector<uint8_t> nvs;
inline bool nvsFail = false;
inline bool wifiNvsFail = false;
inline std::vector<uint8_t> wifiNvs;
inline bool seedWifi = true;
}
class Preferences {
 public:
  bool begin(const char* name, bool) { wifi_ = std::string(name) == "pf-wifi"; return !(wifi_ ? Fake::wifiNvsFail : Fake::nvsFail); }
  size_t getBytesLength(const char*) {
    if (wifi_ && Fake::seedWifi && Fake::wifiNvs.empty()) {
      Fake::wifiNvs.resize(152, 0);
      Fake::wifiNvs[0] = 1;
      std::memcpy(Fake::wifiNvs.data() + 4, "host-test", 9);
      std::memcpy(Fake::wifiNvs.data() + 37, "fake-password", 13);
    }
    return data().size();
  }
  size_t getBytes(const char*, void* target, size_t size) {
    if (data().size() != size) return 0;
    std::memcpy(target, data().data(), size);
    return size;
  }
  size_t putBytes(const char*, const void* source, size_t size) {
    if (!wifi_) Fake::assertControlThread();
    if (wifi_ ? Fake::wifiNvsFail : Fake::nvsFail) return 0;
    const auto* bytes = static_cast<const uint8_t*>(source);
    data().assign(bytes, bytes + size);
    return size;
  }
  void end() {}
 private:
  bool wifi_ = false;
  std::vector<uint8_t>& data() { return wifi_ ? Fake::wifiNvs : Fake::nvs; }
};
