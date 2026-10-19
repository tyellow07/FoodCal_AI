# Recipe Parsing：離線 Contract v1 原型

規格來源是 `docs/recipe_parsing_contract_v1.md`；五份原始設計範例位於
`docs/examples/recipe_parsing_contract_v1_cases.json`。本模組沒有修改它們。
Validator／Mock 使用標準函式庫且不依賴 Ollama；Mock 是 fixture replay，並非文字解析器。

另有顯式呼叫的 `OllamaRecipeParsingAdapter`，僅連本機 loopback Ollama；
Validator／Mock 本身仍不觸發網路或模型。以下的離線 API 可独立使用。

## 受控 Ollama Adapter

```python
from src.recipe_parsing import OllamaRecipeParsingAdapter

# source_snapshot 必須由呼叫端建立及核對，包含完整 Source 欄位與原文 hash。
adapter = OllamaRecipeParsingAdapter(model='gemma4:31b', num_predict=4096, num_ctx=4096,
    timeout=7200, stall_timeout=1800)
result = adapter.parse(caller_input_id, source=source_snapshot,
                       retrieval_binding=caller_binding)  # standalone 用 None
print(result['status'], result['review_status'])
print(adapter.last_audit_path)  # 本機私有資訊，不是契約公開欄位
```

匯入／建構不進行連線。`parse` 每次最多一筆 `/api/chat` 請求，沒有重試或批次。
沿用既有 `experiments.recipe_parsing_gemma.streaming` 的 HTTPStream 與
collect_stream：7200 秒總期限、1800 秒停滯上限（可設定，0 關閉）、60 秒進度、
串流與 thinking 保存。支持 Ctrl+C、`cancelled` 回呼及建立 last_cancel_file 中止。
關閉用戶端不能保證伺服器立刻停止運算。此階段仍依賴 checkout 中該傳輸模組；
尚未整理成可獨立安裝的套件，不修改歷史實驗程式。

預設為 temperature=0、seed=42、num_ctx=4096、num_predict=4096、
keep_alive=5m、stream=true、format=json。新版 prompt 是 ollama_prompt.md，
不是舊實驗 prompt；其 UTF-8 hash 與實際參數由程式記錄。model_digest 只接受
呼叫端獨立核對的值；未提供時為 null 並記 issue，不相信模型自報 digest。
完成串流的 model 標籤必須與請求相符；length、不完整串流、timeout 或取消均失敗。

`num_ctx` 可明確設定為正整數，預設4096保持相容；`num_predict` 是生成上限，
兩者獨立。實际 API request 與 provenance 記錄相同值，不能只根據 length 診斷原因。
`num_predict` 已支援指定正整數，預設4096保持相容；可用明確呼叫覆蓋，
例如 `OllamaRecipeParsingAdapter(num_ctx=8192, num_predict=6144)`。
這只是實驗設定入口，不表示增加預算會提升抽取正確性。

新 wire 回應只是 Contract v1 的四個候選欄位投影：ingredients、
preparation_steps、evidence、issues，各自使用原契約結構。它不是新的結果 schema；
模型不得附加 source、provenance、approval、status 或 completeness 宣告。
原文／來源／binding 由呼叫端快照提供，provenance／hash／狀態由程式建立，
並經 Validator 與模型 Trust Boundary 再驗證。來源 metadata 的真實性與授權
仍是呼叫端責任。ID 必須符合原文跨度的確定性規則，不接受任意模型 ID。

有效候選仍回傳 **partial／needs_review + coverage_unknown**，直到有獨立的
語意／完整性審查；Validator 通過不授予 parsed／approved 或真實食譜資格。
全空候選為 unavailable，不宣稱無食材；JSON／schema／證據錯誤回 failed 並清空
輸出陣列。錯誤型別、假證據或不合法的 exact 值不會被修補為成功資料。
無效呼叫端 metadata／設定則在送出前拋 ValueError 或 ContractValidationError。

