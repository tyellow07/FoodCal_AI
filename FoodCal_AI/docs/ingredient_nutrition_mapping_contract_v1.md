# Ingredient → Nutrition Mapping Contract v1

狀態：離線 synthetic/test_only 原型。銜接 Recipe Parsing Contract v1，沒有修改
Parsing、Detection 或 Retrieval 的契約。沒有正式營養資料來源，也不計算熱量。

## 1. 輸入與可信邊界

`MockNutritionMappingAdapter.map(parsing_result, *, expected_source,
identity_assertions=None)` 接受完整 Parsing v1 結果，先沿用原 Validator 驗證。
不可只傳食材名称及數量，否則會失去原文、證據及快照身分。

`expected_source` 是呼叫端從可信流程取得的完整 Source 快照，必須與解析結果的
Source 完全相同；不得從未驗證模型答案複製此欄位來假裝獨立核對。相等只代表
快照一致，不證明來源真實、授權、人工核可或模型沒有漏抽資訊。既有模型輸出應先
通過 Recipe Parsing Trust Boundary；本 Adapter 不取代該邊界。

可選 `identity_assertions` 以本快照的 ingredient_id 為 key。每項必須包含：

| 欄位 | 型別與語意 |
| --- | --- |
| attributes | 完整五欄 object：food、part、state、quality、weight_basis，各為非空字串或 null |
| evidence_reference | 呼叫端身分判斷的非空證據 locator；不是模型自稱，也不代表已驗證其內容 |
| source_text_sha256 | 必須等於 Parsing Source 的原文 hash，禁止跨快照套用 |

這些屬呼叫端提供的**測試身分斷言**，不是從名稱自動推導的事實。null 表示未知。
`quality` 指品質／規格描述，不是預測信心或資料可靠度；各屬性使用精確字串比較，
尚無正式詞彙表或同義詞解析。fixture 的 synthetic_cut_a、synthetic_unspecified_grade
只供測試，不是食品分類標準。reference 使用可分享 locator，不放私人路徑或憑證。

## 2. 結果欄位

Python 型別：`src/nutrition/mapping_types.py`。輸出可用 json.dumps 序列化。

| 外層欄位 | 定義 |
| --- | --- |
| contract / schema_version | foodcal.ingredient_nutrition_mapping / 整數 1 |
| status | complete / partial / unavailable / failed，見下節 |
| adapter_kind | 固定 mock |
| fixture_sha256 | 本次載入的 fixture 原始 bytes SHA-256 |
| parsing_snapshot | 完整獨立 Parsing 快照或 null；保留 source、binding、provenance、原 status、issues、所有 steps/evidence |
| source_check | matches_caller_snapshot / not_checked，不能解讀為來源認證 |
| qualification | 固定 test_only、eligible_for_production=false 及 machine-readable reasons |
| ingredients | 每項宣告一筆 IngredientMapping，維持原順序與數量，不合併同名食材 |
| issues | code、severity、JSON Pointer path、message，與 Parsing Issue 結構相同 |

每項 IngredientMapping 必要欄位：ingredient_id、ingredient（原食材完整副本）、
identity_assertion（有效 caller 斷言或 null）、preparation_step_ids、mapping、mass、
qualification、issues。全球識別仍採 `(input_id, source.text_sha256, ingredient_id)`。

### 三個獨立結果

| 結果 | 狀態與規則 |
| --- | --- |
| mapping | matched：完整五欄身分已知且恰有一個符合候選；ambiguous：身分未知或多筆符合；unavailable：沒有精確名稱候選或身分衝突；error：caller 身分斷言無效 |
| mass | available：exact 數量且單位是明示 g／克；unavailable：其他情況，mass_g=null |
| qualification | 本原型一律 test_only，無正式使用資格；不受 matched 或 available 影響 |

`mapping` 包含 status、reason、selected_entry_id、candidates。候選為完整條目快照，
保留 entry_id、ingredient_name、identity、source、nutrient_basis、nutrients。
只有 matched 的 selected_entry_id 非 null 且必須出現在唯一候選中。
名稱只用來發現候選，精確名稱相同仍不能證明食品、部位、生熟狀態、品質及重量基準。
已知屬性只篩掉衝突候選；未知屬性禁止自動選定唯一條目。不使用模糊相似度選取。

