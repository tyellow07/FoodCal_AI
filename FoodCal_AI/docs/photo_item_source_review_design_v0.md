# Photo Item Source Review Design v0（提案＋v0 實作）

日期：2026-11-11 設計；2026-11-13 依第 10 節**建議預設**完成 v0 實作（第 12 節）。第 10 節仍**待團隊確認**，
確認結果與暫定值不同時須同步修改程式與測試。
不修改 Review Contract v1 語意、Prototype Profile v0、Portion Contract v0 或 Local Context Provider；
`review_validation.py` 只把共用 helper 移到 `review_common.py`，行為不變。
目的：讓 Portion Contract v0 的 `photo_calculation` 營養基準（`PhotoNutritionBasis`）能由受控人工審查產生，
且全程不經過食譜。用途固定 `diagnostic/test_only`，`nutrition_eligible=false`。

核心原則：**Recipe candidates may explain hidden calories, but are not visual proof.**

## 1. 為什麼不能直接套用 Review v1

| Review v1 的綁定 | 程式位置 | 套用到照片的後果 |
| --- | --- | --- |
| 審查範圍是 `recipe_scope`（input_id、ingredient_id、recipe_text_sha256） | `review_validation.py` `_validate_shape`／`_verify` | 每次照片計算都得先掛到某份食譜的食材 |
| 附件必須是 Parsing、expected_source、CandidateLookup；候選條目來自食譜食材的 candidate lookup | `_verify` 附件槽位、`mock_mapping_snapshot` 核對 | 沒有食譜的照片無法審查 |
| 身分 verified／mismatch 必須有 `recipe_text` 雙邊證據 | `two_sided_evidence_required` | 由食譜原文證明照片食材的身分、生熟與處理方式，違反核心原則 |
| basis 用途固定 `recipe_declared_ingredient_reference`；`photo_consumed_mass` 只能以規則 NA | `basis_target`、`NA_RULE` | 從未審查照片質量與營養基準的相容性 |

因此提案採**平行的照片食材審查**：沿用 Review v1 的信任與證據機制，但範圍、附件與目標端證據改為照片。
食譜、Parsing 與食譜隱藏候選都不得進入這個審查。

## 2. 沿用與不沿用

- **沿用**：TFDA 來源核對（provenance、sample 原件、逐列 observation、原值與 Decimal 解析）、
  獨立 ReviewContext／ReviewTrustAnchor 與 ledger 一致性、`official_record`／`official_document`／pointer
  locator 規則、`manual_evidence_comparison_v1`、聚合規則（mismatch 優先於 unresolved，再到 compatible）、
  Local Context Provider 的 registry／pin／revision 生命週期。
- **不沿用**：`recipe_scope`、Parsing／expected_source／CandidateLookup 附件、`recipe_text` 來源、
  `recipe_declared_mass` 的人工審查。
- 實作時先把共用 helper（JSON pointer、JSON 載入、來源 observation 核對）從 `review_validation.py`
  抽出，**行為不變**，既有 Review v1 測試不得修改即通過；照片審查放在新模組，不在既有函式內分支。

## 3. Review Input（提案格式）

所有欄位必要且封閉；不接受 approved、matched、eligible 等額外欄位。

| 欄位 | 規則 |
| --- | --- |
| contract／schema_version／purpose | `foodcal.photo_item_source_review`／整數 0／`diagnostic/test_only` |
| photo_scope | image_sha256、evidence_type（ingredient_annotation／reviewed_prediction）、evidence_id、ingredient_candidate、evidence_sha256、estimate_id、estimate_sha256；後兩個 hash 採 Portion Contract 的 canonical JSON v0 |
| candidate_scope | 與 Review v1 相同：provider=tfda、dataset=8543、food_id、original_name、data_type、archive／member／sample sha256 |
| source_link | food_id、expected_archive_sha256、expected_sample_sha256、link_ref；比照 candidate lookup 的明示 source link，不做模糊比對，也不由類別名稱推得食品 ID |
| attachment_refs | 封閉槽位：visual_evidence（bytes）、portion_estimate（bytes）、source_lookup（native `SourceLookup`）、source_provenance（bytes）、evidence_files（bytes 清單） |
| basis_target | purpose 固定 `photo_consumed_mass_calculation`；required_nutrients 規則同 Review v1（非空、original_name 唯一，熱量與修正熱量不合併） |
| claims／evidence | 結構同 Review v1；claim 的 side 改為 `photo`／`source`，不接受 `recipe` |

