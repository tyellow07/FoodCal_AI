# Local Context Provider v0

日期：2026-10-12。程式 `src/nutrition/context_provider.py`，測試
`tests/test_nutrition_context_provider.py`。依 Context Provider Design v0 實作獨立 sidecar，
沒有修改正式 Review Contract、Prototype Profile、Validator 或 Aggregator。

## 信任前提與範圍

結果 usage 固定 `local_controlled/test_only`，submitter_authenticated=false，nutrition_eligible=false。
審查者註冊識別不是人員認證，身分證據 ref 是可信 operator 的登記指向，不是 Provider 自動驗證的身分證。
不提供帳號、密碼、簽章、官方來源線上查證或正式營養核可。

**可信 bootstrap** 必須獨立提供 registry_reader、原件 SHA-256 pin、authority_ref、policy_revision、
reviews 與 clock；不可讓同一個不可信請求配置这些參數。Registry bytes 及 pin 不能從待審查模型輸出
同時計算後冒充可信 root。固定配置檔可透過 `Path(...).read_bytes` 作 reader，但其 pin 與 revision
必須來自受控配置；檔案保護由應用程式／OS 管理，本模組不改 ACL。

不可信請求只能傳 context_id、expected_recipe_scope、expected_candidate_scope、basis_target。
API 沒有 reviewer 權限、approved、anchor、附件路徑等覆寫參數。Registry 或證據不提供自動登記功能。

native Candidate 物件目前仍無標準 wire 格式；本原型接受可信 owner 建立的 RegisteredReview，
在 bootstrap 深拷貝所有附件，單次查核再深拷貝。真實用途須由既有 TFDA Source Adapter／
已驗證 Parsing 流程建立它們；不可反序列化不可信 pickle、使用 repr hash 或接受 request 的 native 物件。
這是受控記憶體持有的信任前提，**不是 native 物件已獲密碼學認證**。
持有 process／Provider 私有狀態／可信配置寫入權限者可繞過本機 gate。

## API 與結果

```python
from src.nutrition.context_provider import LocalContextProvider, RegisteredReview

# 下列六個參數全部由可信 bootstrap 提供，不能從待審查 request 取得。
provider = LocalContextProvider(
    registry_reader=controlled_registry_reader,
    registry_sha256=independently_pinned_registry_hash,
    authority_ref=registered_authority,
    policy_revision=registered_revision,
    reviews={"context-001": RegisteredReview(saved_input_bytes, owner_review_context)},
    clock=controlled_clock,
)
result = provider.review_registered(
    "context-001", expected_recipe_scope=recipe_scope,
    expected_candidate_scope=candidate_scope, basis_target=basis_target,
)
```

RegisteredReview 保留完整 Review Input UTF-8 JSON 原件 bytes 與既有 ReviewContext。
Context 的逐屬性結果從已 pin 的 ledger 讀出；request 不能另供結果覆蓋。
JSON 拒絕重複 keys 與 NaN/Infinity。沒有通用 canonicalization；更換 bytes 編碼需重新登記。

ProviderResult 分別保留：

- provider_gate：passed／blocked，僅代表供應與授權 gate。
- issues：ReviewIssue(code, path, message)；不輸出原文、私人路徑或例外詳細內容。
- validation：既有 ReviewValidation 或 null；授權失敗不呼叫 Validator。
- decision：既有 AggregateDecision 或 null；只有授權、Validator、聚合及回傳前 Registry 核對成功才提供。
- context_id、policy_revision 與固定 usage、eligibility、submitter_authenticated。

provider_gate=passed 不保證 validation.valid；PDF 或缺證據仍由 Validator 拒絕，decision=null。
blocked 不改寫成已審查的 unresolved 屬性。合法 unresolved 維持原有 needs_review，
compatible 也不升級上游 partial/needs_review/test_only，不改動 Candidate／Source 資格。

## Registry 精確暫定格式

