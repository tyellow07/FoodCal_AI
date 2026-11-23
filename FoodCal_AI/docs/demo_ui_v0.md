# 本機 Demo UI v0

基準：24423f5c80cee8627684756a4b377c23de6b8be6。UI 重用 Diagnostic Fixture、FoodDetector 與 RecipeRetriever，不改變既有 CLI 或資料契約。

## 啟動

2026-11-19 起使用專案 `.venv`（README「換電腦啟動」已安裝 requirements-ui.txt）；舊版 `work/demo_ui_venv` 已不存在：

```bash
.venv/bin/python -m streamlit run src/ui/app.py          # macOS / Linux
.\.venv\Scripts\python.exe -m streamlit run src/ui/app.py  # Windows
```

瀏覽 http://127.0.0.1:8501。服務預設只綁定 loopback，關閉 Streamlit usage telemetry，限制上傳 20 MB（2026-11-23 從 10 MB 調高，見最後一節）。
停止服務使用 Ctrl+C。

新環境可建立獨立 UI 環境；下列命令假設基底 Python 已具有專案原有 Python 依賴，並非完整依賴鎖定：

```powershell
python -m venv --system-site-packages work/demo_ui_venv
.\work\demo_ui_venv\Scripts\python.exe -m pip install -r requirements-ui.txt
```

## 操作與資料邊界

- 預設「合成展示（不需模型）」：按「顯示展示結果」，顯示合成畫布、合成辨識框及既有 run_demo() 的完整結果。沒有權重也能運作（2026-11-20 前名稱為 Fixture Demo／顯示 Fixture 診斷）。
- 「上傳照片辨識（需要模型權重）」（原「真實 YOLO」）：明確選此模式、上傳 PNG/JPEG/WEBP，再按「開始辨識」（原「開始 YOLO 推論」）。本機沒有權重或類別檔時，頁面直接說明並停用按鈕。選圖、切換模式或瀏覽頁面不觸發模型。單次按鈕只呼叫一次 FoodDetector.detect_food，無自動重試。
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

## 瀏覽器實測與版面修正（2026-11-20）

在 Chrome 實際操作（合成展示、上傳模式、展開區、清除、窄視窗）後修正：
- 標題改為「FoodCal AI · 餐點熱量估算」；模式、按鈕與提示全部改成中文，測試用食譜候選移到「進階選項」。
- 結果改為左圖右表（窄螢幕自動上下排列，606 px 寬無水平捲動；Chrome 視窗無法縮到 390 px，手機仍需實機確認）。
- 重疊的辨識框標籤自動往下錯開（`label_layout`），標籤改為「#編號 類別 分數」；合成畫布說明字移到左下角。
- 上傳模式先檢查本機有沒有權重與類別檔，沒有時直接說明並停用「開始辨識」；上傳預覽固定 360 px 寬，手機直拍照片方向正確。
- 「各項目的份量狀態」改為展開區；`.streamlit/config.toml` 設 `toolbarMode = "viewer"` 隱藏 Deploy 等開發者選單。
- 瀏覽器 console 無錯誤。這台電腦沒有 YOLO 權重（GitHub 所有分支與歷史也沒有），真實辨識無法在本機實測。

## 粗估份量（2026-11-20）

上傳模式的結果頁改成：辨識結果 → 份量與熱量（粗估）→ 嚴格模式結果（展開區）→ 開發者細節。
- **框出學生證**：用拖曳框選（Altair 圖表的區間選取），不需要另外安裝套件。照片撐滿圖表的資料座標範圍，框選座標直接換算回原圖像素。
- **手動加一道菜**：可以補上模型沒辨識到的菜，以及白飯、麵這類單一食材。
- **自己填重量**：每道菜都可以填克數，會取代粗估值。
- **實測**：用 Chrome 跑過一次白飯照片。學生證框和卡片實際位置相差 < 2%。體積約 235 mL；填 168 g 正確顯示；暫時放一份測試用的密度與熱量表，合計正常顯示。console 無錯誤。
- 方法與限制見 `rough_volume_estimation_v0.md`。


## 介面改版（2026-11-20，依期末提案簡報）

照提案簡報第 6–9、11 頁的畫面設計重做（`src/ui/theme.py`、`src/ui/app.py`、`.streamlit/config.toml` 的 `[theme]`）：