附件核對：

- **visual_evidence**：bytes 的 canonical JSON hash 等於 `evidence_sha256`；`evidence_identity` 與 photo_scope
  一致；`evidence_blocker` 必須為 None（observed_candidate、食材層級、有合法 image_region）。
- **portion_estimate**：`validate_portion_estimate` 無 issue；hash 等於 `estimate_sha256`；其 evidence_ref 綁定同一筆
  視覺證據。份量的複核與校準是否就緒，仍由 `estimate_photo_energy` 重算 assessment 時把關，本審查不重複判定。
- **source_lookup**：status=found、唯一 `FoodRecord` 且與 candidate_scope 一致；observation 核對與 Review v1 相同。
  `production_eligible`、`identity_approval` 及三個 applicability 欄必須仍為原始的 False／unresolved。
- ledger 的 attachment_catalog scope 精確為 `{photo_scope, candidate_scope}`；出現食譜附件即 invalid。

## 4. Evidence 來源

| origin | locator | 核對 |
| --- | --- | --- |
| official_record／official_document | 同 Review v1 | 同 Review v1 |
| photo_evidence_record | pointer | 原件必須正是 visual_evidence 附件；例如 `/ingredient_candidate`、`/image_region` |
| photo_portion_record | pointer | 原件必須正是 portion_estimate 附件；例如 `/mass_g`、`/mass_kind` |
| photo_visual_review | pointer | 獨立人工視覺判斷紀錄（第 5 節），放在 evidence_files，origin 由 catalog 決定 |
| caller_assertion／unverified_claim | pointer | 同 Review v1；不能支持 verified、mismatch 或 NA |
| recipe_text | — | 拒絕：`recipe_evidence_not_allowed` |

statement_role 新增 `photo_observation`、`photo_mass`；照片角色的證據不能用來支持來源端聲明，反之亦然。

## 5. 照片端身分證據

現行標註規範 `ingredient-bbox-v2-draft-0` 只有三類：visible_fried_egg、cooked_rice_region、
cooked_noodle_region，且定義明示「不核可 over-easy 工法」、「保留小麥／米等身分未知」。標註因此只足以支持
食材候選，**不足以證明** part、state、processing、quality_composition。

提案新增 `foodcal.photo_visual_review.v0` 紀錄，由看過原圖的獨立審查者填寫：

- photo_scope（逐欄等於 Review Input）、reviewer_ref、reviewed_at、image_access=`reviewed_original_image`。
- judgments：food、part、state、processing、quality_composition、edible_fraction_visible；
  每項 value_state 為 known／unknown／missing，known 需要 value 與 region 參照。
- 看不出來的保持 unknown，例如吸油量、調味、內餡，或外觀無法區分的生熟；不得由料理名稱或食譜補值。
- 審查者不得同時是該張圖的標註者或份量紀錄的 producer。

## 6. 必要屬性與規則

identity 沿用五項：food、part、state、processing、quality_composition。照片端 claim 的證據必須是
photo_evidence_record 或 photo_visual_review；verified／mismatch 需要照片與官方兩邊證據。

| basis attribute | 照片用途的核對內容 |
| --- | --- |
| reference_measure | 官方「每100克含量」標籤；同 Review v1 |
| edible_portion | 份量紀錄的可見質量是否含骨、殼、皮等非可食部，與每 100 g 可食部基準是否相容；需要 edible_fraction_visible 判斷與官方證據 |
| weight_basis | 官方樣品狀態（生、熟、乾）與拍攝當下狀態是否一致；生樣品基準乘熟食質量為 mismatch，v0 沒有任何核可換算 |
| photo_consumed_mass | **必須審查，不可 NA**：照片端 claim 等於份量紀錄的 mass_g 與 `visible_mass_at_capture`，證據為 photo_portion_record；食用聲明仍由 estimate_photo_energy 處理 |
| recipe_declared_mass | 只能 not_applicable，controlled_rule `photo_item_excludes_recipe_mass_v1`；任何人工審查結果都是 invalid |
| data_version | 同 Review v1；官方版次為 null 時只能 unresolved |
| nutrient_units／nutrient_definition | 每項 required nutrient 各一筆，同 Review v1；kcal／kJ 不互轉 |

