# Photo Portion Estimation Contract v0

狀態：2026-11-11 離線 synthetic/test_only 契約原型。定義照片份量紀錄、校準紀錄、不確定性與
營養串接閘門；**沒有**估算模型、校準資料、訓練或真實營養數值。未修改 Detection、Visual Ingredient、
Parsing、Mapping、Review 契約或既有 diagnostic 輸出；`photo_portion_grams` 仍為 null。

核心原則：**Recipe candidates may explain hidden calories, but are not visual proof.**

Python 型別：`src/portion/types.py`；結構檢查：`src/portion/contract.py`；
綁定與串接：`src/portion/estimation.py`（`assess_portions`、`estimate_photo_energy`）。

## 1. 量的定義

- 單張照片最多支持 `visible_mass_at_capture`：拍攝當下、該視覺證據所指食材的可見質量。
- Nutrition 契約的 `photo_consumed_mass` = 可見質量 ＋ 明示食用聲明。v0 只定義
  `whole_visible_portion_asserted`（聲明者、證據 locator 必填）；剩食前後對比尚未定義。
- 四種重量（recipe_declared_mass、100 g reference_measure、edible_portion、photo_consumed_mass）
  分別保留。本契約只產生第四種的可見質量部分，不從其他三者推算，也不反向填補。
- 一律以區間 `{low, high, coverage}` 表示，數值為精確十進位字串（不接受 float、指數、負號、NaN）。
  不提供單點估計；既有 scalar `photo_portion_grams` 永遠維持 null。

## 2. 證據邊界

| 視覺證據 | 能否帶照片質量 | reason |
| --- | --- | --- |
| ingredient_annotation／reviewed_prediction，observed_candidate，有 ID 與合法 image_region | 可，仍須方法、複核與校準條件 | — |
| 同上但 status=uncertain | 否 | visual_evidence_uncertain |
| dish_detection_only | 否 | ingredient_level_semantics_unverified |
| dish_classification_only | 否 | classification_is_not_ingredient_detection |
| inferred_from_recipe（recipe_assisted_hidden_ingredients） | 否；永不加入熱量總和 | recipe_prior_not_visual_evidence |

綁定以 `evidence_ref.evidence_sha256` 鎖定整筆證據（canonical JSON v0：sorted keys、緊湊分隔、UTF-8、
禁止 NaN）。證據任何欄位變動即 `evidence_hash_mismatch`；同一證據多筆估計為 `duplicate_estimates`，
不擇一。綁到食譜候選、菜餚框或不存在的證據都記為 assessment issue，不產生質量。

下列 `method.kind` 明確拒絕，未列出的其他種類一律 `method_kind_not_allowed`：

| kind | code |
| --- | --- |
| recipe_declared_quantity | recipe_quantity_is_not_photo_mass |
| standard_serving | standard_serving_is_not_photo_mass |
| dish_label_default | dish_label_is_not_photo_mass |
| detection_confidence | model_confidence_is_not_mass |
| box_count | box_count_is_not_portion_count |
| ai_draft | ai_draft_is_not_measurement |

## 3. PortionEstimate（封閉欄位，多或少欄即 fields_mismatch）

| 欄位 | 定義 |
| --- | --- |
| contract／schema_version | foodcal.photo_portion_estimate／整數 0 |
| estimate_id | 非空字串 |
| evidence_ref | evidence_type、evidence_id（annotation_id 或 review_id）、ingredient_candidate、evidence_sha256 |
| mass_kind | 固定 visible_mass_at_capture |
| mass_g | low ≤ high、high > 0；coverage 為 (0, 1] 的名目涵蓋率 |
| method | kind（direct_weighing／calibrated_visual_estimate）、method_id、method_version |
| measurement | 秤重才有：instrument_resolution_g（> 0）、evidence_ref（可分享 locator）、measured_by |
| calibration_ref | 校準估計才有：calibration_id、record_sha256（校準紀錄 canonical hash） |
| producer | direct_weighing 配 human；calibrated_visual_estimate 配 calibrated_method |
| review | not_requested／pending／accepted／rejected；有決定才填 reviewer_ref、reviewed_at |
| usage | 固定 diagnostic/test_only |

- 秤重區間寬度不得小於秤的解析度（`interval_narrower_than_resolution`）。
- 複核者必須不同於 producer 與 measurer（`review_not_independent`）；rejected 直接 unavailable。
- evidence／measurement locator 不得是機器路徑（`private_path_not_allowed`）。

## 4. CalibrationRecord 與使用門檻

| 欄位 | 定義 |
| --- | --- |
| contract／schema_version | foodcal.portion_calibration／0 |
| method | 固定 calibrated_visual_estimate，method_id／version 必須與估計完全相同 |
| status | draft／approved／revoked；approved 須有 approved 決定 |
| dataset | reference（可分享 locator）、sha256、split=held_out_by_source_group、ground_truth=direct_weighing、sample_count、source_group_count |
| coverage | nominal ∈ (0, 1)、covered_count（held-out 樣本中真值落在區間內的數量） |
| error | mae_g |
| validity_scope | ingredient_candidates（不重複）、mass_range_g（low < high） |
| producer_ref／approval | 核可者必須不同於執行校準者（`approval_not_independent`） |