每次使用新的 UUID 私有 audit 目錄（預設
experiments/recipe_parsing_gemma/results/adapter/<run_id>/），獨立保存 request.json、
stream.jsonl、progress.jsonl、result.json；不覆寫歷史檔案。包含輸入、未驗證候選、
已驗證結果、局部輸出、thinking、首片段延遲、eval_count、done_reason 與總耗時。
raw_output_sha256 指答案字串 UTF-8 hash；完整 thinking／stream 另存 audit。
預設目錄由既有 .gitignore 排除；自訂 audit_dir 須由呼叫端維持存取與 Git 隱私策略。
Audit 寫入失敗可能拋 OSError，不能將未保存的輸出當成已完成實驗。
同一 adapter 實例應循序使用，避免 last_audit_path／last_cancel_file 指示混淆。

### 舊七欄的顯式轉換

只有 `output_format='legacy_v0'` 才使用原實驗 prompt 與 convert_legacy_items；
預設不自動辨識或接收舊陣列。原文與舊七欄回覆都保留在 audit，不修改參考答案。

- 舊格式缺 quantity_raw、宣告跨度、穩定 ID、料理步驟目標及完整性資訊。
  只在 evidence_text 及其中名稱均唯一時建立 anchor；歧義項隔離、保留 warning。
- 只解析緊接名稱的數量 token 與明示 unit，核對 ASCII 數字或半是否與舊值一致。
  不支援／不一致的 token 保留為 unparsed/null，沒有 literal 的虛構值則失敗。
  不補來源中模型漏掉的值，不換單位，不猜 weight。適量／少許、缺值沿用原語意。
- 已確認可不加的 optional=true 保留；其他未能安全判定的 true 存於 audit，
  契約以 unresolved/false/uncertainty 表示（false 不代表必需）。此匯入詞彙限制
  不修改通用 Validator 的語意政策。
- 非 null preparation_method 僅在同一證據有唯一動作且可明示單項作用時轉 step。
  非逐字方法／歧義 scope 僅存 audit 並 warning；null 不表示沒有步驟。
  不從原文補 case_04 add 或 case_05 group mix，也不把三個 null 合成群組步驟。
- 保留有效項但無法安全轉換其餘資訊時為 partial/needs_review；完全無法建立
  可保存 entity 時依 v1 必須 unavailable（不能製造空 partial）。型別／偽證據
  違規整筆 failed，不能用 partial 洗掉結構錯誤。

2026-10-11 已只執行一次新版 case_01_clear_quantities 真實 Smoke Test：
883.179秒、首 thinking 39.979秒、無答案，done_reason=length；結果 failed/needs_review，
安全失敗 envelope 與獨立 Trust Boundary 通過，候選驗證未到達。
prompt_eval_count=686、eval_count=3410，合計4096；/api/ps 的 context_length=4096。
生成計數尚未達 num_predict=4096，context 容量限制是推測，不宣稱已證明因果。
私有報告位於 results/adapter/20261011T030459Z-7d7d02df5fe843b6ae6015a4e8fd9ffe/SMOKE_REPORT.md
（相對 experiments/recipe_parsing_gemma/）。不直接繼續 case_02；先確認有效 context／
生成預算，再另行決定受控實驗。此次未改參數、重跑、修改模型回應或補值。

2026-10-11 另完成一次僅將 num_ctx 提高至8192的case_01對照：1041.877秒，
首thinking 38.302秒，無答案；prompt_eval_count=686、eval_count=4096、length。
有效context=8192，runner truncated=0；本次更符合num_predict生成上限，
不是context容量耗盡。安全failed envelope／Trust Boundary通過，候選驗證未到達。
133項離線測試於前後通過；不直接繼續case_02。私有比較報告位於
results/adapter_ctx8192/20261011T033825Z-0887e7ac64e94342b08452f89a951a58/CONTEXT_COMPARISON.md
（相對 experiments/recipe_parsing_gemma/）。沒有重試、修補輸出或修改Prompt。

2026-10-11 再完成一次只提高num_predict至6144的case_01對照：context仍8192，
1464.526秒、eval_count5718、done_reason=stop，首thinking41.140秒、首答案1164.998秒。
取得合法JSON但Contract失敗：12個supports字串違反陣列型別；另有evidence ID前綴與
名稱anchor的唯讀診斷。正式結果failed/needs_review，安全envelope及獨立Trust Boundary通過。
135項測試於前後通過。未修補輸出；不再盲目增加預算，thinking控制與Contract輸出遵循
需分別受控驗證。私有報告：results/adapter_predict6144/
20261011T040506Z-bdced9189e254b0794845bf53031b922/GENERATION_COMPARISON.md。

