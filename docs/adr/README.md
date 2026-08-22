# Architecture Decision Records

ADR 記錄長期有效、跨模組或涉及安全與資料一致性的決策。現行操作數值與 wire contract 以 [`DATA_COLLECTION_SPEC.md`](../DATA_COLLECTION_SPEC.md) 為準。

| ADR | Status | 決策 |
|---|---|---|
| [0001](0001-django-central-three-station-capture.md) | Accepted | Django 中央三站採集流程 |
| [0002](0002-auto-trigger-lock-and-dataset-recovery.md) | Accepted | 自動觸發鎖定與 Dataset 復原 |
| [0003](0003-guarded-station-one-fast-path.md) | Accepted | 受守門的首站捷徑與有界重試 |
| [0004](0004-hcsr04-echo-voltage-safety.md) | Accepted | HC-SR04 Echo 電壓安全 |
| [0005](0005-initial-uniform-timing-profile.md) | Superseded | 初始統一 timing profile |
| [0006](0006-dashboard-managed-capture-timing.md) | Accepted | Dashboard 管理與 snapshot timing |
| [0007](0007-classification-after-data-commit.md) | Accepted | 資料提交後才執行實體分類 |
| [0008](0008-legacy-four-class-command-mapping.md) | Superseded | 舊四級分類 command mapping |
| [0009](0009-canonical-four-class-contract.md) | Accepted | 現行四級分類契約 |
| [0010](0010-independent-model-repository.md) | Accepted | 模型與硬體採獨立 Repository |
| [0011](0011-anthracnose-veto-ai-contract.md) | Superseded | 炭疽病一票否決 AI 契約 |
| [0012](0012-defect-ratio-ai-contract.md) | Accepted | 局部瑕疵比例與人工覆核 AI 契約 |
| [0013](0013-django-orchestrated-single-feed-auto-run.md) | Accepted | Django 協調單顆送料自動運轉 |
| [0014](0014-hcsr04-terminated-upstream-feed.md) | Accepted | HC-SR04 終止上游送料；現行機構背景見 ADR-0016 |
| [0015](0015-integrated-radial-paddle-feeder.md) | Superseded | 一體式徑向撥料送料筒；徑向撥片、送料馬達與供電由 ADR-0016 取代 |
| [0016](0016-hardware-platform-feeder-sorter-redesign.md) | Accepted | 拍攝平台、分槽盤送料、下置式分類器與雙電源重構 |

完成項目的實作過程、版本進度與 commit 歷史不另建 ADR，由 Git、tag 與 release 保存。
