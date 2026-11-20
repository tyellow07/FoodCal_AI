# Gemini 視覺辨識 v0（2026-11-20）

狀態：團隊決定網頁只用 Google Gemini 的免費 API 辨識食物。YOLO 和 Claude 的方法先放棄，程式保留在 repo 裡
（`src/detection/model.py`、`src/detection/claude_vision.py` 與它們的測試），網頁上不再顯示。
程式：`src/detection/gemini_vision.py`；網頁：`src/ui/app.py`；測試：`tests/test_gemini_vision.py`（用假的 client，不連網）。

核心原則不變：**Recipe candidates may explain hidden calories, but are not visual proof.** Gemini 只回報照片裡看得到的東西。

## 1. 流程

1. 使用者上傳照片，勾「我了解並同意把這張照片傳給 Google Gemini 辨識」，按「開始辨識」。
2. 程式先在本機自動找學生證（`card_locator.py`），找到的話，在**要送出的那份**照片上把卡片塗成灰色，卡號不會送出。
3. 照片縮到長邊 1568 px，重新存成 JPEG（只有像素，沒有 EXIF 和 GPS）後送出。
4. 回答用 JSON schema 限制格式。每道菜回傳：
   - 繁體中文名稱、英文名稱
   - 已知的食物代碼，或 `other`
   - 盤裝或碗裝
   - Gemini 慣用的 `box_2d`：[ymin, xmin, ymax, xmax]，0–1000
   - 看得到的食材、把握程度
5. 網頁顯示藍框 G1、G2…，接著是粗估份量區（`rough_volume_estimation_v0.md`）：
   - 學生證比例尺
   - 排除認錯的菜、手動加菜
   - 自己填重量

## 2. 模型與金鑰

- **模型**：依序試 `gemini-flash-latest`、`gemini-flash-lite-latest`、`gemini-2.5-flash`。
  前一個不存在或當天額度用完，就換下一個。
- **免費額度**（2026-11-20 在 AI Studio「Rate Limit」頁面看到的，每個模型分開計算）：
  - Flash 系列：每分鐘 5 次、每天 20 次。
  - Flash Lite 系列：每分鐘 15 次、每天 500 次。
  - 所以每天前約 20 張用 Flash，之後自動改用 Flash Lite。
- **連線方式**：依序試 Gemini API（AI Studio 金鑰）和 Vertex AI express mode，用的是同一把金鑰。
  第一個不接受這把金鑰時，自動換下一個。
- **金鑰位置**：寫在 `FoodCal_AI/.streamlit/secrets.toml` 的 `GEMINI_API_KEY = "..."`，或設定環境變數 `GEMINI_API_KEY`。
  這個檔案不會被加進 Git，金鑰不要貼進對話、程式碼或文件。
- **錯誤提示**：金鑰無效、超過免費額度（429）、被安全機制擋下、回答被截斷或格式錯誤，都有中文說明。

## 3. 免費版的條款（使用前要知道）

- Google 的 Gemini API 條款規定，免費版送出的內容和回答，Google 可以用來改進產品。這些內容會先去識別化，之後可能由真人審閱。
- Google 建議不要送出敏感或個人資料；台灣使用者適用這套免費條款。
- 所以網頁會：
  1. 在同意文字裡寫明以上條款。
  2. 送出前先塗掉學生證。
  3. 提醒使用者不要拍到人臉。
- 免費額度依專案計算，有每分鐘、每天的次數上限，確切數字在 Google AI Studio 查看。超過時網頁會提示稍後再試。

## 4. 待做

- 用同一批校園照片，由人工標出正確菜名，量 Gemini 的菜名正確率、漏掉和多認的數量，並列出至少 3 個失敗案例。
- 確認期末專案可以使用外部 AI 服務（問助教）。
