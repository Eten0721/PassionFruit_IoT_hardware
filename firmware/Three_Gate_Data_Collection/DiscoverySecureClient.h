#pragma once
#include <WiFiClientSecure.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

// HTTPClient's per-read timeout alone permits indefinitely trickling headers.
class DiscoverySecureClient : public WiFiClientSecure {
 public:
  void setDiscoveryDeadline(uint32_t deadline) { deadline_ = deadline; bounded_ = true; }
  void clearDiscoveryDeadline() { bounded_ = false; }
  void boundRequest(uint32_t budgetMS) {
    const uint32_t candidate = millis() + budgetMS;
    if (!bounded_ || static_cast<int32_t>(candidate - deadline_) < 0) setDiscoveryDeadline(candidate);
  }

  int connect(const char* host, uint16_t port, int32_t timeout) override {
    if (!bounded_) return WiFiClientSecure::connect(host, port, timeout);
    const uint32_t remaining = remainingMS();
    if (remaining < 2000UL) return 0;
    setHandshakeTimeout(remaining / 2000UL);
    return WiFiClientSecure::connect(host, port, min(uint32_t(timeout), remaining / 2));
  }
  size_t write(uint8_t value) override { return write(&value, 1); }
  size_t write(const uint8_t* buffer, size_t size) override {
    if (!bounded_) return WiFiClientSecure::write(buffer, size);
    size_t sent = 0;
    while (sent < size && remainingMS() > 0) {
      // Core 3.3.11 keeps this TLS socket nonblocking; do not reset the total deadline on progress.
      const int result = mbedtls_ssl_write(&sslclient->ssl_ctx, buffer + sent, min(size - sent, size_t(4096)));
      if (result > 0) sent += result;
      else if (result == MBEDTLS_ERR_SSL_WANT_READ || result == MBEDTLS_ERR_SSL_WANT_WRITE) vTaskDelay(pdMS_TO_TICKS(1));
      else { WiFiClientSecure::stop(); return sent; }
    }
    return sent;
  }
  int available() override { return remainingMS() == 0 ? 0 : WiFiClientSecure::available(); }
  uint8_t connected() override { return remainingMS() == 0 ? 0 : WiFiClientSecure::connected(); }
  int read() override {
    const uint32_t remaining = remainingMS();
    if (remaining == 0) return -1;
    if (bounded_) setTimeout(remaining);
    return WiFiClientSecure::read();
  }
  int read(uint8_t* buffer, size_t size) override {
    const uint32_t remaining = remainingMS();
    if (remaining == 0) return -1;
    if (bounded_) setTimeout(remaining);
    return WiFiClientSecure::read(buffer, size);
  }
  size_t readBytes(char* buffer, size_t size) override {
    if (!bounded_) return WiFiClientSecure::readBytes(buffer, size);
    size_t count = 0;
    while (count < size && remainingMS() > 0) {
      const int value = read();
      if (value >= 0) buffer[count++] = static_cast<char>(value);
      else vTaskDelay(pdMS_TO_TICKS(1));
    }
    return count;
  }

 private:
  uint32_t deadline_ = 0;
  bool bounded_ = false;
  uint32_t remainingMS() {
    if (!bounded_) return UINT32_MAX;
    const int32_t remaining = static_cast<int32_t>(deadline_ - millis());
    if (remaining > 0) return static_cast<uint32_t>(remaining);
    WiFiClientSecure::stop();
    setTimeout(1);
    return 0;
  }
};
