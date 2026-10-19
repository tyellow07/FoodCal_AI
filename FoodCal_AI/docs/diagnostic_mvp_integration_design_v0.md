# Image → Food → Recipe → Nutrition Diagnostic MVP v0

日期：2026-10-12；盤點基準 HEAD 9aeaed5af425f638b17b46e13c7a58c21cadd47c。
開始時工作目錄乾淨。本輪僅設計，沒有實作新的 pipeline、執行模型或改動既有 Contract。
目標是展示「哪些候選可追溯、哪些資訊仍缺失」，不是展示正式熱量估算。

## 1. 現有介面與相容性

| 模組／實際 API | 輸入 → 輸出 | 整合判斷 |
| --- | --- | --- |
| src.detection.detect_food(image)／FoodDetector.detect_food | local path、PIL 或 uint8 BGR array → schema 1 dict，含 image/model/settings、detections | 可直接餵給 RecipeRetriever；每框保留 detection_id、class_id/name、confidence、原圖 xyxy 與 model_identifier |
| src.recipes.RecipeRetriever(catalog_path, include_test_fixtures, classes_path).retrieve(result, limit=5) | Detection schema 1 → 每 detection 的候選食譜、catalog hash、status/reason | 已支援多框與 repeated objects；預設無正式 catalog，回 unavailable；建構時仍必須提供 15 類 YAML |
| MockRecipeParsingAdapter.parse(input_id) | 五個固定完整 case ID → Recipe Parsing v1 dict | 可接 Mapping，但不是文字 parser；只接受未綁 Retrieval 的 unreviewed design fixtures，不能直接餵 Retrieval 候選 |
| OllamaRecipeParsingAdapter.parse(input_id, source=..., retrieval_binding=...) | 呼叫端可信 Source 原文快照與 binding → Contract v1 結果 | 真實文字解析入口；Adapter 保護來源／provenance，合法模型候選仍 partial/needs_review；本輪不呼叫 |
| MockNutritionMappingAdapter.map(parsed, expected_source=..., identity_assertions=...) | 驗證後 Parsing snapshot → 逐 ingredient 的 mapping、declared mass、qualification | 可直接接完整 Parsing；expected_source 必須獨立取得；名稱吻合不自動核可身分，catalog 僅 synthetic、nutrients=null |
| TFDANutritionSourceAdapter(snapshot_directory).lookup(food_id) | 固定八筆官方快照的精確 ID → SourceLookup dataclass | 需本機私人快照；不能替換 Mock Mapping；保留原值／Decimal／單位／逐列 evidence，基準 unresolved、production_eligible=false |
| review_ingredient_sources(parsed, expected_source, mock_mapper, source_adapter, identity_assertions, source_links) | Parsing scope＋明示食品 ID/archive/sample hashes → CandidateLookupResult | 已組合 Mapping 與 Source；source found/link verified 與身分核可分離，identity 仍 pending/ambiguous |
| Review／Provider／Bootstrap | 登記人工紀錄、受控 Context／Registry／高水位 → 診斷核對結果 | 本 MVP 非必要依賴；預設 not_requested，不能虛構 Registry 或為展示擴張核可流程 |
| FoodRecognizer.recognize_food(image_path) | 整張圖 → (Food-101 top-5 food/confidence list, inference_time) | 與 YOLO 15 類不同 taxonomy，沒有 boxes/detection_id；不能直接送 Retrieval schema 1 |

Detection／Retrieval 的資料結構直接在 model.py／retrieval.py 定義，沒有獨立 types.py。
檢查實際欄位而非假設模組名稱相容：Retrieval 只有 optional ingredients/cooking_methods 字串清單，
沒有 Parsing 所需的 text、text_sha256、ingredient anchors 或 evidence spans。
不可把字串清單拼接成食譜原文，亦不可從 recipe_id 推測來源內容。

### 必要的最小連接層

1. **食譜文字 resolver／fixture 關聯清單**：以 source_id、recipe_id、catalog_sha256 找到原文
   Source snapshot；核對來源 reference、text hash 與測試資格。缺內容即 unavailable，不生成食譜。
