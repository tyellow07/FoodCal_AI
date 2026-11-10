# AI 草稿匯入與人工標註 GUI

這是去除私人樣本 ID、人工 annotation ID、圖片判讀及歷史私人 smoke 紀錄後的通用操作文件。實作同步自最新工作樹，匯入只讀既有 proposal，不執行模型。

## 前置條件與啟動

從 FoodCal_AI 執行；Python 需可用 Tk/Tcl、Pillow。workspace 必須位於本專案 `work/` 下，包含經核准來源的本機圖片、manifest 與符合 annotation_spec 的資料。`tools/ingredient_pilot.py` 的 build_workspace 是既有資料集專用工具，不是任意圖片通用匯入器；檢查其來源與格式前不要執行。分享版刻意不提供私人工作集。

以下為示意路徑，必須改為自己合法建立的 workspace，不代表庫內附帶資料：

```powershell
python -B -m tools.annotate_ingredient_pilot --workspace work/my_pilot --actor annotator-A
python -B -m tools.annotate_ingredient_pilot --workspace work/my_pilot --actor reviewer-B --review
```

可加 `--image-id` 指向 manifest 中存在的 ID。獨立複核使用不同 actor；檢查 actor 不是身分認證。

## 操作順序

1. 人工框為實線；匯入本圖 AI proposal 後，尚未處理候選為紫色虛線。匯入不改人工框或類別檢查，也不保存 revision。
2. 選取候選，檢查原圖 xyxy、class、visibility、occlusion、truncation 及不確定原因；修改欄位後先按「套用修改」。
3. 明確選擇新增、指定人工 annotation_id 後替換，或拒絕。相同物件應人工確認替換對象；程式不做語意自動匹配。拒絕不刪除原人工框。
4. 所有候選處理完成並逐項核對類別後，明確保存才新增 pending revision。保存後可查看 proposal hash、原候選及操作歷程。
5. 使用獨立複核模式檢視及另存複核決策。此模式禁止匯入與改框，但明確的複核決策仍會新增 revision。

「撤銷最後一框」不是復原 AI 替換。未保存整次操作可離開並確認放棄；已保存版本不覆寫。再次匯入舊 proposal 會因 revision hash 過期阻擋，不應繞過。

## 驗證與限制

匯入／保存核對 manifest、圖片 hash 與尺寸、spec、scope、最新 revision、proposal bytes、enum 與框座標。重複候選、未處理候選、未套用欄位及過期 revision 會阻擋；新 revision 不提升訓練或營養資格。hash 是一致性檢查，不是來源授權或模型身分認證。

同時鐘刻度的 revision 檔名仍維持時間排序；沒有跨程序交易鎖。重疊框是否為同一物件仍需人工判斷。AI 輸入／接受不等於 GT；原圖語意及 bbox 正確性需另行人工驗收。

測試只建立暫存合成圖片、proposal 與 revisions，不讀私人工作集；命令及本輪實際結果見 HANDOFF.md。