執行方法：其餘屬性只接受 manual（`manual_evidence_comparison_v1`、version 1）；controlled_rule 只用於
recipe_declared_mass 的 NA。其他規則或版本一律 blocked。

## 7. 來源使用與完整性 gate 紀錄

依 nutrition_reference_policy_v1 §4，每個 gate 保存狀態、審查者、時間、policy revision、evidence locator 與
reason code，並綁定用途與快照。提案 `foodcal.source_gate_record.v0`：

| 欄位 | 規則 |
| --- | --- |
| gate | source_use／integrity |
| purpose | 固定 photo_calculation；照片計算不接受 not_applicable |
| candidate_scope | 與 Review Input 逐欄相同 |
| status | pending／passed／failed；passed 需要 evidence_locator、reviewer_ref、reviewed_at 與已登記的 policy_revision |
| reason_code | 例如 license_scope_unconfirmed、attribution_missing、snapshot_missing、version_conflict、nutrient_missing |

gate 紀錄放在同一份受信任 ledger 中，由 anchor pin 保護，不能由 request 自行宣告。
source_use 須由人核對 TFDA 資料集條款中的使用、標示與再散布義務；未核對前為 pending。

## 8. 聚合與產生 PhotoNutritionBasis

提案 API：`build_photo_nutrition_basis(review_input, attribute_results, gate_records, *, context, trust_anchor, energy_nutrient)`。
每次重新執行完整驗證，不接受呼叫端的結論：

- `gates.identity`／`gates.basis`：對應 decision 為 compatible → passed；incompatible → failed；needs_review 或 invalid → pending。
- `gates.source_use`／`gates.integrity`：取自驗證後的 gate 紀錄；缺紀錄為 pending。
- `energy`：取自已核對的 observation，`original_name`、`unit`、`per_100g_edible_portion`，value 為
  `NutrientValue.parsed_value` 的精確十進位字串，且 status 必須為 reported；v0 不提供成分區間，因此能量涵蓋率下界維持 null。
- `estimate_id`、`ingredient_candidate` 取自 photo_scope；結果直接作為 `estimate_photo_energy(nutrition_bases=[...])` 的輸入。
- 驗證 invalid 或 blocked 時不產生 basis，下游為 `nutrition_basis_unavailable`。即使全部 passed，
  `nutrition_eligible` 與 `production_eligible` 仍為 false。

## 9. 驗收案例（下一輪實作）

| 案例 | 預期 |
| --- | --- |
| 合成資料全部核對、四個 gate 都 passed | 產生 basis；estimate_photo_energy 可計算；nutrition_eligible 仍為 false |
| 附上食譜附件或 recipe_text 證據 | invalid，recipe_evidence_not_allowed |
| 視覺證據為 uncertain 或 dish_detection_only | invalid，photo_scope 不可審查 |
| 份量紀錄綁定其他證據或 hash 被改 | invalid |
| 外觀看不出生熟 | identity needs_review，state_unknown；identity gate pending |
| 官方生樣品對上熟食照片 | weight_basis mismatch；basis incompatible；basis gate failed |
| 可見質量含骨或殼，基準為可食部 | edible_portion unresolved 或 mismatch，不做廢棄率換算 |
| photo_consumed_mass 標成 NA | invalid_not_applicable |
| recipe_declared_mass 以人工方式審查 | invalid，只接受規則 NA |
| source_use 仍 pending | basis 的 source_use gate pending，下游 `gate_source_use_pending` |
| 官方版次 null | data_version unresolved，integrity 不能 passed |
| 熱量與修正熱量混用，或 kJ 參照 | 單位或定義 unresolved／mismatch；不轉換 |
| request 自帶自洽的 context 與 anchor | blocked，review_context_untrusted |
| 食譜隱藏候選（油、糖） | 沒有視覺證據，無法建立 photo_scope；能量總和照舊排除 |