2. **Parsing 呼叫選擇**：離線 fixture 按明示 input_id 重播；真實模式才明確啟用 Ollama。
   Mock 結果 retrieval_binding=null 保持原樣；wrapper 只能另存 fixture association，不能冒充正式 Contract binding。
3. **逐食材 Source links**：獨立、明示 ingredient_id/input_id/text hash 與 TFDA ID/pins；
   缺連結保留 missing_source_link，不做 fuzzy matching 或將食物類別直接映射成食品 ID。
4. **診斷 serializer**：dict 與 frozen Source dataclasses 的展示投影；Decimal 轉精確十進位字串，
   保留 raw_value（含前置空白）、單位、status/reason、全部來源 hashes，不转 float 或補缺值。

Model輸出的 Source/provenance/approved 不可覆寫 resolver 的呼叫端快照。
fixture source 從獨立已核對檔案讀取；不要單純複製 model result.source 來充當 trusted expected_source。

## 2. 三種執行方式與前置條件

| 模式 | 會執行的項目 | 需要的外部資源 | 展示限制 |
| --- | --- | --- | --- |
| fixture_offline（優先） | 合成 Detection schema 1 → 真實 Retrieval／Mock Parsing／Mock Mapping 呼叫 | 既有 Python 依賴、可追蹤小 fixtures；不需要 weights/Ollama/TFDA 原件 | 明確 synthetic replay，不聲稱從圖片辨識；TFDA 未提供時 not_queried |
| yolo_local + fixture parsing | 真正圖片推論 → Retrieval；有明示文字關聯才重播 mock | best.pt、15 類 YAML、圖片；torch/ultralytics、現有影像依賴 | 食譜／Parsing 仍 test_only；YOLO 見到的不是確認食材或份量 |
| yolo_local + ollama_local | YOLO → 可追溯原文 resolver → Gemma → Mapping／可選 TFDA 查詢 | 前項加本地 Ollama、已核對 gemma4:31b tag/digest、足夠資源、完整原文 | 受控推論有較高延遲；正式食譜来源目前缺失，預設不得啟動 |

ViT/Food-101 為另一條整張圖分類模式，非本 MVP 預設；若日後接入需獨立 taxonomy adapter 與
classification scope，不能為滿足 Detection validator 虛構整圖 box 或當成多物件結果。
先採 YOLO 介面，避免同時載入兩套模型或擴張類別融合。

YOLO 原樣參數 conf=0.25、iou=0.7、imgsz=640 等 BASELINE_SETTINGS，不在 demo 調參。
default weights：runs/detect/taiwanese_food_baseline_10ep/weights/best.pt；
default taxonomy：data/roboflow_taiwanese_food_detection/foodcal_data.yaml。
ViT 本機目錄 models/nateraw-food，FoodRecognizer 使用 local_files_only，但初始化會載入模型。

Ollama Adapter 的程式預設 num_ctx=4096、num_predict=4096、json_schema=false，
**不能把成功實驗的設定誤稱為預設**。未來受控模式需顯式指定 num_ctx=8192、num_predict=6144、
json_schema=True、既有 Prompt/Schema hash、temperature=0、seed=42、timeout=7200、stall_timeout=1800。
保留取消、串流、私有 audit；num_predict 不聲稱是跨 backend 階段的整筆硬上限。
本輪不核驗當下服務資源或模型 availability，日後首次真實呼叫前再確認。

官方 TFDA 分支僅允許既有八筆 pinned snapshot；FOODCAL_TFDA_SNAPSHOT 是 optional 測試前置條件。
沒有本機原件時不能從 sample_summary 重建營養值；摘要只有 metadata／計數，不是營養資料庫。
預設離線展示只呈現 synthetic Mapping 候選（nutrients=null），不把它們稱為官方食品。
真實 TFDA 查詢另外 opt-in；圖像／食譜來源及官方資料仍未達正式使用資格。

## 3. 可展示的最小資料流程

