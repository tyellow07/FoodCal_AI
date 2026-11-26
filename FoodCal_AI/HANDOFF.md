# FoodCal AI 交接

日期：2026-11-08（2026-11-19 增補）。程式以原始專案最新工作樹為準，含未 Commit 功能；來源 HEAD 27bdfdde7b52e0c5ced901f2dc213aa1d13d4497 只作歷史基準，不能重建本次未提交內容。目標原 HEAD 9de79cf6ff6cb34150d31b5bb1d65a3b18a8c55a；分支 feature/foodcal-ai-handoff。

## 模組入口與 MVP 進度

| 階段／模組 | 入口 | 實際狀態 |
|---|---|---|
| Photo Recognition | src/detection/model.py；FoodDetector、detect_food | API 與受控 YOLO 流程存在；權重／taxonomy 須另外核准提供 |
| Visual Ingredient Evidence | src/visual_ingredients/adapter.py、review.py | 視覺候選與 recipe prior 分離；SampleReviewContext 可驗證逐樣本綁定，私人實證不附帶 |
| Annotation／AI-assisted | tools/annotate_ingredient_pilot.py、ingredient_ai_draft.py、ingredient_box_edit.py | 畫框、欄位編輯、AI 草稿逐項處理、來源追蹤、append-only revisions、獨立複核、跨程序鎖與原子寫入；不產生 AI proposal |
| Annotation schema／inventory | examples/visual_ingredient_detection_v2/annotation_spec.json；tools/ingredient_pilot.py、inventory_ingredient_data_v2.py | 規範與驗證程式可分享；真實 manifest、標註與 inventory 結果私有 |
| Recipe Retrieval | src/recipes/retrieval.py | 未提供核准 catalog 時 unavailable；synthetic catalog 僅供測試 |
| Recipe Parsing | src/recipe_parsing/{schema,validator,mock,ollama}.py | 結構與來源證據檢查、離線 Mock、選用本機 Ollama；不自動呼叫服務 |
| Nutrition Mapping | src/nutrition/mapping.py、tfda_source.py、candidate_lookup.py | 對應及來源診斷原型，私人 TFDA snapshot 不附帶 |
| Nutrition review | src/nutrition/context_provider.py、bootstrap.py、review_validation.py、review_aggregation.py、review_common.py | 明確人工斷言／審查契約；不得自動提升正式資格。review_common.py 為 Review v1 與照片審查共用的證據 helper（2026-11-13 抽出，行為不變） |
| Photo item source review | src/nutrition/photo_review.py、photo_review_types.py、photo_context_provider.py；build_photo_nutrition_basis、PhotoContextProvider | 2026-11-13 v0 實作（docs/photo_item_source_review_design_v0.md 第 12 節）：照片食材對 TFDA 條目的受控人工審查，全程不經食譜，產生 photo_calculation 的 PhotoNutritionBasis；第 10 節決策為暫定、待團隊確認；只有合成測試資料，沒有任何真實審查或 gate 紀錄 |
| CLI／Streamlit | src/pipeline/diagnostic_demo.py、yolo_diagnostic.py；src/ui/app.py、adapter.py、explain.py | 合成診斷可離線跑；真實 YOLO 另需明確啟動及合法資源。2026-11-19：主畫面中文摘要、技術細節收進開發者區、上傳照片依 EXIF 方向轉正 |
| Annotation → Visual evidence | tools/ingredient_pilot.py visual_evidence_from_annotation；src/visual_ingredients ingredient_annotation_envelope | 2026-11-19：通過 validate_annotation 的人工標註轉成 ingredient_annotation 證據，保留 annotation_id、image_sha256、annotator、reviewer，可進入份量契約與照片審查；不改訓練資格 |
| Campus meal workflow | tools/meal_workspace.py、run_meal_pipeline.py；標註 `reference_card` | 2026-11-19：校園餐照片建立校準／評估兩種工作區（不可混用）、GUI 學生證框模式、由標註＋秤重匯出校準 CSV、校準核可、估計→份量複核→熱量→predictions.csv（docs/campus_meal_workflow_v0.md）；只有合成測試 |
| Stage metrics／red-line audit | src/evaluation/stage_metrics.py；tools/evaluate_stages.py | 2026-11-19：辨識 F1（IoU ≥ 0.5）與類別涵蓋率、份量 MAPE／涵蓋率、營養對應 ±10%、第 2 項點數；紅線 R1–R4 稽核（run_meal_pipeline energy 自動輸出）；只有合成測試 |
| Usability study | src/evaluation/usability.py；tools/score_usability.py、measure_latency.py | 2026-11-19：測試流程、中文 SUS、任務成功標準、記錄範本（docs/usability_test_v0.md）、SUS／成功率／p95 反應時間與第 4 項點數；尚未找人測試 |
| Meal evaluation | src/evaluation/meal_metrics.py；tools/evaluate_meals.py | 2026-11-19：整餐參考熱量對照（docs/meal_evaluation_protocol_v0.md），計算 MAPE、涵蓋率、寬度、B0、作答率、拒答正確率與評分表點數；沒有真實測試集 |
| Portion Estimation | src/portion/contract.py、estimation.py、reference_card.py；assess_portions、estimate_with_reference_card | Contract v0（docs/photo_portion_estimation_contract_v0.md）：可見質量區間、秤重／校準方法、校準紀錄門檻與獨立複核。2026-11-19 新增估算方法 reference_card_area_ratio v0 與 tools/build_portion_calibration.py（docs/portion_calibration_protocol_v0.md）；2026-11-20 營養模型 nutrition_model.py（docs/nutrition_model_v0.md：組成食材 × TFDA 熱量＋依食譜估計的油糖，分開顯示、不重複計算）；2026-11-20 網頁辨識改為只用 Google Gemini 免費 API（src/detection/gemini_vision.py，docs/gemini_vision_v0.md；同意後才送、先塗掉學生證）；YOLO 與 Claude（claude_vision.py）先放棄、程式保留；2026-11-20 新增 reference_card_volume_density v0（volume_density.py：體積 × 網路密度，同文件第 5 節）；2026-11-20 團隊改用粗估份量做為網頁主流程（rough_volume.py＋card_locator.py＋src/ui/portions.py，docs/rough_volume_estimation_v0.md：用學生證背面 logo 自動找卡片、找不到再框選、排除錯誤辨識、形狀模板、密度表、手動加菜、自己填重量；不經校準，標為粗估，嚴格流程保留）；沒有真實秤重資料或校準紀錄，預設 photo_mass_unknown，photo_portion_grams=null |
| Nutrition Estimation | src/portion/estimation.py；estimate_photo_energy | photo_calculation 四 gate 全 passed、明示食用聲明才計算 Decimal 能量區間；basis 可由 build_photo_nutrition_basis 從照片審查產生（合成資料端到端可算），但沒有真實審查、份量紀錄或 gate 紀錄，實際輸出仍 unavailable；diagnostic／YOLO／Streamlit 結果以 `photo_energy_estimate` 呈現逐項原因 |