使用門檻（核可不能豁免，調整須修訂本契約）：approved、sample_count ≥ 30、source_group_count ≥ 2、
covered_count／sample_count ≥ nominal。30 與 2 是 v0 暫定下限，不是統計充分性的證明。
估計的 coverage 必須等於校準 nominal；食材與區間須落在 validity_scope 內。

## 5. Assessment：完整性失敗與就緒阻擋

`assess_portions(envelope, estimates=, calibrations=)` 接受 Visual Ingredient envelope，逐筆視覺證據產生
PortionItem；食譜隱藏候選另列 `hidden_ingredients`，一律 unavailable、mass_g=null。

| 類型 | 結果 | reason |
| --- | --- | --- |
| 無估計 | unavailable | photo_mass_unknown |
| 完整性失敗 | unavailable，不顯示區間 | invalid_estimate、evidence_hash_mismatch、ingredient_candidate_mismatch、duplicate_estimates、estimate_rejected_by_review、calibration_unavailable、duplicate_calibration_id、calibration_invalid、calibration_hash_mismatch、calibration_method_mismatch、coverage_differs_from_calibration、calibration_revoked |
| 就緒阻擋 | estimated，ready_for_energy=false | independent_review_not_requested／pending、calibration_not_approved、calibration_sample_too_small、calibration_source_groups_insufficient、calibration_coverage_below_nominal、outside_calibration_ingredient_scope、outside_calibration_mass_range |

整體 status：全部 estimated 為 estimated、部分為 partial、皆無為 unavailable。`nutrition_eligible` 恆為 false。

## 6. 不確定性傳遞

- 單項能量：質量區間 [m_lo, m_hi] 與每 100 g 能量 [e_lo, e_hi]（無區間時 e_lo = e_hi = 原報告值），
  能量 = [m_lo·e_lo／100, m_hi·e_hi／100]，以 Decimal 精確計算。
- 涵蓋率下界採 Bonferroni union bound，不假設獨立：單項 max(0, c_m + c_e − 1)；
  總和 max(0, 1 − Σ(1 − b_i))。營養參考值沒有區間時，下界為 null，
  `coverage_note=composition_uncertainty_not_quantified`，不假裝已量化成分變異。
- 不乘偵測 confidence、不依 retrieval_score 加權、不平均候選食譜。

## 7. 串接營養估算

`estimate_photo_energy(envelope, estimates=, calibrations=, nutrition_bases=, consumption=)` 內部重新計算
assessment，不接受呼叫端提供的結論。PhotoNutritionBasis（封閉欄位）：

| 欄位 | 定義 |
| --- | --- |
| basis_id／estimate_id／ingredient_candidate | 綁定單一估計；食材字串須完全相同（`basis_ingredient_mismatch`） |
| purpose | 固定 photo_calculation；recipe 用途不可乘照片質量 |
| energy | original_name、unit（kcal／kJ，不互轉）、per=per_100g_edible_portion、value、可選 interval |
| gates | source_use、identity、basis、integrity；皆須 passed 且有 evidence_ref |
| reviewer_ref／usage | 非空；usage 固定 diagnostic/test_only |

照片計算不得以 not_applicable 跳過任何 gate（沿用 nutrition_reference_policy_v1 §4）；未通過記為
`gate_<name>_<status>`，項目 reason 為 `nutrition_basis_unresolved`。

總和只有在下列皆成立時才 computed：每筆視覺證據都已計算（`visual_items_unavailable`）、有食用聲明
（`consumption_basis_missing`）、單一能量單位（`mixed_energy_units`）、同食材區域不重疊
（`overlapping_same_ingredient_regions`，避免同一份量重複標註）。結果固定 `scope=visible_items_only`；
有食譜隱藏候選時 `completeness=incomplete_hidden_ingredients_unquantified` 並列於 `excluded_recipe_priors`：
它們可能解釋額外熱量，但不能由照片量化。`nutrition_eligible` 與 `production_eligible` 恆為 false。

## 8. 與既有介面的關係

- 現行 nutrition review profile 只支援 recipe_declared_ingredient_reference，且 photo_consumed_mass 僅能
  not_applicable；尚無任何 photo_calculation basis，因此實際輸出維持 unavailable。
- diagnostic_demo、yolo_diagnostic、Streamlit 與 Visual Ingredient envelope 未修改；
  `photo_portion_grams=null`、`calorie_estimate=unavailable` 照舊。

## 9. 驗收與下一步

`tests/test_portion_estimation.py` 以既有合成 fixture（小黃瓜 observed、紅蘿蔔 uncertain、烹調油／糖為
食譜隱藏候選）及合成數值驗證：預設 unavailable、證據邊界、禁止質量來源、hash 綁定、校準門檻、
複核獨立性、區間與 Bonferroni 計算、gate、食用聲明、kJ 不轉換、重疊與單位阻擋、輸入不變。
所有數值皆為測試用合成數字，不是該食材的量測或營養資料。

下一步依序：(1) 取得合法、可依來源分組的秤重資料，提出真正估算方法並完成校準；
(2) 新增 photo_calculation 用途的營養基準審查 profile；(3) 審查後才把 assessment／energy 區塊接入
diagnostic 輸出；(4) 定義剩食前後對比的食用量。