```text
合成影像／image reference + 明標 Detection fixture（或顯式啟用 YOLO）
  → Detection schema 1，保留所有框與 confidence
  → RecipeRetriever，保留每框全部候選及 independent retrieval_score
  → 明示 recipe/source resolver 或 fixture association
  → Mock Parsing fixture（或日後顯式 Ollama），原 Contract 不修改
  → Mock Mapping：mapping / declared mass / qualification 分開
  → 有明示 source link 與外部 snapshot 才跑既有 Candidate Lookup
  → 一份 JSON + CLI 摘要：候選／證據／缺失／不可估算原因
```

第一版不用 UI、HTTP service 或通用 pipeline 框架。只需薄協調函式與 fixture demo CLI。
以 per-detection／per-recipe scope 組織結果；不同 recipe 的 ingredient_id 可能相同，
不可全域只以 ingredient_id 作 key。引用須含 detection_id、recipe_id、input_id、text_sha256。
可按同一來源快照快取解析，但每個引用保留其原始辨識物件；不合併重疊框、不得因此累加食用份量。

## 4. 最小輸出 envelope（提案，不是正式 Contract）

| 欄位 | 用途 |
| --- | --- |
| diagnostic_version、mode、usage | 協調層版本及 synthetic/live 模式，永遠 test_only |
| stages | 每階段 executed/replayed/skipped/unavailable/failed 與 machine-readable reason |
| detection_snapshot、retrieval_snapshot | 完整既有結果保留；展示 projection 另存，不改回傳物件 |
| objects[].detection_id／recipe_candidates[] | 多物件及多候選，不自動取 top-1 當真實食譜 |
| recipe_candidates[].association | resolver binding 或 fixture_association_only；catalog／source／text hashes |
| parsing_snapshot、mapping_snapshot | 完整 Contract snapshot，含 partial/review_status/provenance/issues/evidence/steps |
| source_diagnostic | Candidate Lookup 或明確 not_queried 與原因；查得 Source 附精確 Decimal 序列化 |
| review | not_requested；日後顯式輸入受控 review 時另保留結果，不自動核可 |
| estimate | unavailable、photo_consumed_mass_g=null、calories=null、nutrition_eligible=false、reason codes |

以下是 envelope 的局部示意，省略完整 snapshots，不是實際執行結果，也不是 Contract v1 文件：

```json
{
  "diagnostic_version": 1,
  "mode": "fixture_offline",
  "usage": "diagnostic/test_only",
  "stages": {
    "detection": {"status": "replayed", "reason": "synthetic_fixture_not_image_inference"},
    "retrieval": {"status": "executed", "reason": null},
    "parsing": {"status": "replayed", "reason": "explicit_fixture_association"},
    "mapping": {"status": "executed", "reason": null},
    "tfda": {"status": "skipped", "reason": "external_snapshot_not_supplied"}
  },
  "objects": [{
    "detection_id": 1,
    "recipe_candidates": [{
      "recipe_id": "synthetic-chain-001",
      "association": {"status": "fixture_association_only", "contract_retrieval_binding": null},
      "parsing_ref": "parsing_snapshots/0",
      "mapping_ref": "mapping_snapshots/0",
      "source_diagnostic": {"status": "not_queried", "reason": "missing_source_link"}
    }]
  }],
  "review": {"status": "not_requested"},
  "estimate": {
    "status": "unavailable",
    "photo_consumed_mass_g": null,
    "calories": null,
    "nutrition_eligible": false,
    "reasons": ["fixture_only", "recipe_is_prior", "photo_mass_unknown", "nutrition_basis_unresolved"]
  }
}
```

實作需填入本例省略的完整 parsing_snapshots／mapping_snapshots、Detection／Retrieval，
並驗證各 ref 能解析；不能把示意中的 ref 當已存在資料。
Mock Parsing 的 parsed/unreviewed 與 Ollama 的 partial/needs_review 原樣保留，
不能為統一展示改成 approved，亦不能因 Mock Mapping complete 而升級 Parsing。
Source found、identity pending、basis unresolved、mass available、eligibility=false 是獨立欄位。

declared mass 可為 200g，但標籤必須是「食譜宣告食材量」，不在 estimate 中當照片食用重量。
quantity=null／適量／少許／半顆／匙／ml 原樣保存；無證據的單位換算不產生 g。
所有不可估算情況用 null + reason，不顯示 0 kcal、不對候選熱量加權，也不乘模型 confidence。
候選框可能涵蓋料理或其中成分，不能直接按框數當餐份數。

