#pragma once

// Copy this file to secrets.h and fill in local Wi-Fi and Django server settings.
const char* ssid = "YOUR_WIFI_SSID";
const char* password = "YOUR_WIFI_PASSWORD";

// Django should be started with HTTPS, for example:
// C:\Users\qoqoo\anaconda3\envs\PF\python.exe -s manage.py runsslserver 0.0.0.0:8000
const char* commandUrl = "https://YOUR_DJANGO_SERVER_IP:8000/api/esp32/command/?format=text";
const char* reportUrl = "https://YOUR_DJANGO_SERVER_IP:8000/api/esp32/report/";