`mass` 包含 status、reason、mass_g、basis、rule、evidence_links。
basis 固定 recipe_declared_ingredient；available 的 rule 是 literal_g，mass_g 等於
原 quantity，包含明示 0。它只是食譜宣告的克數，不證明是可食部、特定生熟狀態、
整批／每人份、照片食用份量，也不證明與營養條目的 per_100g_edible_portion 相容。
identity.weight_basis 另存呼叫端適用基準；本原型不進行可食率、烹調損耗或產量換算。

vague、missing、unparsed 保留 null、quantity_raw 及 uncertainty；半顆維持 0.5 顆。
顆、根、匙、大匙、ml 等一律沒有換算依據，不產生質量，不估密度或常見份量。
`optional`／`optional_state` 原樣保留，not_marked 不表示必加或照片中存在。

preparation_step_ids 僅列出原步驟明示關聯；完整步驟與證據留在 parsing_snapshot。
mix/slice/add 不推導生熟狀態、執行順序、吸油率、營養修正或新食材。

## 3. 狀態、診斷與 fixture

complete 只表示每項 mapping=matched 且 mass=available；仍可能是未審查的部分解析，
也永遠不表示可以計算熱量。其餘逐食材處理結果為 partial；全數身分斷言錯誤為
failed（保留有效 Parsing 快照及逐項診斷）。原 Parsing unavailable/failed 原樣傳遞。
Parsing 無效、來源不符或 assertion ID 不在本快照時，failed 並清空食材與快照，
不傳出未驗證結果。無效 fixture 在建構時拋 MappingFixtureError，不退回舊資料庫。

稳定 reason/code：unique_explicit_identity、identity_unresolved、multiple_compatible_entries、
no_exact_name_candidate、identity_conflict、invalid_identity_assertion、explicit_grams、
quantity_vague、quantity_missing、quantity_unparsed、unit_missing、conversion_basis_unavailable、
invalid_parsing_contract、source_snapshot_mismatch、invalid_identity_scope。
前兩類未解結果用 status/reason 表達；錯誤另有定位 issues。Parsing 錯誤保留底層
錯誤代碼與原因於 message，path 前綴 /parsing_snapshot；局部錯誤定位
/ingredients/<index>/identity_assertion。只有無效斷言的欄位不會回傳原始壞值。

fixture：`tests/fixtures/nutrition_mapping.json`，4 個 synthetic/test_only 條目，
包含同名牛肉生／熟歧義；nutrients 固定 null，刻意不提供虛構營養數值。
catalog 與条目嚴格拒絕未知欄位、重複 ID／JSON key、非有限數值、錯誤型別、
非 synthetic/test_only 來源或任何營養數值。nutrient_basis 是結構測試欄位，
不代表正式數值存在。Mock 不讀取舊 NutritionDatabase，也不連外或載入模型。

## 4. 核心 invariants 與 JSON 摘要範例

1. 每個有效 Parsing 食材恰對應一筆，原文、數量、單位、optional、uncertainty、anchor 與 evidence 不變。
2. 相同快照、fixture 及斷言得到相同結果；輸入與 fixture 不被回傳物件修改。
3. 所有 step/evidence 參照沿用已驗證快照；不同快照的斷言禁止使用。
4. matched 與 available 互不推導；test_only 無法經 caller/model approved 宣告升級。
5. 未知質量只能 null，不以 0、平均值、常見份量填補；顯式零與未知不同。
6. 不產生 kcal、營養推論、照片質量或料理損耗。

以下是 case_01 雞胸肉在明確 synthetic caller 身分斷言下的**單項摘要**，
不是完整輸出；完整可執行範例見 `src/nutrition/README.md`：

```json
{
  "ingredient_id": "ing_3_6",
  "mapping": {"status": "matched", "reason": "unique_explicit_identity", "selected_entry_id": "synthetic-chicken-breast-raw"},
  "mass": {"status": "available", "reason": "explicit_grams", "mass_g": 200, "basis": "recipe_declared_ingredient", "rule": "literal_g"},
  "qualification": {"status": "test_only", "eligible_for_production": false}
}
```

## 5. 未解決與下一步

尚未定義正式食品身分詞彙、來源認證／授權政策、身分證據的語意審核、可信單位換算、
營養數值欄位／單位／版本、整批與份數及圖片份量；這些不能由此 Mock 補造。
先獨立決議可信營養條目來源與身分審核，再考慮注入受控來源 Adapter，不修改 Parsing v1。
舊 database.py 缺 provenance、portion 固定區間與 mvp_pipeline.py 的 top_//food
引用問題另記，未在本次改寫。沒有新增正式熱量計算或自動提交。