離線違規分析及未啟用的Prompt差異草案見
`docs/recipe_parsing_case01_contract_review.md`；6項新回歸測試隔離supports型別、
Evidence前綴與名稱anchor錯誤，全部141項測試通過。正式Prompt／Validator不變。

## Python API

```python
from src.recipe_parsing import (
    MockRecipeParsingAdapter, validate_recipe_parsing, load_recipe_parsing,
    ContractValidationError,
)

adapter = MockRecipeParsingAdapter()  # 明確的 mock；只載入一次
result = adapter.parse('case_05_composite_seasoning')
assert adapter.is_mock
assert result['source']['kind'] == 'test_fixture'
assert result['provenance']['parser_kind'] == 'design_example'
assert len(result['preparation_steps'][0]['ingredient_ids']) == 3

report = validate_recipe_parsing(result)
assert report.valid
for error in report.errors:
    print(error.path, error.code, error.message)  # JSON Pointer／錯誤類型／原因

try:
    snapshot = load_recipe_parsing(result)  # 驗證後深複製；不修補資料
except ContractValidationError as error:
    print(error.report.errors)
```

`types.py` 提供 `TypedDict`（Source、Ingredient、PreparationStep、Evidence、
Provenance、RetrievalBinding、Issue、RecipeParsingResult 等）及不可變的
ValidationError／ValidationReport。型別註記不代替 runtime validator。

Validator 接收已解碼的 Python JSON 資料，回傳 `ValidationReport`。必要 key、
未知 key、型別與 enum 錯誤先列出；有結構錯誤時不進入依賴欄位的語意檢查。
原始物件不會被修改；錯誤碼包含 `missing_field`、`extra_field`、`type`、
`span`、`evidence`、`id`、`duplicate_id`、`reference`、`normalization`、`status` 等。
這些驗證錯誤不會偷偷改成契約內的成功／partial 結果。
契約內 `issues[].path` 除了 JSON Pointer 語法，也必須指向結果中既存值；
根路徑 `""` 與 `~0`／`~1` 跳脫都支援，錯誤回報 `issue_path` 定位該 issue。
驗證器自身的 missing_field 路徑則可以指向尚不存在的必要欄位，兩者用途不同。

### 模型輸出必須使用獨立的信任入口

```python
from src.recipe_parsing import load_model_recipe_parsing

# 以下資料由呼叫端從來源快照、實際請求與 Retrieval 候選建立，
# 不得從 model_payload 本身複製成「可信」資料。
validated = load_model_recipe_parsing(
    model_payload,
    expected_input_id=caller_input_id,
    expected_source=caller_source_snapshot,
    expected_provenance=system_model_provenance,
    expected_binding=caller_binding,  # standalone 為 None
)
```

`validate_model_recipe_parsing` 回傳同型別 ValidationReport；load 版本在錯誤時
拋 ContractValidationError，成功時深複製。兩者均先執行完整 Contract Validator，
再比較 input_id、全部 Source 欄位、全部 Provenance 欄位與 Binding。呼叫端的
provenance 必須標示 model；模型不能藉自稱 manual／design_example 逃避檢查。
實際模型／prompt／raw output hash／settings 要由系統 audit 計算或記錄。

模型自稱解析結果 approved 回報 `untrusted_approval`；來源、provenance、binding
及 input_id 不一致分別回報 `source_mismatch`、`provenance_mismatch`、
`binding_mismatch`、`input_identity_mismatch`，且保留定位路徑與原因。
不會把偽造欄位悄悄改回可信值再稱成功，也不改動原件。

呼叫端提供的 production/approved 來源可以被保留；它不提升模型結果的
review_status。一般 validate／load 仍可處理已人工核可的歷史紀錄，**其 valid
只表示契約檢查通過，不能作為接受原始模型核可宣告的入口**。
expected context 是程式信任邊界，不是身分認證機制；若呼叫端也把模型的宣告
當成 expected context，比較仍無法保護資料。此原型沒有 Ollama Adapter。

