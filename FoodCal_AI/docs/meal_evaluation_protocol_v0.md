# 整餐熱量評估協定 v0

狀態：2026-11-19 評估程式與合成測試完成（`src/evaluation/meal_metrics.py`、`tools/evaluate_meals.py`、
`tests/test_meal_evaluation.py`）；**還沒有真實測試集**。門檻是團隊提案，正式量測前確認，量完不改。

## 1. 參考熱量（真值）

一餐參考熱量 ＝ Σ（可見食材 i 的秤重克數 × 食材 i 的 TFDA 每 100 g 熱量 ÷ 100）。

- 重量：拍照當下可見、假設全部吃完（對應 `whole_visible_portion_asserted`）；剩食扣除尚未定義。
- 每 100 g 熱量：TFDA 中烹調狀態相符的條目（熟飯不是生米）；兩人獨立選，不一致時討論並記錄。
- 油、醬汁、糖等看不到的熱量不算進參考值，系統的主數字也不算（`scope=visible_items_only`）。
- 單位 kcal。系統輸出區間並宣告涵蓋率（預設 80%）。

## 2. 測試集

| 項目 | 要求 |
| --- | --- |
| 規模 | ≥ 30 餐；水木、風雲樓、小吃部各 ≥ 5 餐；跨 ≥ 3 天（`source_group`） |
| 拍照 | 手機俯拍、整個餐盤入鏡、旁邊平放學生證或悠遊卡 |
| 秤重 | 每個可見食材分開秤，解析度 ≤ 1 g，扣容器重量；分不開的菜整份秤並選最接近的複合條目，找不到就排除並記數 |
| 隔離 | 不能用於訓練、調參或份量校準；正式評估前凍結檔案清單（記 hash） |
| 應拒答子集 | 另收 ≥ 10 張：非食物、食物出框、模型類別外、模糊過暗、多人共食，各 ≥ 2 張 |

測試集照片與秤重紀錄是私人資料，只放在 Git 忽略的目錄。

## 3. 檔案格式與執行

```text
references.csv : meal_id,source_group,subset,reference_kcal   # subset = main | should_abstain（後者 reference_kcal 留空）
predictions.csv: meal_id,status,low_kcal,high_kcal            # status = computed | unavailable
```

```bash
.venv/bin/python -B -m tools.evaluate_meals --references <references.csv> --predictions <predictions.csv>
```

沒有列在 predictions 的餐視為 unavailable。`prediction_from_energy_estimate(meal_id, photo_energy_estimate)` 可把系統輸出轉成一列；
只有 `status=computed` 且單位為 kcal 的總和算作答，kJ 不換算。

## 4. 自動計分的項目（評分表第 1 項與第 3b、3c 項，共 35 分）

| 子項 | 算法 | 給分 |
| --- | --- | --- |
| 1a 中點誤差（10） | abs(中點 − 參考) ÷ 參考 的平均，只算有作答的餐 | ≤20%：10；≤30%：7；≤40%：4 |
| 1b 涵蓋率（8） | 參考值落在區間內的比例 | ≥80%：8；≥70%：4 |
| 1c 寬度（7） | (上界 − 下界) ÷ 參考 的中位數 | ≤60%：7；≤100%：4；≤150%：1 |
| 3b 作答率（5） | main 子集中有作答的比例 | ≥70%：5；≥50%：3；≥30%：1 |
| 3c 拒答正確率（5） | should_abstain 子集中回 unavailable 的比例（< 10 張記 0） | ≥90%：5；≥75%：3；≥50%：1 |

- 作答少於 10 餐：第 1 項記 0。
- 基準線 B0：每餐都猜 main 子集參考熱量的中位數；系統 MAPE 沒有低於 B0（同一批作答餐）時，1a 最多 4 分。
- 全部拒答時 3c 必得滿分、3b 為 0，所以拒答不會被當成準確。
- 測試集未達第 2 節規模時，報告會附註「items 1-2 not valid」。

第 2 項（各階段準確度）、紅線稽核、易用性、工程與展示需要人工或流程證據，不在這支程式內。
