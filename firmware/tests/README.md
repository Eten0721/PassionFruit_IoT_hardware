# Firmware 純邏輯測試

`classification_sequence_test.cpp` 在不依賴 Arduino runtime 的情況下，驗證 Gate 3／分類器複合流程的角度、時序、timeout 與 command 去重。

在 Repository 根目錄執行：

```powershell
C:\msys64\mingw64\bin\g++.exe -std=c++11 firmware\tests\classification_sequence_test.cpp firmware\Three_Gate_Data_Collection\ClassificationSequence.cpp -o $env:TEMP\classification_sequence_test.exe
& $env:TEMP\classification_sequence_test.exe
```

程式以 exit code `0` 表示全部 assertion 通過。