Mock 建構時驗證全部案例；拒絕重複 JSON key／input_id、無效 JSON、非有限數字、
錯誤契約、非 test_fixture/test_only、已核可結果、非 design_example 或帶 binding
的資料，以 `FixtureLoadError` 回報。`parse` 只接受完整 input_id；無效參數為
ValueError，未知 ID 回傳 `unavailable` 與 `coverage_unknown`，不宣稱沒有食材。
每次回傳独立副本，保留原 fixture 身分與內容，不假冒 Gemma 輸出。

`diagnostic_result(input_id, status='failed', code='invalid_json', message=...)`
可建立空陣列／error 的 mock 失敗紀錄；也支援 unavailable。呼叫端應將壞原件
隔離至外部 audit，不能靠清空欄位掩飾失敗。此 helper 不執行 JSON 解碼或推論。

## 狀態與驗證範圍

- 正常狀態名稱是 `parsed`，不是 `success`；不接受別名。
- partial 必須保留食材或步驟、有 warning、沒有 error，且 needs_review。
- unavailable／failed 的食材、步驟、證據陣列必須全空並保留原因；failed 需 error。
- 未知數量保留 null；半顆保持 0.5／顆；不補重量、單位或營養。
- Unicode code point、end exclusive 跨度、原文 UTF-8 hash、跨度 ID、角色證據、
  食材參照、v1 動作白名單與 anchor 展示順序均檢查。
- schema 合法與 substring／scope 涵蓋不證明語意正確、完整抽取或來源真實。
  Validator 不授予 approved；接收人工聲明時仍需呼叫端的授權／稽核流程。

三项語意邊界：

| 範圍 | v1 表示及限制 |
| --- | --- |
| optional | 可不加的原文證據對應 explicit_optional/true；not_marked/false 不表示必需。explicit_required 需另有明示證據；unresolved/false 要 uncertainty、warning、partial/needs_review。角色證據的存在不證明可選標記的作用範圍。 |
| 模糊數量 | 適量／少許保存 quantity_raw、quantity=null、rule=null、vague、uncertainty=true 與說明；unit 可保存原量詞但不可換算。沒有明示數量才是 missing，不能把適量填成已知 0。 |
| unknown | unknown 不是新的 enum。缺失為 missing，存在但不支援為 unparsed/null＋warning/partial，無可保存抽取為 unavailable；無法確定動作或目標使用現有 null／unresolved 狀態。這些都不表示原文沒有食材或錯誤。 |

## 歧義與原型政策（未改動 Contract v1）

1. 規格沒有列出 numeric_literal 完整白名單。暫採明示的保守政策
   `[0-9]+(?:\.[0-9]+)?`，用 Decimal 與 quantity 比對；不接受符號、科學記號、
   分數／中文數字，半用獨立 half_literal。使用者已核可此原型暫定限制；
   未將它寫入或改動 Contract v1／五案例範例。
   `validate_recipe_parsing`、`load_recipe_parsing`、Mock 建構器都可注入
   `numeric_parser(raw) -> Decimal`，不擴大動作或 JSON schema。
   不支援語法拋出 `UnsupportedQuantityFormat`（亦為 ValueError），Validator
   回報 `unsupported_quantity_format`，定位 quantity_raw；這代表能力限制，
   不判定原文內容錯誤。自訂 parser 也可用此例外表達自己的能力限制。
   不能把未解析的字串宣告為 exact；應保留原文 quantity_raw／證據，改以
   quantity_state=unparsed、quantity=null、quantity_rule=null、uncertainty=true、
   warning／partial／needs_review 表示，這種完整表示會通過驗證。
   Validator 不會自動做上述轉換或補值。型別錯誤仍回報 type，支援語法的數值
   不一致則回報 normalization；兩者與 unsupported_quantity_format 分開。
2. explicit_optional／explicit_required 的語意詞彙與 vague 量詞不是封閉白名單。
   原型檢查對應狀態、旗標及逐字角色證據；不能證明標記的實際語意／作用範圍。
3. issues 與不確定欄位的精確對應未細定；接受欄位、所屬 entity 或祖先／根路徑
   的說明，issues code 是開放集合。仍需人工確認原因是否充分。
4. model 的「實際 settings」、人工 approved 與外部 catalog 身分不能由 JSON
   自證。模型入口另比對呼叫端 context；仍不驗證來源授權或 catalog 內容。
   Source reference 拒絕絕對／越界路徑、URL userinfo 及常見 credential query key；
   不是全面隱私掃描器，公開資料仍需另外審查。

