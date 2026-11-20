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
另一個寫法是「體積 × 密度」，見第 5 節。
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

## 5. 體積 × 密度（reference_card_volume_density v0，2026-11-20）

程式：`src/portion/volume_density.py`；測試：`tests/test_portion_volume_density.py`。拍照與秤重要求和第 2 節相同。

```
可見面積 (cm²) ＝ 食材框像素面積 × 46.20688 ÷ 卡片框像素面積
體積 (cm³)     ＝ 可見面積 × 有效高度 (cm)
重量 (g)       ＝ 體積 × 密度 (g/cm³)
```

1. **密度表**（`docs/examples/food_density_template.csv`，只有表頭）：
   - 欄位：`food,shape,grams,volume_ml,source_title,source_url,retrieved_at,entered_by`。
   - 從網路上的公開資料填，例如「1 杯 = 186 g」就填 `grams=186`、`volume_ml=236.6`（美制 1 杯）。
     程式算出密度 grams ÷ volume_ml（g/mL＝g/cm³），每一列都要留來源網址、查詢日期和填表的人。
   - 優先用可以自由使用的來源，例如 USDA FoodData Central（美國政府公開資料）。
     次級網站的數字常互相矛盾，要回原始頁面確認。
   - `shape`：`mound`（堆成一團：白飯、麵）或 `layer`（薄片：煎蛋、蛋餅）。碗裝湯麵還沒有支援。
2. **有效高度**由秤重照片學出來：有效高度 ＝ 秤重 ÷（密度 × 可見面積）。
   - 它是整個框內的平均高度，框的空角也算進去，所以通常比用尺量最高點低。
   - 用尺量的高度不是輸入，可以記錄下來，和 diagnostics 的 `effective_height_cm_median` 對照。
   - 上下界一樣用 split-conformal，coverage 一樣用留一來源組驗證。
3. **有秤重的食物**：密度查得準不準，會被有效高度吸收，所以重量區間的 coverage 和第 1 節的面積法相同（測試有驗證）。
   密度表多給的是每一項的體積。
4. **沒秤重、但同形狀的食物**：用同形狀食物的有效高度乘上自己的密度，算出體積和重量。
   - 這個重量沒有量測過的誤差範圍，標成 `coverage_validated: false`。
   - 它不是 PortionEstimate，永遠不會進熱量總和；`estimate_with_volume_density` 會回 `ingredient_not_calibrated`。

執行：在第 3 節的指令加上 `--densities <密度表.csv>`，輸出改成 `volume_density_model.json`。
`tools.run_meal_pipeline estimate --model <volume_density_model.json>` 除了估計之外，每一餐還會多一個 `volumes` 清單。

第一次手算檢查（2026-11-20，一張白飯照片，不是校準樣本）：
- **實際**：白飯秤重 168 g，學生證放在盤子上，用尺量側邊高約 1 cm、整體約 1.5 cm。
- **照片**：手描輪廓約 111 cm²，框約 153 cm²。
- **換算**：輪廓 × 1.5 cm ≈ 167 cm³，等於有效密度約 1.0 g/cm³。網路上 1 杯白飯換算的密度只有約 0.67–0.79，
  所以直接相乘會少算 20–35%。
- **結論**：網路密度、輪廓還是框、量高度的位置都會帶來誤差，只有秤重校準能量出合起來的誤差範圍。
