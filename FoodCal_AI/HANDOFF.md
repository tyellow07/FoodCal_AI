# FoodCal AI 交接

日期：2026-11-08。程式以原始專案最新工作樹為準，含未 Commit 功能；來源 HEAD 27bdfdde7b52e0c5ced901f2dc213aa1d13d4497 只作歷史基準，不能重建本次未提交內容。目標原 HEAD 9de79cf6ff6cb34150d31b5bb1d65a3b18a8c55a；分支 feature/foodcal-ai-handoff。

## 模組入口與 MVP 進度

| 階段／模組 | 入口 | 實際狀態 |
|---|---|---|
| Photo Recognition | src/detection/model.py；FoodDetector、detect_food | API 與受控 YOLO 流程存在；權重／taxonomy 須另外核准提供 |
| Visual Ingredient Evidence | src/visual_ingredients/adapter.py、review.py | 視覺候選與 recipe prior 分離；SampleReviewContext 可驗證逐樣本綁定，私人實證不附帶 |
| Annotation／AI-assisted | tools/annotate_ingredient_pilot.py、ingredient_ai_draft.py、ingredient_box_edit.py | 畫框、欄位編輯、AI 草稿逐項處理、來源追蹤、append-only revisions、獨立複核；不產生 AI proposal |
| Annotation schema／inventory | examples/visual_ingredient_detection_v2/annotation_spec.json；tools/ingredient_pilot.py、inventory_ingredient_data_v2.py | 規範與驗證程式可分享；真實 manifest、標註與 inventory 結果私有 |
| Recipe Retrieval | src/recipes/retrieval.py | 未提供核准 catalog 時 unavailable；synthetic catalog 僅供測試 |
| Recipe Parsing | src/recipe_parsing/{schema,validator,mock,ollama}.py | 結構與來源證據檢查、離線 Mock、選用本機 Ollama；不自動呼叫服務 |
| Nutrition Mapping | src/nutrition/mapping.py、tfda_source.py、candidate_lookup.py | 對應及來源診斷原型，私人 TFDA snapshot 不附帶 |
| Nutrition review | src/nutrition/context_provider.py、bootstrap.py、review_validation.py、review_aggregation.py | 明確人工斷言／審查契約；不得自動提升正式資格 |
| CLI／Streamlit | src/pipeline/diagnostic_demo.py、yolo_diagnostic.py；src/ui/app.py、adapter.py | 合成診斷可離線跑；真實 YOLO 另需明確啟動及合法資源 |
| Portion Estimation | src/portion/contract.py、estimation.py；assess_portions | Contract v0（docs/photo_portion_estimation_contract_v0.md）：可見質量區間、秤重／校準方法、校準紀錄門檻與獨立複核；尚無估算模型或校準資料，預設 photo_mass_unknown，photo_portion_grams=null |
| Nutrition Estimation | src/portion/estimation.py；estimate_photo_energy | photo_calculation 四 gate 全 passed、明示食用聲明才計算 Decimal 能量區間；目前無此用途核可基準，輸出 unavailable；診斷輸出未接入 |

核心規則：Recipe candidates may explain hidden calories, but are not visual proof. 模型 confidence、retrieval score、食譜文字 evidence、人工 review 與照片觀察是不同證據，不能互換。

## 離線驗證

先依 README 安裝環境，所有命令從 FoodCal_AI 執行：

```powershell
python -B -m unittest discover -s tests -v
python -B -m unittest tests.test_ingredient_ai_draft tests.test_ingredient_ai_gui tests.test_ingredient_box_edit tests.test_ingredient_pilot -v
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
3. 2026-11-11 已完成 Photo Portion Estimation Contract v0（契約、校準紀錄門檻、區間不確定性與 photo_calculation 串接閘門，見 docs/photo_portion_estimation_contract_v0.md）。仍待：合法且可依來源分組的秤重資料與真正估算方法／校準、photo_calculation 營養基準審查 profile、審查後接入 diagnostic 輸出、剩食前後對比。未完成前維持 null/unavailable。
4. 完成 ingredient identity、nutrition basis、合法營養來源與 review 契約整合；Recipe prior 僅解釋可能隱藏食材，不可冒充視覺證據。
5. 標註目前沒有跨程序交易鎖；hash 防過期不等於原子寫入。多人並行前需設計鎖定與測試。
6. 提交前檢查精確白名單與 diff；Push 前確認目標 Repository 可見性、權限與遠端分支進度。此次 GitHub metadata 查詢回 404，無法據此判定 Repository 不存在或是公開／私有。

沒有攜入早期 src/pipeline/mvp_pipeline.py、recognition、portion placeholder、固定 nutrition/database.py 或訓練／資料集腳本：早期流程有語法錯誤與未驗證熱量假設，亦不符合目前視覺證據規則。不是遺失本輪成果；原始版本仍保留本機。
