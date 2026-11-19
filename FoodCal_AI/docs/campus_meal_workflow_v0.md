# 校園餐實測流程 v0：從拍照到評分

狀態：2026-11-19 程式與合成端到端測試完成（`tests/test_campus_meal_workflow.py`）。**還沒有任何真實照片、秤重或審查紀錄**；
照著本文件收資料之後，才會第一次產生真實的熱量區間與分數。所有照片、標註、秤重與輸出都放在 Git 忽略的 `work/`，不要 commit。

核心原則：**Recipe candidates may explain hidden calories, but are not visual proof.** 這條流程完全不讀食譜。

## 0. 角色（同一筆紀錄的各角色必須是不同人）

| 角色 | 做什麼 | 程式強制的獨立性 |
| --- | --- | --- |
| 拍照者 | 拍照、聲明「整份吃完」 | — |
| 標註者 | 在 GUI 畫食材框與學生證框 | 不能自己複核 |
| 框複核者 | GUI `--review` 接受或退回 | ≠ 標註者 |
| 秤重者 | 秤每個框內的食材 | — |
| 校準者／核可者 | 執行校準／核可紀錄 | 核可者 ≠ 校準者 |
| 份量複核者 | 接受或退回每筆份量估計 | ≠ 估計產生者、≠ 秤重者 |
| 營養審查者 | 照片食材對 TFDA 條目審查，產生 PhotoNutritionBasis | ≠ 標註者、≠ 份量 producer（照片審查 v0） |

## 1. 兩個工作區：校準用與評估用，絕不混用

```bash
# meals.csv 欄位：meal_id,source_group,photo_file
# meal_id 只能用英數字與底線（例：c_1120_shuimu_01）；source_group＝餐廳＋日期（例：1120_shuimu）
.venv/bin/python -B -m tools.meal_workspace build --photos <照片資料夾> --meals <calibration_meals.csv> \
  --workspace work/meals/calibration_v0 --purpose calibration
.venv/bin/python -B -m tools.meal_workspace build --photos <照片資料夾> --meals <evaluation_meals.csv> \
  --workspace work/meals/evaluation_v0 --purpose evaluation
```

- 照片：JPEG 或 PNG（iPhone 請先匯出成 JPEG）。工具會依 EXIF 方向轉正、移除 metadata，再複製到工作區。
- 評估工作區的餐點無法匯出成校準樣本（`workspace_purpose_must_be_calibration`）。
- 拍照協定、測試集規模見 `meal_evaluation_protocol_v0.md` 第 2 節；校準樣本數見 `portion_calibration_protocol_v0.md` 第 2 節。

## 2. 標註與複核

```bash
.venv/bin/python -m tools.annotate_ingredient_pilot --workspace work/meals/calibration_v0 --actor <標註者>
.venv/bin/python -m tools.annotate_ingredient_pilot --workspace work/meals/calibration_v0 --actor <複核者> --review
```

1. 依 `annotation_spec.json` 畫可見的白飯、麵、煎蛋框（三類都要逐一檢查並勾選）。
2. 勾「學生證／悠遊卡框模式」，拖曳框住整張卡片（綠色虛線；每張照片一個，再畫一次會取代）。
3. 保存後，由另一人用 `--review` 接受。只有最新一筆、已接受的 revision 會被後續步驟使用。

## 3. 秤重與校準（校準工作區）

```bash
# weights.csv 欄位：meal_id,annotation_id,mass_g,scale_resolution_g,measured_by（annotation_id 可在 GUI 框清單看到）
.venv/bin/python -B -m tools.meal_workspace export-calibration --workspace work/meals/calibration_v0 \
  --weights <weights.csv> --out work/meals/weighing_v0.csv
.venv/bin/python -B -m tools.build_portion_calibration --samples work/meals/weighing_v0.csv \
  --out work/meals/calibration/rice-v0 --calibration-id rice-v0 \
  --dataset-reference private://campus/weighing_v0 --producer <校準者>
.venv/bin/python -B -m tools.build_portion_calibration approve --calibration work/meals/calibration/rice-v0 \
  --approver <核可者> --date 2026-11-25
```

`approve` 會印出剩下的阻擋原因（例如樣本不足、實測涵蓋率低於 80%）。有阻擋時照樣可以跑後面的步驟，但熱量會維持 unavailable。

## 4. 評估工作區：估計 → 複核 → 熱量

```bash
.venv/bin/python -B -m tools.run_meal_pipeline estimate --workspace work/meals/evaluation_v0 \
  --record work/meals/calibration/rice-v0/approved_record.json \
  --model work/meals/calibration/rice-v0/reference_card_model.json --out work/meals/run_v0
# 份量複核者填 work/meals/run_v0/review_decisions_template.csv 的 status（accepted／rejected）、reviewer_ref、reviewed_at
.venv/bin/python -B -m tools.run_meal_pipeline review --estimates work/meals/run_v0/estimates.json \
  --decisions <填好的 decisions.csv> --out work/meals/run_v0/reviewed_estimates.json
# 營養審查：對每筆已接受的估計做照片食材來源審查（src/nutrition/photo_review.py），把產生的 PhotoNutritionBasis 存成 JSON 清單
.venv/bin/python -B -m tools.run_meal_pipeline energy --workspace work/meals/evaluation_v0 \
  --record work/meals/calibration/rice-v0/approved_record.json \
  --estimates work/meals/run_v0/reviewed_estimates.json --bases <bases.json> \
  --consumption-asserted-by <拍照者> --consumption-evidence private://campus/consumption_protocol_v0 \
  --out work/meals/energy_v0
```

- `energy` 同時寫出 `red_line_audit.json`（紅線稽核，應為空）。
- 一餐只要有任何一個可見食材沒有估計（例如沒畫卡片、食材不在校準範圍、複核未接受、沒有營養基準），整餐就是 unavailable，原因寫在 `results.json`。
- 照片審查會綁定估計的 hash，所以**先完成份量複核再做營養審查**；複核改變估計內容後，舊的審查不再適用。

## 5. 評分

```bash
# references.csv：meal_id,source_group,subset,reference_kcal（參考熱量＝秤重 × TFDA，見評估協定第 1 節）
.venv/bin/python -B -m tools.evaluate_meals --references <references.csv> --predictions work/meals/energy_v0/predictions.csv
```

輸出評分表第 1、3b、3c 項的分數；第 2 項與紅線稽核用 `tools.evaluate_stages`（`meal_evaluation_protocol_v0.md` 第 5 節）。

## 6. 目前限制

- 標註規範只有白飯、麵、煎蛋三類；肉類、蔬菜、醬汁需要先擴充規範並經團隊審查。
- 現有 YOLO 是台灣小吃 15 類的「整道菜」偵測，不會自動產生食材框；這條流程靠人工標註。
- 營養審查（照片審查 v0）的第 10 節決策仍待團隊確認，Local Context Provider 的照片 profile 尚未實作。
