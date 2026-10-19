# Visual Ingredient Annotation v0

範圍：本機 diagnostic/test_only 標註審查規範草案；不修改既有 Contract、YOLO GT 或 Adapter。此規範不能自行建立官方食材名稱、模型能力或來源授權。

## 標註粒度

- dish-level：整碗／整盤料理及混合成分的總範圍，不能轉成一種食材。
- ingredient-level：能在照片中單獨辨認的食材／組件，可見部分有可定位的區域。例如明顯的熟蛋體可作熟蛋候選，不因此斷定精確工法或熟度。
- ambiguous：食材身分、群組粒度或框內成分無法區分。以泛化外觀描述或 null 保留，不強迫選既有類別。

單一 instance 與多塊同類 group 必須明示；預設以可見 instance 為單位。group 框只能表達有明確同類可見成分的區域集合；若混入其他成分／大片湯汁，保留 ambiguous，必要時用多個可見框或 mask。不能把整盤 loofah 類別框當成全部像素都是絲瓜。

## 最小審查紀錄

| 欄位 | 要求 |
| --- | --- |
| annotation_id、policy_version | 唯一審查 ID 與 v0 版本 |
| image_reference、image_sha256、dimensions | 對應原圖，引用留本機、對外不含私人絕對路徑 |
| original_gt_reference | split、label file hash、row number、原 class ID/name、原始 normalized xywh；不覆寫 GT |
| semantic_level、granularity | dish-level/ingredient-level/ambiguous；instance/group/unknown |
| ingredient_candidate、identity_resolution | 候選名稱或 null；泛類已核對／精確身分待核對分開記錄 |
| visible_region、coordinate_space | 原圖 pixels，xyxy 半開區間，0≤x1<x2≤width、0≤y1<y2≤height；mask 若另標需 hash／格式引用 |
| visibility、occlusion、truncation | clear/partial/indeterminate；遮擋與截斷可 unknown，不能把 unknown 當 none |
| preparation_state | 只記直接可見且有證據的狀態；煎熟外觀不證明油量、翻面或精確工法 |
| evidence_refs、reviewer_or_process、review_status、reason | 原圖／框／原始人工紀錄的引用，審查流程識別，verified/ambiguous/insufficient_evidence 與原因 |

模型 Prediction 引用另存 model digest、inference settings、detection_id、原分數及框；模型分數不等於人工標註信心。人工標註 confidence 沒有分數時為 null，不發明 1.0。原圖標註、模型輸出、合成標註、食譜推測必須使用不同 evidence_source。

## visible ingredient candidate 條件

1. 已定位到原圖與食材層級區域，圖像有足够外觀支持候選，不是只看料理名稱或配方。
2. 已明示 instance/group，並審查框內的其他成分；餐具、盤面、湯汁不能作為该食材證據。
3. 食材名稱精度符合證據；外觀不足以区分絲瓜／小黃瓜時，保留 ambiguous 或更寬泛候選，不以 GT 名称自證。
4. 遮擋／截斷須記錄；未知的遮擋後區域不可補畫成可見，也不能利用食譜推論被遮住的食材。身份不能確定則 uncertain；完全不可見或無定位證據則 unavailable。
5. 來源及審查紀錄充分，且獨立於被評估的 Prediction；hash 證明完整性，不證明標註真實性或審查者授權。

符合以上條件最多建立 observed_candidate，仍不是 confirmed ingredient 或 nutrition eligible。verified 審查只能在明示 scope 有效，不會核可整個資料類別或同一模型的所有預測。

## 反例與 fail-closed

- cold_noodle／turkey_rice dish GT 或 Prediction：不得自動產生麵條、肉、蔬菜或調味料。
- loofah 整盤混合料理框：身份或区域不清晰時 ambiguous；不要将其他配料算成絲瓜。
- 飯類 Pred 蓋住蛋 GT：保持原 Prediction；不能改 class、拆出蛋框或提升成蛋 observed_candidate。
- 油光、醬色或食譜中的油／糖：最多 inferred_from_recipe，沒有單獨視覺證據不進 visual_ingredient_evidence。
- 缺 image hash、跨食材／圖片引用、原 GT 指標不符、缺少審查規範／流程：blocked/unavailable，不能使用 verified 字串升級。
- 未審查的類別、來源更新、hash 不符：先撤回可用規則並重審，不能自動繼承舊核對。

## 最小 Adapter 接入門檻（本輪不啟用）

需一份呼叫端受控的逐類語意規則與逐樣本審查紀錄，綁定 taxonomy hash、model digest、policy version、image hash 和 Prediction ID。僅對通過獨立檢查的食材層級 Prediction 建立候選；沒有正確類別輸出、名稱歧義或群組框不適用時仍 unavailable/uncertain。分類信心、GT 或食譜均不能補造模型證據。

未來最小測試：egg 真實框通過但無 ID 0 Pred 仍 unavailable；loofah 混合盤拒絕升級；未審查同類 Pred 拒絕；跨 image/GT/hash 拒絕；遮擋／截斷保留；料理標籤不展開；recipe 油／糖不升級；規範／來源版本變更停止舊核可。若走 segmentation，另外測試可見 mask、unknown 像素與原圖座標對應，不能將 mask 或框面積換成克數。