核心規則：Recipe candidates may explain hidden calories, but are not visual proof. 模型 confidence、retrieval score、食譜文字 evidence、人工 review 與照片觀察是不同證據，不能互換。

## 離線驗證

先依 README 安裝環境，所有命令從 FoodCal_AI 執行：

```powershell
python -B -m unittest discover -s tests -v
python -B -m unittest tests.test_ingredient_ai_draft tests.test_ingredient_ai_gui tests.test_ingredient_box_edit tests.test_ingredient_pilot tests.test_ingredient_revision_lock -v
python -B -m src.pipeline.diagnostic_demo
```

本輪 Windows / Python 3.12：398 tests，386 passed，0 failures，0 errors，12 skipped。4 項 Streamlit 缺選用環境；8 項 Tk GUI 因 Tcl 初始化不可用跳過。新增 27 項中 19 項純邏輯通過、8 項 GUI 跳過。這不是模型準確率，也不是滑鼠操作驗收；未執行訓練、模型推論或網路下載。套件安裝／跨平台相容性未重驗。

2026-11-11 macOS（Darwin 27.2.0）/ Python 3.12.0、Tk 8.6.13，沿用本機既有 `.venv`（requirements.txt 與 requirements-ui.txt 版本相符；未安裝 ultralytics；未下載任何套件）：