Profile `foodcal.local_review_registry.v0`。最外層只允許：
profile、authority_ref（非空字串）、revision（正整數，bool 不接受）、sources、reviewers、grants、contexts。
後四項均為非空字串 ID → record 的 object；不允許未定義 record 欄位。以下是完整欄位表，
recipe_scope/candidate_scope/basis_target 精確沿用 Review Profile v0，而非新的 Contract。

| Record | 必要欄位 |
| --- | --- |
| source | status、candidate_scope、official_dataset_version、source_url、retrieved_at_utc、evidence_ref、purposes |
| reviewer | reviewer_id、status、identity_evidence_ref |
| grant | reviewer_id、status、valid_from、expires_at、recipe_scope、candidate_scope、source_registration_id、purpose、process_ref、attributes、rules |
| context | status、valid_from、expires_at、registered_at、input_sha256、context_sha256、recipe_scope、candidate_scope、basis_target、purpose、source_registration_id、grant_ids |

status 僅 active/revoked/superseded；只有 active 可用。Context ID 為本機 ID，不是 TFDA ID。
reviewer_id 對應 AttributeResult.reviewer_ref；identity_evidence_ref/evidence_ref 為非空受控登記指向。
來源 purposes 精確為 `["diagnostic/test_only"]`；其他 purpose 固定 diagnostic/test_only。
official_dataset_version 為非空字串或 null；null 不當成已核可版次。
Source scope 包括 provider/dataset、食品 ID、原名、data_type 與 archive/member/sample hashes，
並與 Context、grant、request 逐欄相同。來源 URL／取得時間／版次與已提供 provenance 原件核對。
官方授權／取得鏈的真實性仍由 operator 註冊流程承擔，非字串核對可以認證。

grant attributes 是 `[domain, attribute, nutrient_key]` 陣列的清單；nutrient_key 可 null。
rules 是 `[rule_id, rule_version]` 陣列的清單。不支援萬用字元、模糊 scope 或隱式 domain 繼承。
process_ref 必須與 ledger 人工紀錄相等。必要屬性每項都須至少有一個有效精確 grant。
空 attributes/rules 表示沒有相應權限。grant_ids 為非空唯一 ID 清單。
input/context pins 是 64 字元小寫 hex；context pin 固定完整原件，input pin 亦固定完整 basis_target。

授權範例（合成片段，不是完整可用 Registry）：

```json
{
  "reviewer_id": "test://reviewer",
  "status": "active",
  "valid_from": "2026-10-11T00:00:00+08:00",
  "expires_at": "2026-10-13T00:00:00+08:00",
  "recipe_scope": {"input_id": "synthetic_review", "ingredient_id": "ing_0_3", "recipe_text_sha256": "PIN_FROM_OWNER"},
  "candidate_scope": "FULL_REVIEW_PROFILE_SCOPE_REQUIRED",
  "source_registration_id": "s1",
  "purpose": "diagnostic/test_only",
  "process_ref": "test://synthetic-review-process",
  "attributes": [["identity", "part", null]],
  "rules": [["manual_evidence_comparison_v1", "1"]]
}
```

佔位值不能通過驗證。完整合成 Registry 的可執行範例由 tests.test_nutrition_context_provider.OwnerFixture
明確建立，不含官方營養核可。測試用 clock 固定，沒有讀取實際時鐘或本機外部快照。

## 時間與政策生命週期

所有日期需帶時區，期限為 `[valid_from, expires_at)`；start 含端點，expires_at 不含端點。
Context／grant 須在 now 有效，reviewed_at 亦須在 grant 有效期內，且 reviewed_at ≤ registered_at ≤ now。
期限長度由可信 owner 明列，本原型不提供永久預設、寬限期或時鐘偏差容忍。
clock 為可信 bootstrap 注入；每個 Provider instance 拒絕觀測到的時間倒退。

