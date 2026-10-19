# FoodCal AI — team review snapshot

此副本供團隊審查與共同開發，沒有原專案 Git history、資料集、模型權重、真人圖片、人工標註、TFDA 原件或模型原始輸出。這不是正式營養估算產品，也不是已完成開源授權清理的發行版。

## 可展示的功能

- Diagnostic CLI：synthetic/test_only Detection → Retrieval → Parsing／Nutrition 診斷。
- Streamlit 本機 UI：預設合成示範；真實 YOLO 必須明確啟動並另行取得核准的本機權重與 taxonomy。
- Recipe Parsing：Contract、Validator、Mock、受控 Ollama Adapter；預設示範不呼叫模型。
- Nutrition：離線 Mapping、Source／Candidate Lookup、Review 原型；所有正式資格仍受阻，沒有真實熱量計算。
- Visual Ingredient：料理標籤不展開成食材；合成食材證據與食譜推測分開；人工標註工具程式可審查，但私人 Pilot 不在此副本。

所有示範保留 partial/needs_review/test_only；photo_portion_grams=null、calorie_estimate=unavailable。來源 metadata 通過檢查不等於來源已認證。

## Python 3.12 / Windows

先在 Clone 根目錄執行 `cd FoodCal_AI`，再從此 README 所在目錄執行，使用獨立環境，不更動團隊其他專案的依賴：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B -m src.pipeline.diagnostic_demo
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

使用 UI 另安裝選用依賴：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt
.\.venv\Scripts\python.exe -m streamlit run src/ui/app.py
```

開啟 http://127.0.0.1:8501。預設 Fixture 不載入模型。未安裝 Streamlit 時，四項選用 UI 測試會 skip。GUI 標註工具需要 Python 的 Tk/Tcl 與 Pillow；私人工作集必須由權利允許的來源另行建立。

requirements 版本來自原本可執行環境；本輪在隔離副本驗證程式及371項測試，沒有從空白環境重新下載依賴，也沒有建立完整遞移依賴 lock。未來跨平台安裝仍須團隊 CI 驗證。

## 真實推論與外部資源

沒有權重時真實 YOLO 會 blocked，不下載或以別的模型假冒。可檢視 src/detection/README.md；原預設位置為 runs/detect/taiwanese_food_baseline_10ep/weights/best.pt 與 data/roboflow_taiwanese_food_detection/foodcal_data.yaml。資料/權重僅能由有權使用者放在本機 ignored 目錄；示範 taxonomy 在 examples/diagnostic_v0/classes.yaml，不能當成圖片或權重使用授權。

requirements-model.txt 僅為選用版本提示；先審查 Ultralytics／模型及來源資料的使用條件，再決定是否安裝。此副本未執行 YOLO、ViT、Gemma、訓練或資料下載。沒有正式食譜來源時，synthetic catalog 只是測試先驗，不代表照片真實食材。

TFDA Source 與私人 Pilot 的原始快照缺席是刻意設計，相關本機流程須補齊各自核准的外部前置資料；不要用空檔案假裝取得資料。v1.1 真實逐樣本人工審查 metadata 也沒有納入。

## 測試範圍與副本差異

此套件不包含原本依賴真實保存輸出／人工標註／私有快照的五個測試模組：test_adapter_final_evaluation、test_visual_sample_review、test_recipe_retrieval、test_tfda_source_local_snapshot、test_nutrition_candidate_lookup_local_snapshot；也不含 tests/evaluation 歷史整合測試。371 passed 不是原專案全套測試數、不是模型準確率。

唯一原測試程式調整是 tests/test_food_detection.py：將 taxonomy 指向已追蹤的合成15類 YAML，仍使用原 FakeTensor／backend 驗證，不修改正式 Detection API 或權重。src/detection/export_validation_review.py 作為 yolo_diagnostic 的 read_gt 程式依賴保留，沒有帶入任何真實GT內容。

本輪新增README、依賴及.gitignore；其他選入來源檔案依檔案清單核對原始 SHA-256。忽略規則涵蓋 data/runs/work、圖片、模型與秘密檔案，但不是完整DLP保證。

## 團隊 Repository 整合方式

本目錄由已審查的105檔分享快照匯入團隊Repository的FoodCal_AI/，使用feature/add-foodcal-ai分支及Pull Request審查。保留團隊main與其既有歷史，不帶入FoodCal原專案的Git歷史；原專案和原分享副本未修改。

所有指令請先cd FoodCal_AI；專案以本目錄為資源根目錄，請勿從Repository根目錄直接執行模組。根目錄README保留組員原內容。

新增檔案預設被本目錄.gitignore忽略；新增可分享程式時需先審查，再將精確相對路徑加入白名單。不要用git add -f加入私人資料。

SHARING_REPORT.md保留分享快照當時的審查記錄；其中無remote、空庫前置條件與初始main的敘述屬於歷史階段，不適用本次從既有main建立團隊feature分支的流程。

未附資料集、模型權重、第三方套件本體或私人標註。本副本未新增開源License；私人團隊分享不代表授予公開散布、資料訓練或正式營養使用資格。選用Ultralytics與外部模型/資料仍需另行審查其適用條款。沒有重新驗證乾淨環境pip安裝或跨平台依賴鎖定。
