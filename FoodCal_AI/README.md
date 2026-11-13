# FoodCal AI — 開發交接版

更新：2026-11-08。此目錄整合原始專案經審查的最新工作樹（含未 Commit 的 AI 輔助標註），供既有團隊 Repository 使用。先讀本頁，再讀 `AGENTS.md`、`HANDOFF.md` 與 `INTEGRATION_REPORT.md`。Repository 根目錄 README、菜單與團隊歷史保持原樣。

核心原則：**Recipe candidates may explain hidden calories, but are not visual proof.** 食譜候選只能提供隱藏食材／熱量的先驗；料理分類不能直接展開為照片食材，食譜份量不能當成照片重量。

## 現有能力

- Photo Recognition：Detection API、受控 YOLO 診斷；無合法本機權重時安全阻擋。
- Visual Ingredient Evidence：視覺證據 Adapter、逐樣本審查契約、合成 fixture；真實人工審查資料不隨庫分享。
- Annotation：Tk 人工畫框、Bounding Box 欄位編輯、AI proposal 匯入、明確新增／替換／拒絕、來源事件紀錄、append-only revision 與獨立複核；保存使用跨程序鎖與原子寫入（限同一台電腦的本機檔案系統）。匯入功能本身不呼叫 AI 模型。
- Recipe Parsing：Schema、Validator、Mock 與選用 Ollama Adapter；Nutrition Mapping、來源查核與人工審查原型。
- Streamlit Demo／CLI：合成資料離線展示；不是完整營養產品。
- Portion／Energy Contract v0：照片可見質量區間、秤重／校準方法、校準門檻、不確定性傳遞與 photo_calculation 營養串接閘門（`docs/photo_portion_estimation_contract_v0.md`）；沒有估算模型或校準資料，結果維持 unavailable。CLI、YOLO 診斷與 Streamlit 以 `photo_energy_estimate` 逐項顯示原因。
- Photo Item Source Review v0（2026-11-13）：照片食材對 TFDA 條目的受控人工審查，全程不經食譜，可產生 photo_calculation 營養基準（`docs/photo_item_source_review_design_v0.md` 第 12 節）；決策暫定、待團隊確認，只有合成測試資料，沒有真實審查或 gate 紀錄。

Portion Estimation 與 Nutrition Estimation 尚未完成照片實證整合。正式輸出維持 `photo_portion_grams=null`、`calorie_estimate=unavailable`、營養資格 false；沒有可信照片重量或正式食譜來源時不得補造結果。

## 換電腦啟動

使用 Python 3.12，在 Clone 的 `FoodCal_AI` 目錄執行：

```powershell
cd FoodCal_AI
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -m src.pipeline.diagnostic_demo
```

選用 Streamlit：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt
.\.venv\Scripts\python.exe -m streamlit run src/ui/app.py
```

UI 預設使用合成展示，服務位址以啟動輸出為準。Tk GUI 需要 Python 安裝的 Tk/Tcl 與 Pillow；用法見 `docs/ingredient_pilot_ai_import_v0.md`。沒有核准的本機圖片、manifest 與 workspace 時，先執行合成測試，不要建立假的私人資料以通過驗證。

依賴版本沿用既有分享版；本次未下載依賴、未驗證全新環境安裝或跨平台 lock。`requirements-model.txt` 為選用模型環境提示，使用前需確認第三方套件、權重與資料條款。

## 驗證與分享界線

本次離線測試共 398 項：386 passed、0 failures、0 errors、12 skipped。4 項缺 Streamlit；8 項無可用 Tcl/Tk 初始化環境。已執行的測試包含 19 項新增 AI 草稿邏輯測試；GUI 互動測試未算通過。完整範圍及未攜入的私有資料測試見 `HANDOFF.md`。2026-11-11 在 macOS／Python 3.12.0 修正三個測試檔的可攜性問題後，398 項全數通過、0 skipped（含 Streamlit 4 項與 Tk 8 項）；加入 Portion Contract v0 後為 422 項、再加入標註跨程序鎖後為 431 項、接入份量／熱量診斷輸出後為 435 項、2026-11-13 加入照片食材來源審查 v0 後為 465 項，皆全數通過，詳見 `HANDOFF.md`。

不包含 data/runs/work、圖片、模型權重、人工 revisions、私人 manifest、TFDA 營養原件、模型原始輸出、API Keys、虛擬環境或 Codex sessions。資料與權重須另行取得合法使用權；此目錄不是完整私人工作資料備份。

`.gitignore` 採精確白名單；新增檔須審查後加入，不使用 `git add -f`。既有 `SHARING_REPORT.md` 是 2026-10-19 歷史紀錄，當時測試數與分支流程不代表本輪。沒有新增開源 License，也未授予公開散布或第三方資料／模型權利。

本次未 Commit、Push、Merge 或訓練。變更準備於 `feature/foodcal-ai-handoff`；遠端可見性與寫入權限尚未重新確認，詳本輪報告。
