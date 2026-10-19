> 2026-10-19 團隊分享權確認：使用者明確確認有權將本次105檔中的自有程式、文件與合成內容分享至指定Private團隊Repository，沒有未解決的第三方內容授權；不包含資料集、權重或第三方套件本體。這項確認不授予公開散布或外部資源使用權。
> 子目錄整合後再次執行371項離線測試，全部通過；未重新安裝依賴或執行真實模型。

> 歷史審查快照：下列原紀錄保留建立分享副本時的狀態。本次整合至既有Private團隊Repository的FoodCal_AI/，從最新main建立feature/add-foodcal-ai，保留團隊歷史。空庫及初始main流程已由使用者明確改為獨立分支/PR流程。
>
> 本次僅README與本報告的整合說明有變更，其餘103檔依原SHA-256及Git blob核對。原FoodCal專案及原分享副本不修改。授權確認與實際測試結果以本次交付報告及PR描述為準；本文件不是開源或資料/權重使用授權。

# Safe team sharing review

日期：2026-10-19；原專案基準HEAD：27bdfdde7b52e0c5ced901f2dc213aa1d13d4497。
分享基礎為當前已審查工作樹，不限於這個舊commit；沒有複製.git、搬移原件或更動原專案。

## 程式／資料邊界

包含Detection API、Fixture CLI/UI、Recipe Parsing／Retrieval、Nutrition離線原型、Visual Ingredient v1/v1.1程式與標註工具及必要合成測試。實際人工標註與真實預測證據不在副本，不能據此訓練或宣稱食材模型已達標。
排除data/runs/work/models/weights/outputs、圖片、權重、人工annotations、真實Manifest/GT衍生紀錄、TFDA原始或小樣本營養列、私有audit、Ollama logs/thinking、實驗輸出、虛擬環境與舊Git歷史。既有依賴實際私有資源的測試不列入本套件，詳README。

原README和核心requirements是空檔，副本補齊。唯一來源測試修改為test_food_detection的taxonomy來源改用合成15類fixture；正式API程式與Validator不放寬。來源對照與SHA-256清單在Repository外的review目錄，不應整包上傳review資料。

## 能執行／不能執行

無私有資料可執行Fixture CLI、合成Parsing/Mapping、視覺證據示範與Mock離線測試；UI需選用Streamlit，標註GUI需Tk/Tcl。真實YOLO無權重時blocked，私人標註GUI無合法workspace不能直接標資料；TFDA固定快照及真實Gemma實驗不可重現，因原件刻意不分享。
沒有訓練、YOLO/ViT/Gemma推論、下載資料或自動補模型。熱量固定unavailable，照片重量null，正式營養資格false。

## 隱私／授權

掃描常見憑證及絕對路徑；測試中的C:/PRIVATE、C:/Users/private及/home/private是假路徑拒絕案例，不是真實機器帳號。沒有發現真實本機路徑或常見token模式，但靜態掃描不等於完整DLP或著作權審核。
不附資料／權重授權、不擅自指定MIT等專案License。程式分享權、合成內容及第三方依賴條款需團隊確認。此版本僅作團隊審查候選，不能宣稱適合公開發布。

## 新Git與首次上傳條件

建立全新main與白名單index，不建立commit、不設定remote。採default-deny .gitignore：只有核准檔案可加入，未知新檔及私人路徑預設忽略；這不能防止有權者刻意git add -f或改寫既有檔案，需要每次提交前繼續審查。
本輪不連接遠端。Repository為空僅是使用者描述，寫入權限未核驗。首次Push前須確認空庫、權限及分享資格，經使用者批准後才建立初始commit、設定指定remote與Push main；任何ref或既有內容衝突均停止，不force、不重寫遠端歷史。
建議Commit訊息：feat: publish sanitized FoodCal AI team demo snapshot。

## 實際離線驗證

105 個精確白名單候選已暫存於全新 main；沒有 commit、remote 或 push。371 項 unittest 全部通過（0 skipped），Fixture CLI 與 UI Adapter 結果一致；無權重時真實 Detection 安全阻擋。測試使用既有已安裝依賴，未驗證全新 pip 安裝。

原專案 298 份可見檔與 26 份私有工作資料／Git metadata 的 SHA-256 及 Git status 保持不變。Git index SHA-256／blob ID 與最終掃描結果另存副本外 review/INDEX_MANIFEST.json 與 REVIEW_RESULT.json；不可把該私人審查目錄整包上傳。副本 gitattributes 明確允許 Windows CRLF，但仍檢查實際尾端空格；來源內容不因此轉換換行。
