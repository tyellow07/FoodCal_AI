# FoodCal AI — 開發交接版

更新：2026-11-08；2026-11-19 增補（見「2026-11-19 增補」）。此目錄整合原始專案經審查的最新工作樹（含未 Commit 的 AI 輔助標註），供既有團隊 Repository 使用。先讀本頁，再讀 `AGENTS.md`、`HANDOFF.md` 與 `INTEGRATION_REPORT.md`。Repository 根目錄 README 與團隊歷史保持原樣；三份校園菜單在根目錄的 `菜單/`（2026-11-24 整理，原本放在根目錄）。

核心原則：**Recipe candidates may explain hidden calories, but are not visual proof.** 食譜候選只能提供隱藏食材／熱量的先驗；料理分類不能直接展開為照片食材，食譜份量不能當成照片重量。

## 現有能力

- Photo Recognition：Detection API、受控 YOLO 診斷；無合法本機權重時安全阻擋。
- Visual Ingredient Evidence：視覺證據 Adapter、逐樣本審查契約、合成 fixture；真實人工審查資料不隨庫分享。
- Annotation：Tk 人工畫框、Bounding Box 欄位編輯、AI proposal 匯入、明確新增／替換／拒絕、來源事件紀錄、append-only revision 與獨立複核；保存使用跨程序鎖與原子寫入（限同一台電腦的本機檔案系統）。匯入功能本身不呼叫 AI 模型。
- Recipe Parsing：Schema、Validator、Mock 與選用 Ollama Adapter；Nutrition Mapping、來源查核與人工審查原型。
- Streamlit Demo／CLI：合成資料離線展示；不是完整營養產品。
- Portion／Energy Contract v0：照片可見質量區間、秤重／校準方法、校準門檻、不確定性傳遞與 photo_calculation 營養串接閘門（`docs/photo_portion_estimation_contract_v0.md`）；沒有估算模型或校準資料，結果維持 unavailable。CLI、YOLO 診斷與 Streamlit 以 `photo_energy_estimate` 逐項顯示原因。
- Photo Item Source Review v0（2026-11-13）：照片食材對 TFDA 條目的受控人工審查，全程不經食譜，可產生 photo_calculation 營養基準（`docs/photo_item_source_review_design_v0.md` 第 12 節）；決策暫定、待團隊確認，只有合成測試資料，沒有真實審查或 gate 紀錄。
- 2026-11-19 增補：
  - 份量估算方法 `reference_card_area_ratio` v0（學生證比例尺＋秤重校準，`docs/portion_calibration_protocol_v0.md`）與校準工具 `tools/build_portion_calibration.py`；沒有真實秤重資料，所以沒有校準紀錄。
  - 2026-11-20：網頁介面依期末提案簡報改版（流程卡、KEY RULE、你的餐盤／食物辨識／營養估計三欄、營養素色條；`docs/demo_ui_v0.md` 最後一節）；本機範例照片放 `data/samples/`。
  - 2026-11-20：熱量改用營養模型（`docs/nutrition_model_v0.md`）：Gemini 回報組成食材與烹調方式，食材熱量取自衛福部食品營養成分資料庫，看不到的油、糖依烹調方式另外估計、分開顯示。
  - 2026-11-23：營養區加上其他營養素（飽和脂肪、糖、膳食纖維、鈉、膽固醇、鉀、鈣、鐵；`docs/nutrition_model_v0.md` 第 6 節），數值取自同一筆 TFDA 資料；調味的鈉、用油的飽和脂肪、加的糖依烹調方式估計，橘字分開顯示；沒有資料的標「≥」。
  - 2026-11-23：手機版面（`docs/demo_ui_v0.md` 最後一節）：辨識後最上方先顯示「這餐估計」卡，流程卡與 KEY RULE 精簡、範例照片並排、輸入框 16 px 避免 iPhone 放大；照片上的編號標籤不再互相蓋住。
  - 2026-11-23：多角度份量估計（`docs/multi_view_volume_v0.md`）：選填的側面照（桌面上方 15–35 度）量每道菜的高度、碗深和裝多滿，取代單張照片假設的厚度；學生證算出相機角度與比例尺，含透視。模擬場景（`python -B -m tools.benchmark_multi_view`）中位誤差：白飯堆 11%、片狀 19%、碗裝 8%（單張約 32–41% MAPE）；還沒用秤重的真實餐點驗證。
  - 2026-11-24：網頁版面重整（`docs/demo_ui_v0.md` 最後一節）：還沒選照片時只有置中的大上傳區和拍照提醒，範例照片另外一張卡、用「或」隔開；選好照片後改成兩欄（左邊照片和菜色清單，右邊「開始辨識」，辨識後換成營養估計），取代原本的三欄。
  - 2026-11-24：拿掉要點開的區塊（`docs/demo_ui_v0.md` 最後一節）：側面照在「開始辨識」卡和照片下方直接上傳，和俯視照一起分析；重框學生證、加一道菜改成照片下方的按鈕，照片本身變成框選圖；手動加的菜可以單獨移除；Gemini 原始回答只在網址加 `?dev=1` 時顯示。
  - 2026-11-24：網頁多語言（`src/ui/i18n.py`、`src/ui/messages.py`）：右上角選繁中、English、日本語、한국어、Bahasa Indonesia、Tiếng Việt；網址 `?lang=ja` 或瀏覽器語言決定第一次的語言，切換時照片、克數和同意都保留。Gemini 回報每種語言的菜名，備註用頁面語言；開發者資訊與少見的診斷原因只有中英文。
  - 2026-11-20：網頁辨識改用 Google Gemini 免費 API（`docs/gemini_vision_v0.md`）；YOLO 與 Claude（`docs/claude_vision_v0.md`）先放棄，程式保留。
  - 2026-11-20：網頁改用粗估份量（`docs/rough_volume_estimation_v0.md`）：自動找學生證背面的悠遊卡 logo（設定：`python -m tools.setup_card_reference`），找不到再手動框，用學生證當比例尺，依形狀模板估體積，再乘上密度表的密度；可以手動加菜、自己填重量；密度與熱量表放在 `data/food_reference/food_reference.csv`（格式見 `docs/examples/food_reference_template.csv`）。
  - 2026-11-20：「體積 × 密度」方法 `reference_card_volume_density` v0（同一份文件第 5 節）：密度表填網路公開資料並留來源，有效高度由秤重照片學出；沒秤重的同形狀食物只給未驗證的體積與重量，不進熱量。
  - 人工標註接到視覺證據：`tools.ingredient_pilot.visual_evidence_from_annotation` 保留 annotation_id、image_sha256、標註者與複核者，可進入份量契約與照片審查。
  - 整餐熱量評估程式 `tools/evaluate_meals.py`（`docs/meal_evaluation_protocol_v0.md`）：MAPE、區間涵蓋率、寬度、B0 基準線、作答率、拒答正確率與評分表點數。
  - Streamlit 主畫面改為中文摘要（原因代碼翻成中文、下一步建議、免責聲明），技術細節收進「開發者細節」；手機直拍照片依 EXIF 方向轉正後才辨識。頁首列出模型認得的食物；CLI 加 `--summary` 中文摘要。
  - 評分工具：各階段指標與紅線稽核（`tools/evaluate_stages.py`）、易用性測試流程與計分（`docs/usability_test_v0.md`）、標準函式庫涵蓋率（`tools/measure_coverage.py`）；完整評分表與自評見 `docs/project_rubric_v0.md`。
  - 照片審查的登記與授權層 `PhotoContextProvider`（暫定，待團隊確認）。
  - 合成資料可端到端算出熱量（標註 → 校準估計 → 複核 → 營養基準 → 能量）；真實資料仍缺，正式輸出照舊 unavailable。
  - 校園餐實測流程（`docs/campus_meal_workflow_v0.md`）：照片建工作區、GUI 畫學生證框、秤重匯出校準、核可、估計／複核／熱量批次執行、輸出評分用 predictions.csv。

