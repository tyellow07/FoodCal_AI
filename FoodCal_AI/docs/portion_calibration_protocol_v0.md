# 份量校準協定 v0：學生證比例尺法（reference_card_area_ratio）

狀態：2026-11-19 程式與合成測試完成；**沒有任何真實秤重資料**，所以沒有校準紀錄，照片重量仍維持 unavailable。
程式：`src/portion/reference_card.py`；工具：`tools/build_portion_calibration.py`；測試：`tests/test_portion_reference_card.py`。

核心原則不變：**Recipe candidates may explain hidden calories, but are not visual proof.** 食譜份量、菜單標準份量、
菜名與偵測 confidence 都不是這個方法的輸入。

## 1. 方法

1. 拍照時，在餐盤旁平放一張 ISO/IEC 7810 ID-1 卡片（學生證、悠遊卡：85.60 × 53.98 mm，面積 46.20688 cm²），
   卡片邊緣大致和照片邊緣平行。
2. 食材的可見框面積（像素）× 卡片實際面積 ÷ 卡片框面積（像素）＝ 可見面積 cm²。
3. 從秤重樣本得到每種食材的「克／cm²」比值。新照片的區間＝可見面積 × 比值的下、上界。
4. 上下界用 split-conformal 順位統計量（第 ⌊(n+1)α/2⌋ 小與第 ⌈(n+1)(1−α/2)⌉ 小，α＝1−nominal），
   比值向外取到 0.000001，克數向外取到 0.1 g，所以顯示的區間不會比計算結果窄。
5. 校準紀錄的 coverage 與 MAE 採**留一來源組交叉驗證**：評估某一組（餐廳／日期）的樣本時，只用其他組的資料擬合。
   同一組外樣本不足以給出上下界的樣本列在 diagnostics 的 `samples_not_evaluable_out_of_group`，不計入 sample_count。

已知限制：用的是框而不是食材輪廓；沒有量高度，所以碗裝、堆高的食物誤差較大；照片要俯拍，卡片要和食物在同一平面。
這些誤差會反映在校準的 coverage 和 MAE，不會被隱藏。

## 2. 收集秤重資料

| 項目 | 要求 |
| --- | --- |
| 來源組 | 每一組＝一間餐廳的一天（例如 `2026-11-20-水木`）；至少 2 組（契約下限），建議 ≥ 3 組 |
| 樣本數 | 每種食材 ≥ 30 筆可在組外評估的樣本（契約下限）；目前標註規範只有 `visible_fried_egg`、`cooked_rice_region`、`cooked_noodle_region` |
| 拍照 | 手機俯拍、整個餐盤入鏡、卡片平放在餐盤旁 |
| 框 | 食材框依 `annotation_spec.json`（緊貼可見範圍）；卡片框在標註 GUI 用「學生證／悠遊卡框模式」畫（`reference_card`，2026-11-19） |
| 秤重 | 只秤這個框內的食材；秤的解析度 ≤ 1 g；扣容器重量 |
| 隔離 | 校準資料不能同時當評估測試集（見 `meal_evaluation_protocol_v0.md`） |

CSV 欄位見 `docs/examples/portion_weighing_template.csv`（只有表頭，沒有資料）。秤重 CSV 與照片是私人資料，
放在 Git 忽略的 `data/` 或 `work/`，不要加入版本控制。

## 3. 執行

```bash
.venv/bin/python -B -m tools.build_portion_calibration \
  --samples work/weighing/rice_v0.csv --out work/calibration/rice-v0 \
  --calibration-id rice-v0 --dataset-reference private://weighing/rice_v0 --producer <執行者>
```

輸出 `calibration_record.json`（status=draft）、`reference_card_model.json`、`diagnostics.json`，只能寫到 `work/` 底下、
不覆寫既有目錄。秤重 CSV 可由 `tools.meal_workspace export-calibration` 從已接受的標註與秤重紀錄產生，
完整步驟見 `campus_meal_workflow_v0.md`。

核可：`python -m tools.build_portion_calibration approve --calibration <目錄> --approver <核可者> --date <日期>`
另存 `approved_record.json`（draft 保留），拒絕自己核可，並印出仍未通過的契約門檻。

## 4. 從紀錄到熱量

1. 另一位成員（不是執行校準的人）檢查 diagnostics 後執行 `approve`。
   核可改變紀錄 hash，所以**估計要在核可之後產生**；引用 draft hash 的估計會得到 `calibration_hash_mismatch`。
2. `estimate_with_reference_card(evidence_item, card_px, model=..., record=..., estimate_id=..., producer_ref=...)`
   產生 PortionEstimate（review=not_requested）。evidence_item 來自 `tools.ingredient_pilot.visual_evidence_from_annotation`。
3. 第三人複核份量估計（review=accepted，不能是 producer）。
4. `assess_portions`／`estimate_photo_energy` 重新檢查一切；還需要照片審查產生的 PhotoNutritionBasis 與食用聲明。

`calibration_blockers` 的門檻（≥ 30 樣本、≥ 2 來源組、實測涵蓋率 ≥ nominal、已核可）無法由這個方法或核可者豁免。
合成資料端到端可算出熱量（`test_synthetic_end_to_end_reaches_computed_energy`），但那些數字不是食物量測。