## 10. 待決事項（請團隊確認）

1. contract 名稱與 schema_version 0，以及「抽出共用 helper＋新模組」的實作方式（建議）。
2. `photo_visual_review` 紀錄格式、可撰寫者，以及與標註者、份量 producer 的獨立性要求。
3. 照片質量 claim 的表示：建議新增只供 photo_consumed_mass 使用的 value_kind `decimal_interval`。
4. recipe_declared_mass 規則 NA 的必要證據：建議引用 photo_portion_record，而非 Review v1 要求的 official_document。
5. source_use 由誰核對 TFDA 8543 條款，以及 policy_revision 的登記方式；integrity 是否接受固定快照代替官方版次（Review v1 也尚未決定）。
6. 只憑標註類別能否 verified food：建議不行，必須有 photo_visual_review 與官方證據。
7. Local Context Provider 新增 profile `foodcal.photo_review_context.v0` 與照片審查者的 registry grants。
8. 能量成分不確定性區間的來源；v0 沒有，涵蓋率下界維持 null。

## 11. 下一輪最小實作範圍

抽出共用 helper（既有測試不改即通過）；照片審查的型別、Validator、Aggregator、gate 紀錄與
`build_photo_nutrition_basis`；明標 synthetic/test_only 的合成 fixture；第 9 節驗收案例。
不改 Review v1 語意、不核發真實核可、不讀被 Git 忽略的私人資料或真實人工審查紀錄。

## 12. v0 實作紀錄（2026-11-13，暫定）

程式：`src/nutrition/review_common.py`（共用 helper）、`photo_review_types.py`、`photo_review.py`；
測試：`tests/test_photo_item_source_review.py`（全部為合成 test_only 資料，不讀私人資料或真實審查紀錄）。

```python
from src.nutrition.photo_review import validate_photo_review, aggregate_photo_review, build_photo_nutrition_basis
build = build_photo_nutrition_basis(review_input, attribute_results, gate_records,
                                    context=owner_context, trust_anchor=owner_anchor, energy_nutrient='熱量')
# build['status'] built|unavailable；build['basis'] 直接作為 estimate_photo_energy(nutrition_bases=[...]) 的輸入
```

每次呼叫都重新完整驗證，不接受呼叫端的結論；`nutrition_eligible`、`production_eligible` 恆為 false。
驗證 invalid／blocked 不產生 basis；驗證通過但 gate 未全 passed 時仍產生 basis，`reasons` 列 `gate_<name>_<status>`，
下游 `estimate_photo_energy` 以 `nutrition_basis_unresolved` 阻擋。其他原因碼：`energy_nutrient_not_reviewed`、
`energy_observation_missing`、`energy_value_unreported`、`energy_unit_not_supported`。

### 12.1 第 10 節暫定決策（依建議預設）