Portion Estimation 與 Nutrition Estimation 尚未完成照片實證整合。正式輸出維持 `photo_portion_grams=null`、`calorie_estimate=unavailable`、營養資格 false；沒有可信照片重量或正式食譜來源時不得補造結果。

## 換電腦啟動

使用 Python 3.12，在 Clone 的 `FoodCal_AI` 目錄執行。macOS／Linux：

```bash
cd FoodCal_AI
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-ui.txt
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -B -m src.pipeline.diagnostic_demo
.venv/bin/python -m streamlit run src/ui/app.py
```

Windows（PowerShell）：

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

上傳照片的辨識用 Google Gemini 免費 API（`docs/gemini_vision_v0.md`）：`.venv/bin/python -m pip install -r requirements-vision.txt`，
在 `.streamlit/secrets.toml` 寫 `GEMINI_API_KEY = "..."`（不會被加進 Git）。使用者勾選同意後照片才會送出，學生證會先塗掉。
免費版條款允許 Google 用送出的內容改進產品。

UI 預設使用合成展示，服務位址以啟動輸出為準。Tk GUI 需要 Python 安裝的 Tk/Tcl 與 Pillow；用法見 `docs/ingredient_pilot_ai_import_v0.md`。沒有核准的本機圖片、manifest 與 workspace 時，先執行合成測試，不要建立假的私人資料以通過驗證。

依賴版本沿用既有分享版；本次未下載依賴、未驗證全新環境安裝或跨平台 lock。`requirements-model.txt` 為選用模型環境提示，使用前需確認第三方套件、權重與資料條款。

## 驗證與分享界線

本次離線測試共 398 項：386 passed、0 failures、0 errors、12 skipped。4 項缺 Streamlit；8 項無可用 Tcl/Tk 初始化環境。已執行的測試包含 19 項新增 AI 草稿邏輯測試；GUI 互動測試未算通過。完整範圍及未攜入的私有資料測試見 `HANDOFF.md`。2026-11-11 在 macOS／Python 3.12.0 修正三個測試檔的可攜性問題後，398 項全數通過、0 skipped（含 Streamlit 4 項與 Tk 8 項）；加入 Portion Contract v0 後為 422 項、再加入標註跨程序鎖後為 431 項、接入份量／熱量診斷輸出後為 435 項、2026-11-13 加入照片食材來源審查 v0 後為 465 項、2026-11-19 增補後為 491 項，皆全數通過（0 skipped），詳見 `HANDOFF.md`。

不包含 data/runs/work、圖片、模型權重、人工 revisions、私人 manifest、TFDA 營養原件、模型原始輸出、API Keys、虛擬環境或 Codex sessions。資料與權重須另行取得合法使用權；此目錄不是完整私人工作資料備份。

`.gitignore` 採精確白名單；新增檔須審查後加入，不使用 `git add -f`。既有 `SHARING_REPORT.md` 是 2026-10-19 歷史紀錄，當時測試數與分支流程不代表本輪。沒有新增開源 License，也未授予公開散布或第三方資料／模型權利。

本次未 Commit、Push、Merge 或訓練。最初交接準備於 `feature/foodcal-ai-handoff`，之後工作在 `tyellow-*` 分支（2026-11-19 增補在 `tyellow-mvp-usability`）；遠端可見性與寫入權限尚未重新確認。
