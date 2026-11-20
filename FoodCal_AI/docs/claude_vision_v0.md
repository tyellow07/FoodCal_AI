# Claude 視覺辨識 v0（2026-11-20）

狀態：**2026-11-20 晚上團隊改用 Gemini 免費 API（`gemini_vision_v0.md`）**，Claude 和 YOLO 都先放棄。
程式和測試保留，網頁上不再顯示。以下是當時 Claude 方案的紀錄。
程式：`src/detection/claude_vision.py`；網頁：`src/ui/app.py`（上傳模式的「辨識方式」）；測試：`tests/test_claude_vision.py`（用假的 client，不連網）。

核心原則不變：**Recipe candidates may explain hidden calories, but are not visual proof.** Claude 只回報照片裡看得到的東西。

## 1. 為什麼改用 Claude

| | YOLO（taiwanese_food_baseline_10ep） | Claude Opus 5.5 |
| --- | --- | --- |
| 驗證成績 | 自己的 Roboflow 驗證集：mAP50 0.86、precision 0.89、recall 0.84 | 還沒量（見第 5 節） |
| 能認的食物 | 15 類，白飯、燙青菜等常見食材不在內 | 不限類別，也可以表示「不確定」 |
| 實測失敗案例 | 白飯照片被認成土魠魚羹（分數 0.65） | — |
| 框 | 準 | 大概的位置 |
| 執行 | 本機、免費、可離線 | 要網路與 API 帳戶，照片會傳給 Anthropic |

要把 YOLO 改好，需要大量標註過的校園照片再重新訓練，以目前的時間做不到。

## 2. 請求內容

- 一張照片送一次：先縮到長邊 1568 px，再重新存成 JPEG（只有像素，沒有 EXIF 和 GPS）。
- 回答用 JSON schema 限制格式（`output_config.format`）。每道菜回傳：
  - 繁體中文名稱、英文名稱
  - 已知的食物代碼，或 `other`
  - 盤裝或碗裝
  - 0–1000 格線上的框
  - 看得到的食材
  - 把握程度
- 指示裡明確要求：不估重量、份量、熱量，也不補照片裡看不到、只是食譜常有的食材。
- effort 設 `medium`。
- 開啟伺服器端的拒答備援（`fallbacks: "default"`）。拒答、回答被截斷、格式錯誤時，回傳可讀的錯誤代碼。

## 3. 網頁

1. 上傳模式先選「辨識方式」，預設是 Claude。
2. 使用者要勾「我同意把這張照片傳給 Anthropic 辨識」，「開始辨識」按鈕才能按。
3. 結果顯示藍框（C1、C2…）、菜名表、Claude 的備註，以及模型、耗時和 token 數。
4. 接著是同一個粗估份量區（`rough_volume_estimation_v0.md`）：自動找學生證、排除認錯的菜、手動加菜、自己填重量。
5. 不在原本 18 類裡的菜（例如燙青菜），依 Claude 回報的盤裝或碗裝套用通用模板：盤裝平均厚度 1–3 cm，碗裝用碗模板。
   這類菜的密度與熱量，用英文名稱轉成的代碼（例如 `blanched_greens`）在密度表裡查。

## 4. 帳戶、費用與隱私

- **登入**：二擇一，金鑰都不會進 Git。
  - `ant auth login`（安裝：`brew install anthropics/tap/ant`）。
  - 在 `FoodCal_AI/.streamlit/secrets.toml` 寫 `ANTHROPIC_API_KEY = "..."`。這個檔案不在白名單內，所以不會被加進 Git。
- **費用**：依 Claude Console 的 API 額度計費，和 claude.ai 的訂閱是分開的。每次辨識在畫面上顯示 token 數；第一次實測後補上每張的實際花費。
- **隱私**：
  - YOLO 模式完全在本機執行。
  - Claude 模式只有在使用者勾選同意並按下按鈕後才送出照片；本機不存檔。
  - 評分表第 7 項第 4 條已補上這條規則。
- **課程規定**：期末專案能不能使用外部 AI 服務，請組員向助教確認。

## 5. 待做：和 YOLO 比較

- **資料**：用同一批校園照片（不是訓練資料），由人工標出正確的菜名。
- **比較兩件事**：
  1. 菜名正確率：每道菜有沒有認對。
  2. 漏掉和多認的數量。
- **框的準確度**：Claude 的框只是大概，所以分開報告，不直接套用 IoU ≥ 0.5 的 F1。
- **用途**：結果放進報告，失敗案例至少列 3 個。
