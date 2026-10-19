# Review Validator / State Aggregator — Prototype Profile v0

日期：2026-10-12。依使用者確認的暫定格式實作；**不修改正式Review Contract v1**。
正式契約：[Ingredient Identity / Nutrition Basis Review](ingredient_identity_nutrition_basis_review_contract_v1.md)。
程式：review_types.py、review_validation.py、review_aggregation.py，位於src/nutrition/。
用途固定diagnostic/test_only，nutrition_eligible永遠false。

## API

```python
from src.nutrition.review_validation import validate_review
from src.nutrition.review_aggregation import aggregate_review

report = validate_review(review_input, attribute_results,
                         context=review_context, trust_anchor=application_trust_anchor)
decision = aggregate_review(review_input, attribute_results,
                            context=review_context, trust_anchor=application_trust_anchor)
```

Validator不產生逐屬性判斷，只比對既有人工審查紀錄、原件及scope。
Aggregator每次重跑Validator，再對同一份獨立副本聚合；不能傳入valid=true作為旁路。
輸入／原件不修改，結果attribute_results為独立副本。

report.state為valid／invalid／blocked；report.valid只在valid且無issues時true。
issues保留code、path、message。缺上下文、未審查紀錄、未知執行規則／版本或不支持格式為blocked；
schema／scope／hash／原值／參照錯為invalid。兩者都不傳出已驗證屬性集合。
Aggregate無效／blocked均保守回傳identity_decision=basis_decision=invalid、input_integrity=invalid、
attribute_results=[]與blocking_reasons；查具體定位及blocked差異須讀report。這不表示食品本身一定不相容。

## 1. 精確Review Input格式

必要且只能包含contract、schema_version、purpose、recipe_scope、candidate_scope、attachment_refs、
basis_target、claims、evidence。contract固定foodcal.ingredient_source_review；schema_version為整數1（bool拒絕）；
purpose固定diagnostic/test_only。不接受approved、matched或nutrition_eligible等額外輸入欄。
recipe_scope/candidate_scope、Claim、Evidence、Attribute Result沿用正式契約命名，型別見review_types.py。

basis_target的暫定唯一格式：

```json
{
  "purpose": "recipe_declared_ingredient_reference",
  "required_nutrients": [{"original_name": "熱量", "expected_unit": "kcal"}]
}
```

required_nutrients非空；每項只有original_name、expected_unit兩個非空字串；original_name唯一。
不把熱量／修正熱量默默合併。必要屬性是5個identity＋6個一般basis＋每項營養的units及definition。
不能少項、重複、增加未知屬性或用null繞過營養key。

attachment_refs必要且只能有四個必要引用及evidence_files清單；每個ref只有
attachment_id（非空）、representation（bytes/native）、sha256（64字元小寫hex或null）。

```json
{
  "parsing_snapshot": {"attachment_id": "parsing", "representation": "native", "sha256": null},
  "expected_recipe_source": {"attachment_id": "expected", "representation": "native", "sha256": null},
  "candidate_lookup": {"attachment_id": "candidate", "representation": "native", "sha256": null},
  "source_provenance": {"attachment_id": "provenance", "representation": "bytes", "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "evidence_files": [{"attachment_id": "sample", "representation": "bytes", "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}]
}
```

示例hash是佔位格式，不能用作實際來源證明。bytes必須核對實際內容SHA-256；native必須null。
parsing_snapshot／expected_recipe_source接受原生物件或UTF-8 JSON bytes；candidate_lookup只接受既有Python結果；
source_provenance必須JSON bytes。Evidence只能指向bytes原件，不能對native repr作hash。
源provenance須含archive_sha256、member_sha256、source_url、retrieved_at_utc；official_dataset_version可null。
所有來源分析列須完整、原field/value/unit、Decimal解析結果與sample/object hash一致，不能只留選中的能量列。

## 2. 支援的Evidence locator

每項Evidence只能有evidence_id、origin、attachment_ref、attachment_sha256、locator、raw_value、
evidence_text、statement_role。ID唯一，raw_value為有限JSON scalar或null，evidence_text字串或null。
origin由独立context catalog核對，不能由request自稱；statement_role維持正式契約列舉。

| origin | locator精確欄位 | 確定性核對 |
| --- | --- | --- |
| official_record | sample_filename、source_row_index、source_object_sha256、field_name | 已提供sample陣列原件、完整Source觀測及scope；源row index與sample row index分開；比對原JSON物件hash及原field值 |
| recipe_text | start、end | 零起算Unicode code point半開區間；附件bytes等於Parsing原文UTF-8，跨度須屬此ingredient的已驗證evidence範圍 |
| caller_assertion / unverified_claim | pointer | UTF-8 JSON原件；RFC6901 JSON Pointer，不接受URI fragment，~0/~1正確解碼，array不允許負index、前置零或- |
| official_document | page、section | 暫只支持原本就是UTF-8 JSON的文件；page為正整數，section為RFC6901 pointer；文件須有page及適用archive_sha256，且內容／版本適用性已有外部人工紀錄 |

caller_assertion範例：

```json
{"pointer": "/x~1y/~0z"}
```

此pointer解析JSON原件的鍵x/y，再解析鍵~z；原值0與false不得等同。
plain JSON Pointer根字串空值合法；缺key、非法escape及超界array拒絕。
官方文件的JSON支持是暫定受限profile，不是PDF文字抽取；自行從PDF產生的JSON不自動獲official_document來源。
PDF原件以%PDF辨識，回傳unsupported_evidence_format / blocked；停止該證據及整體聚合，不將其他部分拼成核可。
其他無法按支持格式解析的原件拒絕，不補造raw_value或evidence_text。

## 3. 獨立review_context與信任根