- **配色**：米白底、白色圓角卡片、深綠主按鈕、淺綠營養面板。
- **最上方**：校名與團隊列、標題，以及標語「Understand the meal, not just the label」。
- **流程卡**：01 INPUT → 05 OUTPUT 五張步驟卡，完成的步驟變綠。下方是 KEY RULE 黑色橫幅，寫核心原則。
- **三欄**（手機寬度時會上下排列）：
  1. **Your plate · 你的餐盤**：
     - 照片上的框用粉彩色，標籤寫「01 紫米飯」這種編號加中文菜名（用系統的中文字型），可以切換「顯示框」。
     - 上傳照片；有 `data/samples/` 時顯示「或試試範例」。這個資料夾被 Git 忽略，只放本機照片。
     - 同意勾選、「開始辨識 →」主按鈕、學生證與模型的狀態，以及「清除」。
  2. **Food recognition · 食物辨識**：
     - 菜名標題和 Gemini 的備註。
     - 每道菜有編號圓圈、烹調方式、組成、把握程度、粗估重量和看得到的熱量，看不到的油糖用橘色另寫一行。
     - 右邊是克數框（提示文字是粗估值），以及「認錯了」勾選。
  3. **Nutrition · 營養估計**：
     - 大字熱量和粗估範圍。
     - 蛋白質、醣類、脂肪的色條和克數，數值用衛福部資料庫的營養素算，油算進脂肪、糖算進醣類。
     - 總重量；「看得到的食材 ＋ 依食譜估計的油糖」說明框；每道菜「照片*／食譜*／合計」對照表。
- **下方**：
  - 「修正工具」：重框學生證、手動加一道菜。框選工具需要比較寬的空間，所以放在全寬區域。
  - 「開發者細節」：Gemini 的原始回答。
- **合成展示**：移到第二個模式，內容不變。
- **實測**：Chrome 用範例 1（校園自助餐）跑一次。四道菜、675 kcal（313–1445），蛋白質 49 g、醣類 50 g、脂肪 30 g，畫面和簡報風格一致。

## 手機版面（2026-11-23）

用 headless Chrome 模擬 390 px 寬的手機（`mobile: true`）實際操作範例 1 後修正（`src/ui/theme.py` 的 `@media (max-width: 640px)`、`src/ui/app.py`）：

- **答案放最上面**：辨識完成後，頁首下方多一張「這餐估計」卡（熱量、粗估範圍、蛋白質、醣類、脂肪、鈉），只在手機顯示（`theme.mobile_summary`，容器 `fc_mobile_summary`；寬螢幕用 CSS 隱藏，完整營養區本來就在畫面上）。原本要往下捲約三個螢幕才看得到熱量。
- **頁首變精簡**：五張流程卡改成一排小格（只留編號和 INPUT…OUTPUT），KEY RULE 改成上下排、字變小，隱藏右上角重複的 FoodCal AI 標籤。首頁總高度從約 2470 px 降到約 1400 px（CSS px）。
- **範例照片並排**：手機上 Streamlit 會把欄位上下疊，範例照片原本各佔滿整個寬度；改成兩張並排的小縮圖（容器 `fc_samples`）。
- **間距**：卡片之間的空白縮小；每道菜的說明和克數框之間留空。
- **iPhone 不再自動放大**：輸入框字體設成 16 px（iOS Safari 遇到小於 16 px 的輸入框會在點選時放大畫面）。
- 上傳欄位標題加上「手機可以直接拍照」（手機瀏覽器選檔時可以直接開相機）。
- 照片上的編號標籤不再互相蓋住：兩個框靠很近時，後面的標籤往下移、不超出照片（`adapter.tag_layout`）。桌面版也一樣受益。
- 同時修正：營養素清單的標題原本和辨識卡的副標題共用 `.fc-sub` class，讓 Gemini 的備註變成小字粗體；改用 `.fc-nut-head`。
- 限制：只用模擬器看過，沒有在真的 iPhone／Android 上測；「修正工具」裡用拖曳框選學生證的圖表在觸控螢幕上是否好用，還沒驗證。

## 大張手機照片（2026-11-23）

