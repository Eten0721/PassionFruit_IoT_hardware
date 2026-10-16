# 架構圖

此資料夾存放以 [Archify](https://github.com/tt-a1i/archify) 產生的專案架構圖。每張圖一個子資料夾，命名為 `<圖類型>-<主題>-<YYYYMMDD-HHMMSS>/`：

- `candidate.json`：圖的來源描述，修改後重新產生 HTML。
- `*.html`：可直接用瀏覽器開啟的互動架構圖。

圖中的原始碼行號對應產生當時的 commit，程式變更後可能過期；架構與規格仍以 `docs/` 為準。

| 圖 | 內容 |
|---|---|
| [architecture-project-overview-20261017-024459](architecture-project-overview-20261017-024459/passionfruit-iot-overview.html) | 系統總覽：Dashboard、手機、Django、ESP32、模型推論與通知 |
