#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <Ultrasonic.h>
#include "secrets.h"

// 宣告硬體週邊物件
Ultrasonic ultrasonic(26, 27); // Trigger 腳位為 GPIO 26, Echo 腳位為 GPIO 27

// 全局安全網路連線用戶端物件
WiFiClientSecure client;

// 系統控制變數
const float triggerDistanceCM = 5.0;    // 觸發拍攝的超音波距離門檻 (公分)
const float rearmDistanceCM = 8.0;      // 百香果離開此距離後才允許下一次觸發
unsigned long lastTriggerTime = 0;      // 記錄上一次觸發嘗試的時間戳記
const unsigned long cooldownMS = 3000;  // 觸發後的冷卻時間 (毫秒)
const unsigned long wifiConnectTimeoutMS = 15000;  // 初次 Wi-Fi 連線最大等待時間
unsigned long lastPrintTime = 0;        // 紀錄上一次列印距離的時間戳記
bool triggerArmed = true;               // 避免同一顆百香果停留時重複觸發

bool connectWiFi(unsigned long timeoutMS);
bool sendCaptureTrigger();

void setup() {
  Serial.begin(115200);

  // 初始化安全連線設定：強制繞過本地端的自簽憑證檢查
  client.setInsecure();

  Serial.print("正在連線至 Wi-Fi 熱點: ");
  Serial.println(ssid);
  if (!connectWiFi(wifiConnectTimeoutMS)) {
    Serial.println("\n【網路錯誤】初次 Wi-Fi 連線逾時，進入主循環後會繼續重試。");
  }
}

void loop() {
  unsigned long currentTime = millis();

  // 動態 Wi-Fi 斷線自動重連防護邏輯，避免手機熱點斷線導致系統停住。
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\n【網路警報】Wi-Fi 連線中斷！正在嘗試背景自動重連...");
    WiFi.disconnect();
    WiFi.begin(ssid, password);

    unsigned long startAttemptTime = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < 3000) {
      delay(200);
      Serial.print(".");
    }
    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("\n【網路修復】Wi-Fi 背景重新連線成功。");
    } else {
      Serial.println("\n【網路錯誤】熱點重連逾時，等待下一次循環重試。");
    }
  }

  long duration = ultrasonic.timing();
  float distance = ultrasonic.convert(duration, Ultrasonic::CM);

  if (currentTime - lastPrintTime >= 500) {
    Serial.print("感測器當前讀取距離: ");
    Serial.print(distance);
    Serial.println(" cm");
    lastPrintTime = currentTime;
  }

  if (!triggerArmed && (distance <= 0 || distance > rearmDistanceCM)) {
    triggerArmed = true;
    Serial.println("感測範圍已清空，ESP32 trigger 重新待命。");
  }

  if (
    triggerArmed &&
    distance > 0 &&
    distance <= triggerDistanceCM &&
    currentTime - lastTriggerTime >= cooldownMS
  ) {
    Serial.println("\n【系統提示】距離小於等於 5 cm，發送 Django 拍攝 trigger...");
    lastTriggerTime = currentTime;

    if (sendCaptureTrigger()) {
      triggerArmed = false;
      Serial.println("trigger 已送出，等待百香果離開感測範圍後再重新待命。\n");
    }
  }

  delay(50);
}

bool connectWiFi(unsigned long timeoutMS) {
  WiFi.begin(ssid, password);

  unsigned long startAttemptTime = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < timeoutMS) {
    delay(500);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWi-Fi 連線成功");
    return true;
  }

  return false;
}

bool sendCaptureTrigger() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("HTTP 請求略過：Wi-Fi 尚未連線。");
    return false;
  }

  HTTPClient http;
  bool requestDelivered = false;

  http.begin(client, serverUrl);
  http.addHeader("Content-Type", "application/x-www-form-urlencoded");
  http.setTimeout(5000);

  int httpResponseCode = http.POST("");
  if (httpResponseCode > 0) {
    String response = http.getString();
    response.trim();

    Serial.print("Django 回應代碼: ");
    Serial.println(httpResponseCode);
    Serial.print("Django 回應內容: ");
    Serial.println(response);
    requestDelivered = httpResponseCode >= 200 && httpResponseCode < 300;
    if (!requestDelivered) {
      Serial.println("Django 未接受 trigger，本次不解除待命鎖。");
    }
  } else {
    Serial.print("HTTP 請求失敗，錯誤代碼: ");
    Serial.println(httpResponseCode);
  }

  http.end();
  client.stop();
  return requestDelivered;
}
