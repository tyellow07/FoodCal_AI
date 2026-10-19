# Pilot 既有框編輯 v0

2026-10-13。原GUI的class/visibility/occlusion/truncation只作用於新畫框，沒有選框或既有框編輯；切換圖片也會保留控制欄的旗標。这解释操作障碍，但不能因此断定所有旧旗标都错误。

最小修復：框清單顯示序號、完整annotation_id及屬性；選框載入class、visibility、occlusion、truncation、uncertainty_reason與原圖xyxy。選取框高亮，新增模式與編輯模式分開，避免選框時意外再畫一個框。切圖／新增模式重設控制欄，避免沿用上一張旗標。

```powershell
python -B -m tools.annotate_ingredient_pilot --workspace work/ingredient_annotation_pilot_v0 --actor annotator-A
```

先關閉舊GUI再啟動此命令。點清單選框 → 修改欄位／座標 → **套用指定框** → **保存人工標註（pending）**。套用只改記憶體，不代表已保存；保存以新檔名、parent hash寫新revision，不覆寫原檔。未存就切圖仍會丟失編輯。
同一框annotation_id保持；class變更時granularity按v2規範同步，其他框的內容在套用階段不變。保存人工修正版會沿用既有邏輯重設整張複核及所有框語意為pending，要求重新獨立複核。原accepted revision不刪除，亦不自動核可新內容。

review模式仍是只讀框檢視＋獨立決策，不支援修改框。程式拒絕越界、非法class／旗標、非字串理由及改寫ID。程式不判断米飯身分、遮擋語意或替使用者填理由。

逐框人工清單保存在ignored的work/ingredient_annotation_pilot_v0/correction_checklist_20261013.md；內容引用本輪讀到的原圖與框ID。使用者若已新增revision，应先确认最新ID。原始人工annotation與私有路径不放进本文件。

本輪發現pilot-003/004已有新的手動畫框revision，indeterminate改為clear/partial；仍有unknown、飯／肉絲混淆、可見蛋體範圍與image_boundary語意待人工判斷。修復只降低操作成本，不取代人工複核。

```powershell
python -B -m unittest tests.test_ingredient_box_edit -q
```

離線測試涵蓋單框隔離、輸入不變、非法編輯、ID保留、class/座標變更、GUI選框載入／套用、review只讀、模式重設與新revision不覆寫。沒有自動修改真實人工標註或執行模型。

本輪實際結果：新增7項測試passed；完整主測試430 passed、2 skipped；實驗33 passed；新版GUI啟動smoke通過。開工時所有人工revisions和manifest hash不變，沒有新增真實annotation revision。原資料／歷史標註、9個受保護資源及18份TFDA證據hash不變。程式與文件未暫存、Commit或Push。