| # | 暫定實作 |
| --- | --- |
| 1 | contract `foodcal.photo_item_source_review`、schema_version 0；ledger profile `foodcal.photo_review_context.v0`。共用 helper 抽出後，Review v1 既有測試未修改即通過；另以 176,490 筆變異輸入比對新舊 validator，狀態、錯誤碼與路徑全部相同 |
| 2 | `foodcal.photo_visual_review.v0` 封閉欄位：profile、photo_scope、reviewer_ref、reviewed_at、image_access、judgments、usage；judgment 為 {value_state, value, region}，known 的 region 須與證據框同 format／coordinate_space 且落在框內。審查者不得等於視覺證據的 `evidence_source.annotator`、份量 producer 或 measured_by。**誰有資格撰寫**未實作，留給第 7 項的 registry grants |
| 3 | `decimal_interval` 只限 photo_consumed_mass 的 photo 端 claim（該 claim 也只能用它）：{low, high, coverage}、unit `g`，須與份量紀錄 mass_g 完全相同 |
| 4 | recipe_declared_mass 只接受 controlled_rule `photo_item_excludes_recipe_mass_v1` 的 NA，證據須含 photo_portion_record；其他狀態 `invalid_recipe_mass_review`；不得對它提出 claim |
| 5 | gate 紀錄 `foodcal.source_gate_record.v0`（第 7 節欄位＋profile）；passed／failed 須 reviewer_ref、reviewed_at 與等於 ledger `policy_revision` 的整數；passed 另須 evidence_locator；pending 不得帶審查欄位。integrity **不接受固定快照代替官方版次**：官方版次 null（`integrity_version_unknown`）或 data_version 未 verified（`integrity_requires_verified_version`）時 passed 為 invalid。誰核對 TFDA 8543 條款仍待指定，目前沒有任何真實 gate 紀錄 |
| 6 | 標註類別不能 verified food：identity 五項的 photo 端 claim 必須引用同名 judgment（pointer `/judgments/<name>/value`）且值相同（`photo_visual_review_required`、`photo_claim_judgment_mismatch`）；edible_portion 引用 edible_fraction_visible，weight_basis 引用 state |
| 7 | 2026-11-19 暫定實作：`src/nutrition/photo_context_provider.py` 的 `PhotoContextProvider`（registry profile `foodcal.local_photo_review_registry.v0`），grants 以 photo_scope 取代 recipe_scope；每筆屬性結果需 active 審查者與涵蓋該屬性、規則與 process_ref 的 grant，每筆已決定的 source_use／integrity gate 需 `['gate', <名稱>, null]` 權限；之後由 `build_photo_nutrition_basis` 重新驗證。未修改既有 LocalContextProvider。仍待團隊確認 |
| 8 | 不提供能量成分區間：basis `energy.interval=null`，涵蓋率下界維持 null |

### 12.2 實作時補上的規則（原文未明訂）

- 視覺證據的 `evidence_source.image_sha256` 必須等於 photo_scope.image_sha256（`photo_image_unbound`），且須記錄
  `evidence_source.annotator`（`visual_evidence_producer_unrecorded`），否則無法核對獨立性。標註規範與 reviewed
  prediction 已有 image_sha256，但現行 adapter 輸出沒有 annotator，因此 `fixture_demo`／`from_reviewed_detection`
  的證據目前還不能進入照片審查，須上游補記錄者後才能使用。
- 只屬於官方紀錄的屬性（reference_measure、data_version、nutrient_units、nutrient_definition）mismatch 只需官方證據與
  source claim；identity、edible_portion、weight_basis 與 photo_consumed_mass 的 mismatch 仍需雙邊證據。
- statement_role 綁定來源：photo_evidence_record／photo_visual_review → photo_observation，photo_portion_record →
  photo_mass，官方來源只能用來源角色，caller_assertion／unverified_claim 只能 caller_claim。photo 端 claim 只能引用照片
  來源，source 端只能引用官方來源；低信任來源兩端都可引用，但不能支持 verified／mismatch。
- 整份 ledger attachment_catalog（含未被引用的項目）都必須是同一組 photo_scope／candidate_scope，origin 屬於封閉清單；
  recipe 開頭的 origin 或帶 recipe_scope 的項目為 `recipe_evidence_not_allowed`。
- basis_id 為 `photo-basis:` 加 canonical hash（context pin、estimate_id、food_id、能量營養素）；identity／basis gate 的
  evidence_ref 為 `photo-review-context:sha256:<pin>#<gate>`；reviewer_ref 為逐屬性結果審查者（排序後以逗號分隔）。

### 12.3 驗收

第 9 節 14 案各有一個測試；另有 48 個攻擊案例表與證據邊界、ledger／gate、輸入不變測試，共 30 項。
逐一停用 `photo_review.py` 的 82 個 require 檢查，除一個多重防護檢查（其他規則已使它不可能單獨觸發）外，
每個都會讓至少一項測試失敗。下一步：第 10 節團隊確認、Provider 照片 profile／grants、上游視覺證據補 annotator。
