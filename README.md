# 百香果 IoT 系統快速部署

Windows 電腦執行 Django，手機透過 `/camera/` 拍照，ESP32 則控制三個 SG90 閘門與 MG996R 分類器。這份 README 只說明如何在同學的 Windows 電腦快速復現並啟動系統。

## 1. 下載專案

先安裝 Git，再於 PowerShell 執行：

```powershell
git clone https://github.com/Eten0721/PassionFruit_IoT_hardware.git
cd PassionFruit_IoT_hardware
```

## 2. 建立 Python `3.10.20` 環境

以下兩種方式擇一使用。推薦 Anaconda，因為與目前開發環境一致。

### 方法 A：Anaconda（推薦）

安裝 Anaconda 或 Miniconda，開啟 Anaconda Prompt：

```powershell
conda create -n pf_iot_env python=3.10.20 pip -y
conda activate pf_iot_env
python --version
```

### 方法 B：Python 內建 `venv`

先在 Windows 安裝 CPython `3.10.20`，再於 repository 根目錄執行：

```powershell
py -3.10 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python --version
```

兩種方式的 `python --version` 都應顯示：

```text
Python 3.10.20
```

## 3. 安裝 Django 並建立本機設定

確認目前位於 repository 根目錄，且虛擬環境已啟用：

```powershell
python -m pip install -r Django_Server\requirements.txt
Copy-Item .env.example .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
notepad .env
```

將剛才產生的字串填入 `.env`：

```dotenv
DJANGO_SECRET_KEY=貼上剛才產生的隨機字串
```

目前資料採集由檔案系統管理，不使用 Django database model，因此不需要執行 migration。

## 4. A Plan：使用伊藤的 `iPhone 17 Pro` 熱點

預設讓 Windows 電腦、ESP32 與拍照手機都使用伊藤的 iPhone 個人熱點，Django 電腦固定為 `172.20.10.3`。

### 固定 Windows 電腦 IP

1. 先讓 Windows 電腦以自動取得 IP 的方式連上 iPhone 熱點。
2. 執行 `ipconfig /all`，記下 Wi-Fi 的 Subnet Mask；gateway 預期為 `172.20.10.1`。
3. 按 `Win + R`，輸入 `ncpa.cpl`。
4. 對「Wi-Fi」按右鍵 →「內容」→「網際網路通訊協定第 4 版（TCP/IPv4）」。
5. 選擇「使用下列的 IP 位址」，填入：
   - IP 位址：`172.20.10.3`
   - 子網路遮罩：使用前一步 `ipconfig /all` 顯示的值
   - 預設閘道：`172.20.10.1`
   - 慣用 DNS：`172.20.10.1`
6. 重新連線後執行 `ipconfig`，確認 IPv4 已變成 `172.20.10.3`。

不要在沒有確認的情況下固定填入 `255.255.255.0`；子網路遮罩以 iPhone 熱點當下提供的值為準。

### 開放 Windows 防火牆

以系統管理員身分開啟 PowerShell：

```powershell
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private
New-NetFirewallRule -DisplayName "PassionFruit Django 8000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private -RemoteAddress LocalSubnet
```

### 確認 ESP32 Server URL

正式 firmware 的本機設定檔位於：

```text
firmware/Three_Gate_Data_Collection/secrets.h
```

A Plan 的兩個 URL 應為：

```cpp
const char* commandUrl = "https://172.20.10.3:8000/api/esp32/command/?format=text";
const char* reportUrl = "https://172.20.10.3:8000/api/esp32/report/";
```

`secrets.h` 也必須填入 iPhone 熱點的 SSID 與密碼，而且不可提交到 Git。

## 5. 啟動系統

在 `Django_Server` 目錄執行：

```powershell
python manage.py runsslserver 0.0.0.0:8000
```

電腦開啟 Dashboard：

```text
https://172.20.10.3:8000/dashboard/
```

手機開啟相機頁：

```text
https://172.20.10.3:8000/camera/
```

第一次開啟會顯示自簽憑證警告。確認網址是自己的 Django 電腦後，選擇繼續前往並允許相機權限。手機頁面使用期間不可鎖屏或切到背景。

部署成功時應符合：

- 電腦可以開啟 Dashboard。
- 手機可以開啟相機並顯示預覽。
- ESP32 Serial 顯示 Wi-Fi connected，且能向 Django 輪詢 command。

## 6. B Plan：改用同學家的 Wi-Fi

離開 iPhone 熱點後，原本固定的 `172.20.10.3` 不能直接帶到其他網路使用。

1. 到 Windows 的 TCP/IPv4 設定，改回「自動取得 IP 位址」與「自動取得 DNS 伺服器位址」。
2. 讓 Windows 電腦與手機連上同學家的同一個 Wi-Fi；不要使用訪客網路。
3. 執行 `ipconfig`，找到實際 Wi-Fi IPv4，例如 `192.168.0.54`。
4. 若本機還沒有 `secrets.h`，先於 repository 根目錄執行：

```powershell
Copy-Item firmware\Three_Gate_Data_Collection\secrets.example.h firmware\Three_Gate_Data_Collection\secrets.h
```

5. 修改 `firmware/Three_Gate_Data_Collection/secrets.h` 的四項設定：
   - `ssid`：同學家的 Wi-Fi 名稱
   - `password`：同學家的 Wi-Fi 密碼
   - `commandUrl`：改成新的電腦 IPv4
   - `reportUrl`：改成新的電腦 IPv4
6. 使用既有的 ESP32 燒錄環境重新燒入正式 firmware。若尚未準備燒錄環境，請參閱 [ESP32 快速燒錄準備](Necessary_library/README.md)。
7. Django 仍執行 `python manage.py runsslserver 0.0.0.0:8000`，不需修改 Django 或 `.env`。
8. 手機改用新的電腦 IP 開啟 `/camera/`。

若新 IP 是 `192.168.0.54`，`secrets.h` 應改為：

```cpp
const char* ssid = "同學家的 Wi-Fi 名稱";
const char* password = "同學家的 Wi-Fi 密碼";
const char* commandUrl = "https://192.168.0.54:8000/api/esp32/command/?format=text";
const char* reportUrl = "https://192.168.0.54:8000/api/esp32/report/";
```

手機網址則改為：

```text
https://192.168.0.54:8000/camera/
```

如果同學家的路由器啟用了 client isolation，裝置之間可能無法連線，此時改回 iPhone 熱點方案。

## 7. Dataset 位置

照片、分類資料與 `metadata.csv` 位於：

```text
Django_Server/dataset/
```

此目錄已被 Git 忽略，`git push` 不會備份 dataset。需要保留照片時，請另外備份整個 `Django_Server/dataset/`。