- 修正前：398 tests，387 passed，0 failures，11 errors，0 skipped；Streamlit 4 項首次實際執行並通過。
- 11 errors 皆為測試可攜性問題，只改三個測試檔，未改正式程式或 validator：
  - Tk GUI 8 項：macOS 暫存目錄 `/var` 是 `/private/var` 的 symlink；fixture 以未 resolve 的路徑取代 ROOT，與 AnnotationApp resolve 後的 workspace 比對失敗（`private_workspace_required`）。`DraftFixture` 改用 resolve 後路徑，與正式 `ROOT` 一致。
  - YOLO 診斷 3 項：detector 已 mock，`library_version` 仍讀取選用套件 ultralytics 的 metadata；測試內一併 mock 該版本查詢。
  - 路徑修正後，GUI 測試於同一程序連續執行會卡在 `window.update()`：Tk 8.6.13 對已 withdraw 的複雜視窗呼叫完整 `update()` 會無限空轉，不含專案程式碼的最小程式也能重現；顯示視窗或 `update_idletasks()` 不會。withdraw 視窗收不到視窗系統事件，GUI 測試改用 `update_idletasks()`。
- 修正後：398 tests，398 passed，0 failures，0 errors，0 skipped；四個標註模組 42 項通過；diagnostic_demo exit 0、partial、photo_portion_grams null、calorie unavailable。
- 仍未驗證：全新環境安裝（需下載套件）、真實模型推論、人工 GUI 操作驗收。
- 加入 Portion Contract v0 後（同環境）：422 tests，422 passed，0 failures，0 errors，0 skipped（新增 tests/test_portion_estimation.py 24 項合成測試）；四個標註模組 42 項通過；diagnostic_demo exit 0，輸出不變。
- 加入標註跨程序鎖後（同環境）：431 tests，431 passed，0 failures，0 errors，0 skipped（新增 tests/test_ingredient_revision_lock.py 7 項，含真實子程序競爭；GUI 複核保存 2 項）；標註相關五個模組 51 項通過；diagnostic_demo 輸出不變。舊流程對照：6 個程序從同一 parent 同時保存，修正前 5/5 輪皆 6 筆成功（分岔、遺失 5 筆修改），修正後 5/5 輪皆恰好 1 筆成功。
- 加入份量／熱量診斷輸出與 YOLO 版本修正後（同環境）：435 tests，435 passed，0 failures，0 errors，0 skipped（新增 4 項，並在既有 UI／YOLO 測試補上新區塊斷言）；標註相關五個模組 51 項通過；diagnostic_demo exit 0，`photo_energy_estimate` 為 unavailable、既有欄位不變。`run_yolo_diagnostic` 在查不到 ultralytics 套件 metadata 時改記 `library_version=null` 並照常寫出 result.json 與核對受保護資源（原本直接拋出例外）。
- 2026-11-19 晚間（分支 tyellow-reference-card-pipeline，逐一推上 main）：階段指標與紅線稽核、易用性工具包、支援食物說明與 CLI 摘要、PhotoContextProvider、標準函式庫涵蓋率工具；523 tests 全過（在涵蓋率工具內執行時，巢狀的涵蓋率測試會自行 skip）。
- 2026-11-19 校園餐工作流程與學生證框（分支 tyellow-reference-card-pipeline）：新增標註卡片框驗證／保存／GUI 測試 4 項與校園餐端到端 4 項（照片 → 工作區 → 標註 → 校準匯出 → 擬合 → 核可 → 估計 → 複核 → 熱量 → 評估）；全部合成資料。
- 2026-11-19 增補後（同環境，分支 tyellow-mvp-usability，自 tyellow-photo-item-review 分出）：491 tests，491 passed，0 failures，0 errors，0 skipped（新增 26 項：UI 方向 1、中文摘要 6、標註→證據 2、學生證比例尺份量 10、整餐評估 7；Streamlit AppTest 補摘要斷言）；diagnostic_demo exit 0，輸出與修改前逐位元組相同；Streamlit 實際啟動 health check 回 ok。合成資料端到端可算出熱量；所有數值皆為測試用合成數字。未取得或使用任何真實秤重、照片或 TFDA 資料。
- 2026-11-13 加入照片食材來源審查 v0 後（同環境，分支 tyellow-photo-item-review）：465 tests，465 passed，0 failures，0 errors，0 skipped（新增 tests/test_photo_item_source_review.py 30 項，含第 9 節 14 個驗收案例與 48 個攻擊案例子測試）；Review v1／Context Provider／bootstrap 既有 72 項未修改即通過；標註相關五個模組 51 項通過；diagnostic_demo exit 0，輸出與修改前逐位元組相同。共用 helper 抽出另以 176,490 筆變異輸入比對新舊 validator，狀態、錯誤碼與路徑 0 差異。
- 2026-11-23 營養區加上其他營養素（`docs/nutrition_model_v0.md` 第 6 節；飽和脂肪、糖、膳食纖維、鈉、膽固醇、鉀、鈣、鐵，取自 TFDA 開放資料集同一筆整合編號，調味鈉依烹調方式估計）：580 tests，580 passed，0 skipped；網頁用範例 1 實測一次。
- 2026-11-23 多角度份量估計（`docs/multi_view_volume_v0.md`，`src/portion/multi_view.py`、`src/evaluation/multi_view_sim.py`、`tests/test_multi_view.py`）：只在模擬場景與合成學生證圖片驗證；真實準確度要依該文件第 5 節收秤重資料。
- 2026-11-26 水果、飲料與更多食物（`docs/nutrition_model_v0.md` 第 7 節、`docs/rough_volume_estimation_v0.md` 第 8 節、`docs/multi_view_volume_v0.md` 第 7 節）：633 tests，633 passed，0 skipped（新增 9 項）；diagnostic_demo exit 0；`tools.benchmark_multi_view` 原有三種形狀的數字不變。只用合成資料與模擬場景。
- 2026-11-26 校園菜單檢查與第二批 84 種食材（`docs/nutrition_model_v0.md` 第 8 節、`tests/test_campus_menu_coverage.py`）：635 tests，635 passed，0 skipped。
- 2026-11-26 多種方式找學生證（`docs/rough_volume_estimation_v0.md` 第 9 節、`tests/test_card_finder.py`）：643 tests，643 passed，0 skipped。
- 2026-11-26 點四個角標學生證（`docs/demo_ui_v0.md` 最後一節、`src/ui/corner_picker.py`）：645 tests，645 passed，0 skipped；Chrome 預覽腳本實際點過。
- 2026-11-26 肉類補齊 33 種（`docs/nutrition_model_v0.md` 第 9 節）：647 tests，647 passed，0 skipped。
- 2026-11-26 海鮮 27 種（`docs/nutrition_model_v0.md` 第 10 節）：649 tests，649 passed，0 skipped；diagnostic_demo exit 0。
- 2026-11-26 菜色辨識評估（`docs/recognition_evaluation_v0.md`、`tools/run_gemini_batch.py`、`tools/evaluate_recognition.py`、`tests/test_recognition_eval.py`）：666 tests，666 passed，0 skipped（新增 17 項）；只用合成標註、合成回答與合成照片，沒有送任何照片給 Gemini。
- 2026-11-26 六種語言 × 五種寬度（360–1280 px）檢查，修正被截掉的語言選單、按鈕與開關文字（`docs/demo_ui_v0.md` 最後一節）：666 tests，666 passed，0 skipped；diagnostic_demo exit 0；src/ 涵蓋率 91.4%。

