# Diagnostic Fixture Demo v0

這是 **SYNTHETIC FIXTURE ONLY** 的 JSON／CLI 展示，不是圖片辨識、食譜推論或營養核可。
程式：src/pipeline/diagnostic_demo.py。既有 Contract／API、舊 MVP Pipeline、模型與證據不變。

在專案根目錄執行（既有 Python 依賴需可用，包含 PyYAML；沒有安裝新套件）：

```powershell
python -B -m src.pipeline.diagnostic_demo
python -B -m unittest tests.test_diagnostic_demo -q
```

stdout 是 UTF-8 machine-readable JSON；stderr 只有短摘要，例如：

```text
SYNTHETIC FIXTURE ONLY | partial | objects=2 parsed=5 | calories unavailable
```

正常診斷／partial／no_detections exit code 為 0，整包 fixture 無法安全載入為 2。
個別候選失敗仍保留其他物件與 machine-readable reason；不以 exit code 0 表示語意正確或正式可用。
預設不寫檔、不產生私人 audit，也不讀取環境變數的 TFDA 路徑。

## 實際串接

1. detection.json：兩個合成框，model.identifier=synthetic-detection-v0、image source=test://synthetic-image-v0。
   沒有真的圖片推論；第二個物件沒有對應 catalog，示範部分可用。
2. RecipeRetriever 使用 classes.yaml 與 catalog.json，顯式 include_test_fixtures=True。
   Detection／Retrieval schema 與 confidence／retrieval_score 原樣保留。
3. associations.json 固定 recipe_id/source_id/catalog hash → 完整 Parsing case ID、來源快照與 file pin。
   不按料理名稱猜測、不將 Retrieval 的 ingredient 字串拼成食譜。
4. MockRecipeParsingAdapter 重播既有五個 design cases；MockNutritionMappingAdapter 接收完整已驗證快照。
   沒有修改既有解析結果的 retrieval_binding=null；envelope 的 fixture_association_only 另行標示關聯。
5. TFDA／Candidate Lookup 預設不執行：每食材回 blocked＋missing_identity_assertion 等原因。
   source_diagnostic.lookup=null 表示沒有查询，不是營養值为0。

catalog 中五個候選均為 **合成協定關聯**，刻意共用 cold_noodle 類別以測多候選，
不代表它們是冷麵食譜或真實照片中的食材。原 Parsing 原文來自既有合成案例，不是正式食譜來源。
classes.yaml 的 15 類順序核對既有權威 YAML，保存來源 reference/hash；只包含 taxonomy，沒有資料集路徑。
新增 asset 的 .gitattributes 保留 JSON/YAML bytes；既有 Parsing design JSON 的 file pin 使用
LF-normalized UTF-8 bytes SHA-256，以免乾淨 Windows checkout 的 CRLF 改變 pin。
這項正規化只用於 design **檔案** pin；食譜 source.text_sha256、證據 spans 與原始營養值不修改。

## 輸出與信任

run_demo() 回傳 diagnostic_version=1、mode=fixture_offline、完整 Detection／Retrieval projection、
逐物件與候選 association、Parsing/Mapping snapshots、source_diagnostic、stages、review/trust 狀態。
使用 nested snapshots 避免跨 recipe 的 ingredient_id 衝突；重複／重疊框不合併、不當作多份餐。
catalog 的機器絕對路徑在公開 projection 換為 fixture URI，hash 不變、原 Adapter 結果不回寫。

calorie_estimate.status 固定 unavailable、value=null；photo_portion_grams 固定 null。
營養資格固定 false；Mapping 中的 200g 等只是食譜宣告量，不當照片食用重量。
Mapping ambiguous／unavailable、quantity=null、半顆、optional、群組 preparation_steps 原樣保存。
有同形合法 partial/needs_review 輸入時也不升級；既有 Mock fixtures 本身仍 parsed/unreviewed。
原文合法、流程可執行不代表食材语意真实、来源核准或營養可靠。

Python 呼叫範例：

```python
from src.pipeline.diagnostic_demo import run_demo
result = run_demo()
assert result['mode'] == 'fixture_offline'
assert result['calorie_estimate']['status'] == 'unavailable'
assert result['photo_portion_grams'] is None
assert result['trust']['nutrition_eligible'] is False
```

API 可注入由可信呼叫端建立的 source_adapter，CLI 沒有外部 Source／模型開關。
只有逐食材完整身分斷言、原文 scope/hash、食品 ID、來源 archive/sample hash 與 evidence reference
具備時才呼叫既有 Candidate Lookup；缺少／無效前置資訊阻擋查詢。
即使 Source found，identity 仍 pending/ambiguous、basis unresolved、eligibility=false。
有合法格式但錯誤 snapshot hash 時，既有 Candidate Lookup 不保留為已核驗 Source。

實驗 serializer 對 Source dataclass/tuple 作結構化投影，Decimal 保存精確字串、raw_value 的空白／null／0
及 unparsed 狀態；不把合成 Source test double 當官方 Adapter。未 patch 正式 TFDA pins。

## 下一步：最小 YOLO 接入條件

另案增加明示 opt-in 的 live Detection 入口，保持現有 FoodDetector 載入／baseline 參數：
需要本機 best.pt、15 類 YAML、經核准 validation 圖片、影像依賴及模型 class map 一致。
輸出要區分真實 Detection 與 fixture Retrieval／Parsing，不能把 fixture parser 說成真實模型推論。
本 CLI 現在只接受合成 model/image 識別，因此未提供這個功能。
正式食譜原文仍缺失；没有 resolver 對應時應 unavailable，不臨時呼叫 LLM 編造。
不接舊 PortionEstimator／NutritionDatabase／MVP 的熱量輸出。

17 項離線測試涵蓋正常／多物件、缺來源與 hash、未知 case、optional／半顆／缺量／群組步驟、
來源 scope、不可升級信任、Decimal、CLI、fixture 不可變與換行可重現性。
這些測試是資料契約整合驗證，不是食物辨識或熱量估算的準確率。

本輪實際結果：355 項主測試執行，353 通過、2 項外部快照測試依既有預設跳過；
33 項實驗離線測試通過。HEAD archive 加本輪新檔的隔離副本（無 data/runs、同一 Python 依賴環境）
17 項新測試與預設 CLI 都通過。没有執行真實模型、下載資料、暫存、Commit 或 Push。