ReviewContext有record_bytes（UTF-8 JSON，原件bytes）及attachments（attachment_id→bytes/native物件）。
ReviewTrustAnchor有authority_ref與context_sha256；必須由應用程式的**獨立可信配置**提供，
不能從模型／request／context本身計算並回填後冒充認證。此API不實作人員認證或簽章系統。
若呼叫端無法提供這個獨立信任前提，必須不傳anchor；Validator回review_context_untrusted / blocked。
anchor構造器、authority字串、hash相等本身都不是人員真實身分或正式來源授權的證明。

record_bytes必要且只能含：

- profile固定foodcal.review_context.v0、authority_ref（等於独立anchor）。
- recipe_scope、candidate_scope（與Review Input逐欄相同）。
- claims、evidence（與已人工核對的完整輸入声明／證據逐欄相同，不只簽status字樣）。
- attachment_catalog：attachment_id→origin、representation、sha256、scope。
  scope精確為 `{recipe_scope: ..., candidate_scope: ...}`；ref、實際原件及catalog皆需一致。
- reviews：每個必要屬性一筆，精確包含result（完整Attribute Result）、review_status、method、process_ref。

review_status只有reviewed才可使用；其他狀態回review_not_checked / blocked。
process_ref為非空審查流程識別；result需包含reviewer_ref、reviewed_at、evidence_ids等全部契約欄。
result不與request提供的逐屬性結果完全一致時回forged_review_result；不自行重寫狀態。
本v0支持method=manual、rule_id=manual_evidence_comparison_v1、rule_version=1；
或method=controlled_rule且僅photo_consumed_mass使用diagnostic_recipe_reference_excludes_photo_v1、version=1。
其他執行規則／版本blocked，待正式決議後擴充。

可機器核對的review紀錄摘要（非完整context）：

```json
{
  "review_status": "reviewed",
  "method": "manual",
  "process_ref": "test://independent-review-process",
  "result": {
    "domain": "identity", "attribute": "part", "nutrient_key": null,
    "status": "unresolved", "reason_code": "part_ambiguous",
    "recipe_claim_ref": null, "source_claim_ref": null, "evidence_ids": [],
    "rule_id": "manual_evidence_comparison_v1", "rule_version": "1",
    "reviewer_ref": "test://reviewer", "reviewed_at": "2026-10-12T00:00:00+08:00"
  }
}
```

時間與test識別是範例，不是已審查真實食品。attachments與anchor都由受控呼叫端提供，
不能讓模型提供整套自我一致的context與anchor。來源可識別與版本適用性依context及原件核對，沒有HTTP查證。

## 4. 狀態證據條件與Aggregator

Validator檢查Parsing Contract、expected_source、input/ingredient/text hash、Candidate scope/request hashes、
唯一found條目、原始來源scope／URL／時間／hash、原件value/span／引用及独立人工紀錄一致性。
即使Candidate寫link_check=verified，也重新核對其request和全部原始分析列；不信任該字樣作證明。
verified/mismatch須引用known claims與有效证据；identity與mismatch需兩側claim及recipe evidence；
單邊basis定義可只引用來源。caller_assertion或unverified_claim不能作verified/mismatch/NA的充分證據。
processing不能將sample_preparation角色證據當作食譜processing；data_version為null不能verified。
明示g僅支持recipe-declared質量，缺量、顆／匙／ml均不能verified質量；數量0與缺值分開。
單位缺失或所需營養值未解析／missing不能verified相應基準；不推論營養方法。
recipe_declared_mass允許只用食譜單邊證據；其decimal claim須與已驗證Parsing quantity及原unit一致，不能補造0。
data_version與nutrient_units的source claim須與獨立原件的版本／原單位一致。
所有狀態的語意結論由獨立已人工核對的紀錄提供；Validator不分類食材、不判斷同義詞或自動把不同值改為一致。

Aggregator只處理Validator核對後的結果：每domain mismatch優先於unresolved，再到compatible。
保留全部mismatch/unresolved reason，不因其中一項mismatch丟掉其他blocker。
兩domain獨立，兩者compatible仍nutrition_eligible=false。不產生克数換算、熱量或正式approved。

## 5. 測試與尚待納入正式契約的事項

`tests/test_nutrition_review.py` 自行建立明標synthetic/test_only的Parsing、來源原件、文件、人工紀錄與獨立fixture-owner pin。
這是證據協定測試，不是官方資料或真實人工營養核可；沒有依賴被Git忽略的原件／服務。
前10個測試對應正式契約驗收表；另測context缺失／待審查／hash錯、偽造結果／approved、pointer escape、
跨scope／來源物件、低信任證據、非法NA、重複／缺屬性、數值篡改及輸入不可變性。

```python
# 可執行合成使用範例，不可冒充正式reviewer/context。
from tests.test_nutrition_review import SyntheticReview
fixture = SyntheticReview()
report = fixture.validate()
decision = fixture.aggregate()
assert report.valid and decision['nutrition_eligible'] is False
```

正式化前仍需確認：上述wire格式、可信context的供應與保護流程、真正人工reviewer識別與稽核、
官方JSON文件适用性表示及PDF支援方式、逐屬性詞彙／語意規則、原件pin生命週期。
目前完整compatible案例只屬合成fixture；八筆真實快照的版次／可食部／方法缺口仍未獲核可。
不改正式契約，不回寫Candidate、TFDA Source或Mock；不進入自動語意核可引擎。

本輪實際驗證：新增27項測試通過；乾淨隔離副本27項通過。
主測試293項中291通過、2個明確外部快照測試預設skip；Gemma實驗33項離線測試通過，未執行推論。
本輪沒有拿真實8筆建立完整compatible結果，亦未嘗試真實人工核可。