## 私有測試與缺席資源

沿用安全分享版對下列原測試的排除，不複製依賴資料、不造假通過；它們未被 discovery 收集，**不計入上述 12 skipped**：

| 原測試 | 缺席原因 |
|---|---|
| test_visual_sample_review.py | sample_reviews_v11 含真實 GT／人工審查衍生 metadata，不能當純合成 fixture 分享 |
| test_recipe_retrieval.py | setUp 直接讀私人 taxonomy 與既有 detection smoke 結果 |
| test_adapter_final_evaluation.py | 依賴實驗 manifest、保存模型輸出與 evaluation 證據 |
| test_tfda_source_local_snapshot.py、test_nutrition_candidate_lookup_local_snapshot.py | 需要另行核准的 FOODCAL_TFDA_SNAPSHOT；原版本用環境條件 skip，分享版未攜入固定快照測試 |
| tests/evaluation 歷史測試 | 混合資料集、圖片、GT、人工 review 與模型評估原件；未攜入 |

新 AI 草稿測試完全使用暫存合成內容。tests/test_food_detection.py 保留既有分享版對合成 classes.yaml 的注入；不改正式 Detector 或 Validator。換電腦若要復原私人工作，須另行安全移交合法資料與原始證據，不能靠此 Git Repository 還原私人標註。

