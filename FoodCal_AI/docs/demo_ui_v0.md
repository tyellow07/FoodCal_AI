# 本機 Demo UI v0

基準：24423f5c80cee8627684756a4b377c23de6b8be6。UI 重用 Diagnostic Fixture、FoodDetector 與 RecipeRetriever，不改變既有 CLI 或資料契約。

## 啟動

2026-11-19 起使用專案 `.venv`（README「換電腦啟動」已安裝 requirements-ui.txt）；舊版 `work/demo_ui_venv` 已不存在：

```bash
.venv/bin/python -m streamlit run src/ui/app.py          # macOS / Linux
.\.venv\Scripts\python.exe -m streamlit run src/ui/app.py  # Windows
```

瀏覽 http://127.0.0.1:8501。服務預設只綁定 loopback，關閉 Streamlit usage telemetry，限制上傳 10 MB。
停止服務使用 Ctrl+C。

新環境可建立獨立 UI 環境；下列命令假設基底 Python 已具有專案原有 Python 依賴，並非完整依賴鎖定：

```powershell
python -m venv --system-site-packages work/demo_ui_venv
.\work\demo_ui_venv\Scripts\python.exe -m pip install -r requirements-ui.txt
```

## 操作與資料邊界

- 預設 Fixture Demo：按「顯示 Fixture 診斷」，顯示合成畫布、合成辨識框及既有 run_demo() 的完整結果。沒有權重也能運作。
- 真實 YOLO：明確選此模式、上傳 PNG/JPEG/WEBP，再按「開始 YOLO 推論」。選圖、切換模式或瀏覽頁面不觸發模型。單次按鈕只呼叫一次 FoodDetector.detect_food，無自動重試。
- 真實模式重用本機 FoodDetector 的預設權重和 15 類 taxonomy；缺少權重或類別不符顯示 blocked。模型依使用者 session 延遲建立並重用；切換模式或清除會移除 session 中的模型引用。
- 食譜合成先驗需另勾選 synthetic/test_only，預設關閉；沒有正式 catalog 時 Retrieval 回 unavailable。UI 不將食譜 fixture 原文套用為照片食材，真實模式的 Parsing/Mapping/Nutrition 維持 blocked。
- 框線只來自 Detection Prediction，顯示 class_name、confidence 和 detection_id，表格保留 bounding_box。沒有偵測不代表沒有食物；confidence 不是食譜或食材真實機率。
- Fixture 路徑保留既有 partial/needs_review/test_only、evidence/source/review/trust 診斷。熱量固定 unavailable，照片食用重量固定 null，營養資格固定 false。「照片份量與熱量」區塊（2026-11-11）顯示 Portion Contract v0 的 `photo_energy_estimate`：逐項列出無法估算照片重量的原因，不顯示任何重量或熱量數值。

CLI 的 validation YOLO 路徑會寫入私有 audit 並要求 validation 核准紀錄，因此 UI 上傳模式不呼叫該落盤入口；它直接共用既有 Detection/Retrieval API。UI 上傳不是 GT 評估或 validation 通過聲明。

## 隱私與限制

上傳解碼、圖片、結果、框線及 backend 訊息皆只在記憶體處理，程式不使用上傳檔名、不建立暫存圖片或私有 audit。輸出中的圖片與模型絕對路徑改成 memory/local URI，失敗不展示原始 exception 路徑。解碼時移除圖片 metadata，限制 20,000,000 pixels，使用明確的 Pillow 格式解碼器避免 Ultralytics 全域 Image.open 的自動安裝 fallback。

「清除本次圖片與結果」重設上傳 widget 與 session 結果。這是單人本機展示，不提供認證、跨 session 權限或安全抹除；瀏覽器、Streamlit 記憶體、推論 backend 緩衝與 OS swap 不在保證範圍內。不要將服務公開或當作正式資料核可工具。

## 驗證