每次查核重新讀取原件並核對獨立 Registry pin/revision；回傳聚合前再次讀取。
任何中途改動均丟棄 decision。變更或撤銷需要 owner 建立新 Registry revision，
並由可信應用程式替換新的 Provider instance；不自動計算新 pin 或接受 request 的 revision。
舊 Registry bytes 對新 pin 不匹配，或 pin 正確但 revision 不符合 bootstrap，均 blocked。

這不是跨程序防回退／全域單調儲存：應用程式須停止使用舊 instance，確保部署可信的最新 pin/revision。
保留並刻意呼叫舊 instance 的管理員仍可使用舊政策。單一 instance 非並行／多執行緒服務；
不聲稱能原子阻擋回傳後才發生的撤銷。

有效且未撤銷的同一紀錄可重播作可重現診斷；不是一次性 token。
撤銷／過期／更新來源後不能現行核對。此原型沒有 archival_only 入口；歷史閱覽另由唯讀原件工具處理，
不藉歷史閱覽繞過 gate。同食品 ID 的新 snapshot 不移轉舊審查。

## 狀態與測試

主要 gate codes：context_not_registered、context_inactive、registered_hash_mismatch、
registered_scope_mismatch、review_target_mismatch、source_not_registered/source_inactive、
source_version_mismatch/source_not_authorized、reviewer_not_registered/reviewer_revoked、
grant_revoked、review_not_authorized、review_expired、review_time_untrusted、
registry_hash_mismatch、policy_revision_untrusted、registry_invalid、registry_or_bundle_invalid。
Registry 無法讀取、缺必要欄位／引用或不合法 JSON 皆 fail-closed，沒有自動修補或重試。
回傳前改動通常在 pin 核對時以 registry_hash_mismatch 被阻擋；不聲稱有服務端監控。

測試前 12 案對應設計驗收表，其餘涵蓋 Registry 修改、重播與撤銷、grant 期限／撤銷、
端點、時鐘倒退、native 副本隔離、缺證據與 malformed registry。
授權失敗的主要負面測試 mock Validator／Aggregator 並確認未呼叫。
PDF 授權通過後仍由既有 Validator 回 unsupported_evidence_format / blocked。

```powershell
python -B -m unittest tests.test_nutrition_context_provider tests.test_nutrition_review -q
python -B -m unittest discover -s tests -q
python -B -m unittest discover -s experiments/recipe_parsing_gemma -p test_*.py -q
```

下一步：審查此 sidecar 欄位／授權生命週期與 bootstrap 部署方式，再決定是否納入正式 Contract。
真實人員身分認證、文件適用性、native wire 格式與完整營養資格仍未解決；不因本機 gate 通過而開始熱量計算。

本輪實際結果：Provider 25 項與既有 Review 27 項全部通過；主測試 318 項執行，
316 通過、2 項外部快照測試按既有預設跳過；Gemma 實驗離線測試 33 項通過。
沒有執行模型推論，也沒有用未完成的設計案例代替實際測試結果。

## 照片審查 profile（2026-11-19，暫定）

`src/nutrition/photo_context_provider.py` 的 `PhotoContextProvider` 沿用本文件的信任前提與生命週期，差異：
registry profile 為 `foodcal.local_photo_review_registry.v0`；grants 與 contexts 用 `photo_scope`（綁定圖片、視覺證據與份量估計 hash）
取代 `recipe_scope`；已決定的 source_use／integrity gate 紀錄也需要已登記審查者與 `['gate', <名稱>, null]` 權限；
ledger 的 policy_revision 必須等於 bootstrap revision。`review_registered(context_id, expected_photo_scope=…,
expected_candidate_scope=…, basis_target=…, energy_nutrient=…)` 授權通過後呼叫 `build_photo_nutrition_basis`，
回傳 `PhotoProviderResult`（`build` 內含 PhotoNutritionBasis 或 unavailable 原因），nutrition／production eligible 恆為 false。
測試：`tests/test_photo_context_provider.py` 6 項（合成資料）。