## 已知限制與下一個 Agent

1. Streamlit 四項與 Tk 八項已於 2026-11-11 在 macOS 補跑通過（見「離線驗證」）。仍待：全新環境乾淨安裝、在 Windows／Linux 重跑修正後的 GUI 測試，以及依需求進行人工 GUI 驗收。
2. 視覺食材模型／標註仍需合法資料、獨立語意複核、來源群組與 split 驗證；目前不具訓練資格。不得自動訓練或接受 AI proposal。
3. 2026-11-11 已完成 Photo Portion Estimation Contract v0（契約、校準紀錄門檻、區間不確定性與 photo_calculation 串接閘門，見 docs/photo_portion_estimation_contract_v0.md）。2026-11-13 已依第 10 節建議預設實作 photo_calculation 營養基準審查 v0（docs/photo_item_source_review_design_v0.md 第 12 節）。仍待：團隊確認第 10 節暫定決策、Local Context Provider 照片 profile（2026-11-19 已暫定實作 PhotoContextProvider，待確認）、TFDA 8543 條款核對者與真實 gate 紀錄、合法且可依來源分組的秤重資料與校準（估算方法已於 2026-11-19 實作）、標註規範加入學生證框（目前卡片框需另外量）、剩食前後對比。上游視覺證據的 annotator 已由 visual_evidence_from_annotation 補上（2026-11-19）；偵測 adapter（from_detection／from_reviewed_detection）仍沒有 annotator。diagnostic／YOLO／Streamlit 輸出已於 2026-11-11 接入 `photo_energy_estimate`。未完成前維持 null/unavailable。
4. 完成 ingredient identity、nutrition basis、合法營養來源與 review 契約整合；Recipe prior 僅解釋可能隱藏食材，不可冒充視覺證據。
5. 2026-11-11 已加入標註跨程序鎖：每個 annotations 目錄的 OS advisory lock（`.revision.lock`）、鎖內以 `require_latest` 重新確認 parent、暫存檔 fsync 後改名的原子寫入；`save_pending` 與 GUI 複核保存都使用。限制：只保證同一台電腦的本機檔案系統（雲端同步資料夾、網路磁碟不適用，跨電腦多人仍須共享服務或分開工作區）；Windows `msvcrt` 路徑尚未在 Windows 實測；鎖不可重入；當機殘留的 `.tmp` 暫存檔不會被讀取，也不會自動刪除。
6. 提交前檢查精確白名單與 diff；Push 前確認目標 Repository 可見性、權限與遠端分支進度。此次 GitHub metadata 查詢回 404，無法據此判定 Repository 不存在或是公開／私有。