使用者用 iPhone 18 Pro 拍的照片（8064 × 6048，約 4880 萬畫素）不能用：`decode_upload` 原本超過 2000 萬畫素就拒絕，
預覽顯示「圖片無法讀取」，按「開始辨識」時沒有接住錯誤，整頁跳出 traceback。修正：

- **自動縮小，不再拒絕**：超過 2000 萬畫素的照片縮成長邊 4032 px（等於 1200 萬畫素手機照片的大小）。JPEG 用 Pillow 的
  `draft` 直接以 1/2 解碼，這張照片 0.09 秒；縮小後學生證照樣找得到（配對 0.93）。送給 Gemini 的本來就只有長邊 1568 px，
  份量用的是學生證和菜在同一張圖的像素比例，縮小不影響。超過 1 億像素才拒絕（避免解壓縮炸彈）。
- **上傳上限 10 MB → 20 MB**（`.streamlit/config.toml` 與 `MAX_UPLOAD_BYTES`）：4800 萬畫素的 JPEG 常常超過 10 MB。
- **錯誤講清楚**：太大、格式不對（HEIC 要先轉 JPG）、檔案壞掉各有中文說明（`PHOTO_ERRORS`）；「開始辨識」遇到讀不了的照片會顯示原因，不會當掉。
- 測試：`tests/test_demo_ui.py` 新增縮小（含 EXIF 方向）、超過上限拒絕、每個錯誤代碼都有說明。

## 上傳步驟與兩欄版面（2026-11-24）

使用者回饋：上傳照片的地方不直覺，和範例照片擠在同一張卡片裡；三欄排版不夠好看。改成依進度分三個畫面（`src/ui/app.py`、`src/ui/theme.py`）：

- **還沒選照片**：頁面中間一張「你的餐盤」上傳卡（容器 `fc_upload`，最寬 780 px、置中）。Streamlit 的上傳框放大成整塊可點的拖放區，文字改成中文：桌面是「把餐點照片拖曳到這裡」＋「選擇照片」，手機是「拍一張或選一張餐點照片」＋「拍照或選照片」（Streamlit 不能改按鈕文字，所以用 CSS 換）。下面三個拍照提醒：學生證放旁邊、從正上方拍、照片不會保存。範例照片是另一張卡（`fc_samples`），和上傳卡之間用「或」隔開；縮圖同高，下面寫菜名（`SAMPLE_LABELS`）和「用這張照片」。
- **選好照片**：兩欄。左邊是照片預覽（標示「已選照片」或「範例照片」）；右邊是「開始辨識」卡（`fc_start`），放同意勾選、隱私說明、「開始辨識 →」和「換一張照片」。上傳框這時已經不畫出來，而沒畫出來的元件會失去值，所以照片的 bytes 另存在 session state 的 `photo`；預覽只解碼一次（`preview`），開始辨識時直接沿用。
- **辨識完成**：一樣兩欄（左 1.45 : 右 1）。左邊是有編號框的照片，下方是學生證／模型說明、「顯示辨識框」開關和「換一張照片」，再下面是菜色清單；右邊是營養估計。照片上的編號和清單的編號在同一欄，方便對照；菜色說明變寬，不會擠成好幾行。照片最高 62vh（手機 48vh），直拍的照片不會把清單推得很下面。
- 同意勾選在辨識後就不畫出來了，用 `st.session_state['gemini_consent'] = st.session_state['gemini_consent']` 把答案留住，側面照照樣可以送出。
- **測試**：`tests/test_demo_ui.py` 改成沒有照片時不會出現「開始辨識」按鈕；新增兩項：「選好照片要先同意才能開始，換一張照片會回到上傳」，以及「辨識後菜色在照片下、總熱量在旁邊，同意不會遺失」。比對畫面元素時要比對 `class="..."`，因為頁面的 CSS 本身也是一段 markdown，只比對 class 名稱一定會成立。
- **實測**：在 Chrome 看過桌面（2400 px 寬）和 390 px iframe 的三個畫面。在 8501 真的上傳一張照片，按 R 重新執行頁面後照片還在；按「換一張照片」會回到空白的上傳框。辨識結果的畫面是用本機預覽腳本塞入假的辨識結果來檢查版面，沒有把照片送給 Gemini。
