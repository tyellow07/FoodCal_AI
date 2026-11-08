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
| Portion Estimation | 尚無可信照片重量入口 | photo_portion_grams=null；標準份量 placeholder 不作照片估重 |
| Nutrition Estimation | 診斷輸出保留 unavailable | 未完成重量、來源、食材身分及重量基準的端到端驗證 |

核心規則：Recipe candidates may explain hidden calories, but are not visual proof. 模型 confidence、retrieval score、食譜文字 evidence、人工 review 與照片觀察是不同證據，不能互換。

## 離線驗證

先依 README 安裝環境，所有命令從 FoodCal_AI 執行：

```powershell
python -B -m unittest discover -s tests -v
python -B -m unittest tests.test_ingredient_ai_draft tests.test_ingredient_ai_gui tests.test_ingredient_box_edit tests.test_ingredient_pilot -v
python -B -m src.pipeline.diagnostic_demo
```

本輪 Windows / Python 3.12：398 tests，386 passed，0 failures，0 errors，12 skipped。4 項 Streamlit 缺選用環境；8 項 Tk GUI 因 Tcl 初始化不可用跳過。新增 27 項中 19 項純邏輯通過、8 項 GUI 跳過。這不是模型準確率，也不是滑鼠操作驗收；未執行訓練、模型推論或網路下載。套件安裝／跨平台相容性未重驗。

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

1. 在新機驗證乾淨安裝，補跑 Streamlit 四項與 Tk 八項測試，分別記錄實際結果；依需求進行人工 GUI 驗收。
2. 視覺食材模型／標註仍需合法資料、獨立語意複核、來源群組與 split 驗證；目前不具訓練資格。不得自動訓練或接受 AI proposal。
3. 建立真正的照片份量估算契約、校準及不確定性，再串接營養估算。未完成前維持 null/unavailable。
4. 完成 ingredient identity、nutrition basis、合法營養來源與 review 契約整合；Recipe prior 僅解釋可能隱藏食材，不可冒充視覺證據。
5. 標註目前沒有跨程序交易鎖；hash 防過期不等於原子寫入。多人並行前需設計鎖定與測試。
6. 提交前檢查精確白名單與 diff；Push 前確認目標 Repository 可見性、權限與遠端分支進度。此次 GitHub metadata 查詢回 404，無法據此判定 Repository 不存在或是公開／私有。

沒有攜入早期 src/pipeline/mvp_pipeline.py、recognition、portion placeholder、固定 nutrition/database.py 或訓練／資料集腳本：早期流程有語法錯誤與未驗證熱量假設，亦不符合目前視覺證據規則。不是遺失本輪成果；原始版本仍保留本機。