沒有攜入早期 src/pipeline/mvp_pipeline.py、recognition、portion placeholder、固定 nutrition/database.py 或訓練／資料集腳本：早期流程有語法錯誤與未驗證熱量假設，亦不符合目前視覺證據規則。不是遺失本輪成果；原始版本仍保留本機。

## 本機模型環境（2026-11-20）

- 權重來自 GitHub 分支 `codex/project-sync-audit`（組員 commit，未合併進 main）：`best.pt` 5,393,221 bytes、
  SHA-256 `89e75f89…a43f`，靜態檢查為 detect、15 類、名稱與 `examples/diagnostic_v0/classes.yaml` 逐字相同、Ultralytics 8.4.174；
  `models/yolo26n.pt` 是 COCO 80 類訓練起點，推論不需要。兩者皆標示 AGPL-3.0。
- 本機把 `best.pt` 放在預設路徑（Git 忽略），`data/roboflow_taiwanese_food_detection/foodcal_data.yaml` 是**暫用的只含 names 版本**；
  原始檔（SHA-256 `a96e7994…eefbb9`）仍需向組員取得，重新訓練或驗證前必須換回。
- `.venv` 已安裝 `requirements-model.txt`（ultralytics 8.4.174、torch 2.15.0，原有鎖定版本不變），Ultralytics 匿名統計已關閉（`yolo settings sync=False`）。
- 真實推論在網頁與 `tools/measure_latency.py` 跑通：CPU 首次載入約 1.5 秒，之後 p95 0.045 秒；只用過合成測試圖，尚未用校園餐照片。

## 2026-11-26 之後的優先順序

完整評分表、目前自評（2026-11-26 約 23.5／100）與「分數從哪裡來」見 `docs/project_rubric_v0.md` 第 9、10 節。測試涵蓋率用 `python -B -m tools.measure_coverage`（標準函式庫，src/ 約 91.4%）。網頁辨識已改用 Gemini（`docs/gemini_vision_v0.md`），原本 YOLO 15 類不夠用的問題不再擋住展示。

1. 收真實資料（不能用程式取代）：依 `docs/meal_evaluation_protocol_v0.md` 建 ≥ 30 餐秤重測試集，另依 `docs/portion_calibration_protocol_v0.md`
   在不同天收校準樣本（白飯、麵、煎蛋各 ≥ 30 筆）。兩者不能混用。
2. 菜名辨識評估：依 `docs/recognition_evaluation_v0.md` 人工標出同一批校園照片的正確菜名和框，跑 `tools.run_gemini_batch`、`tools.evaluate_recognition`，列出 ≥ 3 個失敗案例。
3. 團隊決策：照片審查第 10 節八項、TFDA 條款核對者、校準核可者與份量複核者。
4. 找 ≥ 5 位非組員做易用性測試（`docs/usability_test_v0.md`），其中一次用真手機完成 T1（模擬器已在 360／390 px、六種語言看過，`docs/demo_ui_v0.md` 最後一節）。
5. 辨識速度：Gemini 一次 8.9–37 秒，評分表 4c 要 p95 ≤ 5 秒。
6. 工程：組員在 macOS 與 Windows 各做一次乾淨安裝；之後的變更走 PR，由作者以外的人 review。
7. 已完成：標註的 `reference_card` 框與校園餐工作流程（2026-11-19）；食材表 226 種，含肉類與海鮮（2026-11-26，`docs/nutrition_model_v0.md` 第 9、10 節）。