```powershell
.\work\demo_ui_venv\Scripts\python.exe -B -m unittest tests.test_demo_ui -q
.\work\demo_ui_venv\Scripts\python.exe -B -m unittest discover -s tests -q
.\work\demo_ui_venv\Scripts\python.exe -B -m unittest discover -s experiments/recipe_parsing_gemma -p 'test*.py' -q
```

新增 14 項離線測試：10 項 Adapter、4 項 Streamlit AppTest；涵蓋預設不載入模型、明確啟動、Prediction 不變、來源不足、錯誤圖片、未知類別、缺權重、無落盤、GT 不繪製及清除/切換模式。
初版 UI 環境主測試 376 passed、2 skipped；基底 Python 372 passed、6 skipped（含 4 個選用 UI 測試）。初版開發沒有執行真實 YOLO/Gemma，真實模式以替身驗證。

實驗離線測試 33 passed；本機 Streamlit 啟動健康檢查 HTTP 200，測試服務已停止。9 個受保護資源及 18 份 TFDA 私有證據 hash 不變，歷史 YOLO audit 數量仍為 1。新增檔案空白與敏感路徑/憑證模式檢查通過；測試中兩個 PRIVATE 路徑是人工錯誤替身，並非本機真實路徑。未暫存或提交任何檔案。

測試介面與上傳 API 參考官方文件：[AppTest](https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest)、[file_uploader](https://docs.streamlit.io/develop/api-reference/widgets/st.file_uploader)。

## 單圖操作確認與標籤修正（2026-10-12）

使用者回報真實 YOLO 單圖操作成功，網頁可接收食物照片並顯示辨識結果。這是使用者操作確認，非本輪重新推論或獨立模型評估。畫面 confidence=0.565，先前涼麵 audit=0.785261；沒有核對兩者圖片 hash 與完整推論設定，不能直接比較或判定模型退步。

標籤改為不透明深色底、白字及較大字型，靠近圖片上緣/右緣時將標籤移回畫布內；Prediction 座標、類別、confidence 不變。新增對比與邊界回歸測試。下一個主要任務是取得第一個可追溯、授權可用的真實食譜原文快照，建立明確來源 ID/hash → Gemma Parsing 的受控單例整合；尚未開始實作，不能用 synthetic candidates 取代真實來源。

修正後 UI 測試 15 passed；完整主測試 377 passed、2 skipped；實驗離線測試 33 passed。合成標籤預覽已人工檢視，預覽僅存於忽略的 work/ 目錄，不包含私人圖片。此 checkpoint 僅收錄 UI 程式、測試、設定、依賴清單及本文件；不收錄圖片、模型、私有 audit 或 TFDA 原始證據，禁止 Push，既有 Git 歷史隱私風險仍在。

## 中文摘要與照片方向（2026-11-19）

- 主畫面先顯示「結果摘要」：標題、熱量（只有 `photo_energy_estimate` computed 時才顯示 kcal 範圍，向外取整數）、辨識項目中文名稱、
  中文原因與下一步建議（`src/ui/explain.py`）。流程狀態、食譜候選與完整 JSON 收進「開發者細節」展開區。
- 頁首說明「估計值僅供參考，不是醫療或營養建議」；原本常駐的 unavailable／null／false 英文提示移除，資訊仍在開發者細節。
- `decode_upload` 先依 EXIF Orientation 轉正再清除 metadata：手機直拍照片的預覽、YOLO 輸入與框線座標一致。
- 測試：`tests/test_ui_explain.py` 6 項；`tests/test_demo_ui.py` 新增方向測試與摘要斷言。
- 頁首「這個模型認得哪些食物？」展開區列出 15 類台灣小吃（中文名）、校園餐多半不在類別內的說明，以及可估份量的三種食材。
- CLI：`python -B -m src.pipeline.diagnostic_demo --summary` 印出同樣的中文摘要（預設仍輸出 JSON，內容不變）。
- 手機測試：設定檔維持只接受本機；需要時依 `usability_test_v0.md` 第 4 節暫時以 `--server.address 0.0.0.0` 在可信任網路執行。
