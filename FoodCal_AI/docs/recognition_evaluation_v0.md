# 菜色辨識評估 v0：Gemini 認對幾道菜

狀態：2026-11-26 程式與合成測試完成（`tests/test_recognition_eval.py` 17 項）。**還沒有用真的照片跑過**：要先有人標註正確菜名，
並由使用者決定把照片送給 Gemini（免費版條款，見 `gemini_vision_v0.md`）。

目的：完成 `gemini_vision_v0.md` 第 5 節的待做項目——用同一批校園照片量 Gemini 的菜名正確率、漏掉和多認的數量，
並列出失敗案例（評分表第 2a 項與第 8b 項「≥ 3 個失敗案例」）。

## 1. 為什麼不能直接用第 2a 項的程式

`tools.evaluate_stages` 的 2a 要求「同類別」：YOLO 只會輸出 15 個固定類別，類別名稱可以逐字比對。網頁現在用 Gemini，
菜名是自由文字（「炒高麗菜」「高麗菜」「炒青菜」可能都對），逐字比對會把對的算成錯的。所以這裡改成：

1. 名稱正規化後一樣（全形轉半形、大小寫、空白、標點都忽略），或符合標註者寫的別名 → 程式直接算對。
2. 其餘可能配對的組合列成「待判斷」清單，由人寫下 `same`（同一道菜）或 `different`，並簽名。
3. **只要還有一組沒判斷，就不給分數**。程式不猜名稱是否相同。

## 2. 流程

```bash
# 1. 產生空白標註表（每張照片一列，附轉正後的寬高；不含任何模型輸出，避免標註者被模型答案影響）
.venv/bin/python -B -m tools.evaluate_recognition template --photos <照片資料夾> --out work/recognition/labels.csv

# 2. 標註者填 labels.csv（每道菜一列，見第 3 節），存成 UTF-8 CSV

# 3. 把照片送給 Gemini（使用者決定後才加 --agree；照片轉正、縮小、學生證塗灰，和網頁一樣）
.venv/bin/python -m tools.run_gemini_batch --photos <照片資料夾> --out work/recognition/predictions.json --agree

# 4. 計分；有待判斷的組合時寫出清單、結束碼 3
.venv/bin/python -B -m tools.evaluate_recognition score --labels work/recognition/labels.csv \
  --predictions work/recognition/predictions.json --write-pending work/recognition/pending.csv

# 5. 判斷者在 pending.csv 的 verdict 填 same 或 different、judge 填名字，再計分
.venv/bin/python -B -m tools.evaluate_recognition score --labels work/recognition/labels.csv \
  --predictions work/recognition/predictions.json --judgements work/recognition/pending.csv
```

- 所有輸出都寫在 Git 忽略的 `work/`，不覆蓋已存在的檔案。
- `run_gemini_batch` 不存照片，只存檔案 SHA-256、框座標所用的寬高和 Gemini 的回答。每張照片處理完就存檔，
  中斷後再跑會接著做；已回答的照片不會重送，除非檔案改了或加 `--redo`。失敗（配額、網路）或略過的照片下次會再試。
- 找不到學生證的照片預設**不送**（避免卡號外流）；確定照片裡沒有學生證才加 `--send-unmasked`。
- 請求之間預設停 5 秒（Flash Lite 免費版每分鐘 15 次）。金鑰讀 `GEMINI_API_KEY` 或 `.streamlit/secrets.toml`。

## 3. 標註表欄位（labels.csv）

| 欄位 | 必填 | 說明 |
| --- | --- | --- |
| `image_id` | ✓ | 照片檔名去掉副檔名（`template` 已填好） |
| `dish_id` | ✓ | 這張照片裡的菜色編號，1、2、3… |
| `name` | ✓ | 正確菜名，用攤位會寫的名字（例：滷雞腿、炒高麗菜） |
| `aliases` | | 也算對的其他叫法，用 `;` 分隔（例：高麗菜;炒青菜）。要在看 Gemini 結果**之前**寫好 |
| `ingredients` | | 看得到的食材，用食材表的 key、`;` 分隔（例：`leafy_vegetable;carrot`），打錯會報錯 |
| `x_min`…`y_max` | | 菜色的框（轉正後的原圖像素）。四格要嘛全填、要嘛全空 |
| `image_width`、`image_height` | 有框時 | 標框用的圖片寬高（`template` 已填好） |

- **有框**：Gemini 的框要和標註框重疊 IoU ≥ 0.5 才算找到（和 2a 一樣）。Gemini 的框會依寬高比例換算到標註圖；
  寬高比差超過 1% 代表不是同一張或方向不同，程式會報錯。
- **沒框**：只看名稱，同一張照片裡任何一道都可以配對。比較快，但算出的 F1 不能當 2a 分數（報告會註明）。
- IoU 門檻在量測前決定，量完不改（`--iou`，預設 0.5）。

## 4. 怎麼算

每張照片內一對一配對，依序三輪：

1. 名稱規則相同的組合（有框的先，IoU 高的先）。
2. 剩下的組合中，人判斷 `same` 的。
3. 剩下有框的組合（IoU 夠但名稱判斷為 `different`）→ **認錯名稱**。

剩下的標註算**漏掉**，剩下的 Gemini 項目算**多認**。沒有結果的照片（沒跑、失敗、略過）所有菜都算漏掉。

| 指標 | 算法 |
| --- | --- |
| precision / recall / F1 | 認對 ÷ Gemini 項目數；認對 ÷ 標註菜數 |
| `localisation_recall` | 有框的標註中，Gemini 框到（不管名稱對不對）的比例 |
| `name_accuracy_when_located` | 框到的菜裡名稱對的比例（「菜名正確率」） |
| `ingredients` | 認對的菜中，Gemini 的組成食材 key 和標註食材的 precision／recall，以及最常漏、最常多的食材 |
| `failure_cases` | 每一筆漏掉、多認、認錯名稱，附照片、菜名、IoU；漏掉的附上最接近的 Gemini 框 |
| `rubric_points.2a_detection` | 全部標註都有框時，用 F1 套 2a 門檻（≥0.80：5；≥0.65：3；≥0.50：1）；否則 null |

報告挑至少 3 筆 `failure_cases`，寫出原因（例：兩道菜框成一道、醬色相近認錯肉、看不到的配料）。

## 5. 限制

- 名稱判斷靠人；同一批照片最好由兩個人各判斷一次、不一致的再討論。程式只記錄判斷者名字，不檢查是否獨立。
- 食材比較只看有沒有，不看比例；比例影響熱量，要靠秤重測試集（`meal_evaluation_protocol_v0.md`）。
- 用 2a 門檻是暫定做法：「同名稱（規則或人判斷）」取代「同類別」，要團隊同意後才寫進評分。
- Gemini 每次回答可能不同；報告要寫模型版本（`predictions.json` 每張都有 `model`）和執行日期。
