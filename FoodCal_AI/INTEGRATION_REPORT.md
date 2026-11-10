# 2026-11-08 整合與安全審查

目標：tyellow07/I2P_I_Project 的 FoodCal_AI/；分支 feature/foodcal-ai-handoff。初始工作樹乾淨，origin 指向指定 Repository。來源以經審查的當前工作樹為準，沒有帶入來源 Git 歷史。

## 實際檔案變更

修改 4 檔：

- .gitignore：增加 7 個精確白名單項目，保留 default-deny 與私人資料封鎖。
- README.md：更新目前功能、環境與測試實況、分享邊界。
- tools/annotate_ingredient_pilot.py：同步最新人工／AI 草稿 GUI、明確決策、來源追蹤與保存驗證。
- tools/ingredient_box_edit.py：同步同時鐘刻度 revision 的排序修正。

新增 7 檔：

- AGENTS.md：接手規則與證據／分享界線。
- HANDOFF.md：模組入口、MVP 進度、命令、限制及後續任務。
- INTEGRATION_REPORT.md：本報告。
- docs/ingredient_pilot_ai_import_v0.md：去除私人樣本及標註 ID 的通用操作說明。
- tools/ingredient_ai_draft.py：proposal 驗證、明確決策及 append-only 來源保存。
- tests/test_ingredient_ai_draft.py：19 項合成邏輯測試。
- tests/test_ingredient_ai_gui.py：8 項合成 Tk GUI 測試。

無刪除。其餘既有 FoodCal_AI 檔案保留；Recipe Parsing、Nutrition Mapping、Visual Ingredient、Streamlit 與既有必要測試，在排除 CRLF/LF 差異後與最新來源一致。tests/test_food_detection.py 刻意保留先前安全分享版的合成 taxonomy 注入。原始空白 README／requirements 不覆蓋分享版。

先前安全分享副本 104 檔中，101 檔文字內容與目前目標相同；差異為 .gitattributes、.gitignore、README 的既有整合調整。另保留目標既有 SHARING_REPORT.md。本次程式同步的五檔以來源 bytes 原樣複製；新增操作文件重新撰寫，不複製私有標註紀錄。

## 範圍、隱私與授權檢查

檢查選入程式、tests、docs、examples 與設定的文字內容及檔案型別；掃描常見 GitHub/OpenAI/AWS 憑證、私鑰、真實使用者路徑及 email。僅保留原本測試用假路徑與 example.org 拒絕案例，沒有發現真實憑證或個人機器路徑。新增五個 Python 檔案內容為專案邏輯及合成測試，不含外部資料列、模型權重或第三方套件本體。

未攜入 data/runs/work/models/weights/outputs、圖片、私人 manifest、人工 revisions、GT 衍生逐樣本紀錄、TFDA 原件、實驗原始輸出、API Keys、環境或 Codex sessions。新操作文件的私人圖片判讀、實際 annotation ID 及私人驗證結果已移除。資料集統計／人工審查報告與未確認可分享的第三方內容沿用排除策略；詳細原始對照清單留在 Repository 外，不上傳。

既有團隊分享授權紀錄見 SHARING_REPORT.md。本次延續使用者指定的團隊整合範圍，不新增公開散布授權、開源 License 或外部模型／資料使用權。靜態掃描不能證明所有著作權或未知秘密；本次選入內容未發現新增第三方授權阻礙。原有團隊檔案不在本次改動範圍，也不宣稱審查了團隊整個歷史。

## 實際離線驗證

在隔離審查副本、Windows Python 3.12 執行：

- `python -B -m unittest discover -s tests -v`：398 項，386 passed、0 failures、0 errors、12 skipped。
- Skip 原因：4 項缺選用 Streamlit，8 項 Tcl/Tk 初始化不可用。跳過不算通過。
- `python -B -m src.pipeline.diagnostic_demo`：exit 0，partial；照片重量 null，熱量 unavailable。
- 68 個 Python 檔 AST 語法檢查通過；不產生 bytecode。

未收集的私有依賴測試另列 HANDOFF.md，不計入 skipped。沒有乾淨安裝、真實模型推論、訓練、資料下載或人工語意驗收。既有歷史 371 passed／42 passed 紀錄不是本次重跑結果。

## Commit／Push 判定

此次限定 11 檔變更可進入本機 Commit 審查；不要把外部 audit 目錄或私人工作資料加入 Git。原始專案與本機備份未作修改；團隊根 README、三份菜單與其他既有檔案保留。未自動暫存、Commit、Push、Merge 或更動分支歷史。

Push 尚不能作無條件保證：本輪 GitHub metadata 讀取回 404，尚未重新驗證可見性、寫入權限及遠端最新分支。404 不代表 Repository 不存在。實際 Push 前需確認指定團隊 Repository 的分享範圍與遠端狀態，僅推送 feature 分支，不 force、不直接覆寫 main。
