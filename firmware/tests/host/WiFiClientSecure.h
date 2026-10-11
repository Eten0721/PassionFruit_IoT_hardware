#pragma once
#include "Arduino.h"
#include <memory>
struct sslclient_context { int ssl_ctx = 0; };
constexpr int MBEDTLS_ERR_SSL_WANT_READ = -2;
constexpr int MBEDTLS_ERR_SSL_WANT_WRITE = -3;
inline int mbedtls_ssl_write(int*, const uint8_t*, size_t size) { Fake::now += Fake::tlsWriteDelayMS; return std::min(size, size_t(Fake::tlsWriteChunk)); }
class WiFiClientSecure {
 protected:
  std::shared_ptr<sslclient_context> sslclient = std::make_shared<sslclient_context>();
 public:
  virtual ~WiFiClientSecure() = default;
  void setInsecure() {}
  void setTimeout(uint32_t) {}
  void setHandshakeTimeout(uint32_t) {}
  virtual int connect(const char*, uint16_t, int32_t) { return 1; }
  virtual size_t write(uint8_t) { return 1; }
  virtual size_t write(const uint8_t*, size_t size) { return size; }
  virtual int available() { return 1; }
  virtual uint8_t connected() { return true; }
  virtual int read() { return 65; }
  virtual int read(uint8_t*, size_t) { return 1; }
  virtual size_t readBytes(char*, size_t size) { return size; }
  void stop() { std::lock_guard<std::mutex> lock(Fake::mutex); ++Fake::connectionStops; }
};