待決議提案（本次未修改 Contract）：建立可選／必需與模糊量詞的語意詞彙及
作用範圍仲裁規則；定義 issue code 與原因充分性的細緻對照；建立人工核可、
caller context 與來源／catalog audit 的信任政策。先保留獨立人工語意審查，
不把字串涵蓋通過當作正確歸屬、完整抽取或真實食譜可靠性。

沒有可靠原文時不把 Retrieval 的 TEST_ONLY ingredient token 當作食譜。
現有 Retrieval API 不變；日後呼叫端提供原文快照，從候選複製 source_id、recipe_id
及 catalog_sha256 建立 binding。每個物件／候選的关联由外部保存，不將 confidence、
retrieval_score 轉成解析機率，不把新結構寫回原字串列表。

## 驗證

```text
python -B -m unittest tests.test_recipe_parsing -v
python -B -m unittest tests.test_ollama_recipe_parsing -v
python -B -m unittest discover -s tests -t . -p "test*.py"
```

五個設計範例均通過；新增測試涵蓋正向、型別與空值、ID／角色／參照、錯誤跨度、
emoji／CRLF、重複宣告、群組／單項步驟、未知動作／數量與狀態、fixture 邊界等。
合成範例通過不能當作真實食譜可靠性證據；尚無已驗證的正式食譜來源。

Prompt v2 單一變因結果見 [case_01 對照報告](../../docs/recipe_parsing_case01_prompt_v2.md)。
舊 Prompt 原檔保留於 `prompt_versions/ollama_prompt_v1.md`。
指定的三類違規已消失，但模型把 optional / uncertainty 輸出為 null，
因此 Adapter 仍安全回傳 failed／needs_review，沒有輸出未驗證食材。
上述 Prompt v2 對照當時只執行一筆，尚未接入 JSON Schema；case_02 未執行。

### 可選 JSON Schema 約束

`OllamaRecipeParsingAdapter(json_schema=True)` 將四欄候選的 JSON Schema
送入 Ollama `format`；預設 `False` 保留原本 `format="json"` 行為。
只支援 `candidate_v1`，不將來源、provenance 或核可權限交給模型。

```python
adapter = OllamaRecipeParsingAdapter(
    model='gemma4:31b', model_digest=verified_digest,
    json_schema=True, num_ctx=8192, num_predict=6144,
    timeout=7200, stall_timeout=1800, audit_dir=private_audit_dir,
)
result = adapter.parse(input_id, source=trusted_source_snapshot)
```

Schema 與純離線結構檢查位於 `schema.py`，使用標準函式庫。
`candidate_json_schema()` 回傳獨立副本；`validate_candidate_schema(value)`
僅驗證本原型的結構子集，並非通用 JSON Schema 引擎。
audit 與 provenance 保存實際 Schema，`schema_sha256` 使用 UTF-8、排序鍵、
緊湊 JSON 計算，不加入 default、不 coercion，也不把 null 旗標改成 false。
quantity、unit 等合法缺失值仍可為 null；optional 與 uncertainty 必須是 bool。
false／not_marked 不代表必須加入，來源核可仍由呼叫端提供。

Schema 只處理型別、枚舉、必要欄位與封閉物件等結構規則。
非負數量、ID 與跨度對應、角色唯一性、原文證據、參照完整性、
正規化、語意與來源信任仍由現有 Validator／Trust Boundary 及人工審核處理。
Schema 通過不表示模型解析完整或來源已核可。

唯一一次 Schema case_01 對照已通過 JSON／Schema／Contract／Trust Boundary，
仍為 partial／needs_review，來源是 test_only，沒有獨立完整性核可。
完整參數、雜湊、限制與前後比較見
[Schema 對照報告](../../docs/recipe_parsing_case01_schema_comparison.md)。
153 項離線測試通過，case_02 尚未執行。

## Generation budget observability

The Ollama adapter adds content-free advisory `budget_diagnostics` to new private
result audits. Disable with `budget_diagnostics=False`; model requests and Contract
results are unchanged. Client num_predict is not independently confirmed as a
server-adopted aggregate cap. Missing server options and separate thinking/answer
token counts stay null. See [metric definitions and case_04 evidence](../../docs/recipe_parsing_generation_budget.md).