## 5. 下一個最小、乾淨 checkout 可重現的實作任務

建議範圍：一個獨立 diagnostic_demo.py、必要的小 fixtures、一份離線整合測試；
不改舊 mvp_pipeline.py，不修改既有 API／Contract 或本輪模型設定。

### 預設 fixture_offline

1. 新增小型 Detection schema 1 fixture、合成影像／image reference 及 taxonomy fixture。
   15 類順序需核對權威 YAML 並記錄 hash／來源；只包含類別清單，不複製私人 train/valid/test 路徑。
   Retrieval 顯式传 classes_path，避免乾淨 checkout 的 ignored data/ 依賴。
2. 不改原 recipe_catalog.json 的測試內容。新建明標 SYNTHETIC CHAIN TEST 的 catalog 與關聯清單，
   候選來源 metadata 與選定既有 Parsing design fixture 的 Source 完整一致。
   synthetic 食物類別關聯只測協定，不宣稱該料理的真實食譜或圖片辨識語意正確。
3. 明示候選 tuple → 完整 case input_id、source/ref/text hash。Mock 重播後核對 source，
   保留 retrieval_binding=null；envelope 明標 fixture_association_only，不能重寫 Mock 結果冒充正式串接。
4. 呼叫既有 Mock Mapping，預設身分未知；測試才提供 synthetic scope/assertions 展示 matched／ambiguous。
   不需要 Review、Registry 或 Bootstrap 核可才能展示缺失原因。
5. TFDA 預設 not_queried；不要在執行程式 patch 官方 pins 冒充真實 Source。
   若測試需驗證 Candidate 路徑，可注入明標 synthetic SourceLookup test double，不宣稱正式 TFDA Adapter。
6. 輸出 deterministic JSON 與短 CLI 摘要；預設 stdout，不寫入歷史 evidence。
   展示輸出避免公開 machine-specific image/model/catalog 絕對路徑；原 API snapshots 私有保存，
   可分享 projection 明示去識別化方法，不改原 source hashes 或 API 回傳物件。

### 最小驗收

- 單物件有候選、兩物件有部分候選、重複／重疊框保持獨立。
- 無偵測、無 catalog、候選無原文／關聯、未知 case ID 各有獨立 reason。
- catalog/source/text hash 或 fixture association 不一致 fail-closed；不掛錯食譜／食材。
- Parsing evidence spans／ingredient IDs／step refs 合法；群組步驟保留多項 ingredient_ids。
- 缺量、模糊量、半顆 optional、明示 g 不產生照片重量或熱量。
- Mock Mapping matched 仍 test_only；Source found 仍不核可；TFDA 未配置可正常回報 not_queried。
- Decimal／raw padded 值／null／0／unparsed 的 serializer 不丟資料或轉成浮點誤差。
- fixture mode 不載入 YOLO/ViT、不呼叫 Ollama、不讀 ignored data/runs、不產生私人 evidence 寫入。

下一輪先完成這個離線 JSON/CLI 展示與合成整合測試。
之後才另行批准一次 YOLO validation 圖片 smoke test，再規劃真正來源原文／Ollama 接入；
沒有正式食譜来源時真實模式仍 unavailable，不能以合成例子的成功率當實際料理可靠度。

## 6. 舊流程與本輪驗證限制

舊 Food-101 mvp_pipeline 使用 IngredientMapper、PortionEstimator、NutritionDatabase，
與新版來源／Evidence 契約不同；其既有 top_//food、先驗重量／倍率與來源問題另存，不在本 MVP 重用或修復。
不從該舊流程取得展示熱量，也不把 classification Top-1／YOLO mAP 當熱量準確率。

本輪只閱讀程式、文件與 fixture metadata，確認工作目錄／HEAD；未執行 Detection、ViT、Ollama 或 TFDA 讀取。
本文件的輸出為設計示意，新增驗收案例尚未實作；不援引前次測試數字聲稱本次整合已通過。
唯一新增檔案為本文件；沒有修改權重、資料、原契約、歷史證據，沒有暫存、Commit 或 Push。
