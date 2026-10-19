# Ingredient Identity Policy v1（草案）

日期：2026-10-11。這是下一個正式來源 Adapter 的政策提案，不是新增或修改
[Mapping Contract v1](ingredient_nutrition_mapping_contract_v1.md) 的欄位。
現有 Mock 的 food／part／state／quality／weight_basis 與 test_only 行為保持不變。
官方來源與確認／待確認事項見 [來源調查](nutrition_source_assessment_v1.md)。

## 1. 三種不同判斷

1. 原文名稱匹配：逐字名稱、已審查俗名或搜尋相似詞只發現候選；分數不是身分機率。
2. 食材身分核可：系統／人工流程比較食物、部位、狀態及加工等證據後做 scoped 決定。
3. 營養適用性：另比對質量基準、可食部、食品狀態、條目版本及營養定義；身分一致
   仍可能不適用。見 [Nutrition Reference Policy](nutrition_reference_policy_v1.md)。

不得由模型自稱 approved、單一同名結果、翻譯相似、Detection 高信心或來源官方
而跳過後兩項。原 quantity 是食譜宣告，canonical identity 不代表照片中實際存在。

## 2. Canonical identity 的必要資訊（設計欄位）

採外部身分登錄／審查資料銜接既有快照，不在 Parsing Ingredient 裡新增同義詞、
生熟狀態或營養欄位；也不在 Mock 既有 attributes 中加入未知 key。

| 資訊 | 規則 |
| --- | --- |
| canonical_ingredient_id、identity_revision | 系統管理的在地身分與修訂；不是 ingredient_id 或任何單一供應商 row ID。實質食品差異使用不同身分；修訂不覆寫歷史 |
| canonical_name、language | 審查後的標準名稱與語言，不改原 ingredient_name、anchor、text hash |
| aliases | 名稱、語言／地區、alias類型、證據 locator、審查狀態；別名只有候選搜尋用途，不保證營養等價 |
| food | 食物種類／必要時物種；混合食品保留配方／產品身分不拆成假原料 |
| part | 部位，如胸、腿、全蛋、蛋白；unknown=null，whole與not_applicable必須有明確依據 |
| state | raw／cooked／as_sold／as_prepared／unknown 等待決詞彙；未知null，不把名稱沒說熟就預設raw |
| processing | 可多項，如去皮、去骨、乾燥、鹽漬、油炸、復水；每項有證據，未知為null，不把空陣列誤認「確定無加工」 |
| quality／composition | 品種、脂肪比例、帶皮／骨、規格等實際適用描述；不是信心分數。null仍是未知 |
| product context | brand／GTIN／市場／配方修訂可null；對品牌食品的核可須提供足夠身分，不用generic條目證明品牌配方 |
| weight_basis | 可食部／購買總重／乾重／復水後重等，分開記錄；不能由明示g自動取得 |
| source bindings | provider_id、dataset、source_record_id、source_version／snapshot hash、原名稱／描述；多來源可對應同一canonical，但需獨立審查 |
| evidence／review | 逐属性保留出處、文字跨度或 row／field locator；審查者、時間、用途、政策版本與結論 |

欄位概念延伸自既有五屬性，而不是第二個 Recipe Parsing 結果模型。canonical
詞彙表、ID格式與 revision 規則待下一輪決議；未決議前不將草案當runtime schema。
「unknown」用null／明確reason表示，不能改成常見部位或既有 synthetic 字串。

## 3. 狀態與處理政策（外部審查，不改Mock）

建議身分審查 status：pending／approved／ambiguous／rejected，附 machine-readable
reason、人工／受控規則版本及適用範圍。approved 只對指定輸入快照、canonical
revision、來源紀錄與用途有效，不授予整張圖片或整個來源永久核可。

- ingredient 以 `(input_id, source.text_sha256, ingredient_id)` 關聯；同名不同宣告
  不合併。來源綁定必須使用 provider+dataset+record+version，禁止裸名稱當ID。
- 同義詞、簡繁、大小寫或英文翻譯可以產生候選；原文不變，正規化規則另存，
  不直接改 evidence_text 或跨度。不同樣品／平均值不能當成重複 row 任意去重。
- attribute 未知使影響身分的差異 unresolved；同名生熟／部位／加工候選不能自選。
  排除不相容候選後只有一筆，仍須核對剩餘必要身分；沒有結果維持 unavailable。
- 系統只檢查 hash、ID、參照與明確核可規則；語意核可要有外部審查，模型只提出候選。
- `mix`／`slice` 只保留步驟證據，不能推出cooked、去骨、加油、重量損耗或配方比例。
  若未來解析到明示加工文字，仍需獨立證據及適用性審查，不能現在補造。
- 食材optional不表示實際使用；沒有可選標記也不表示必須存在，營養適用性不消除它。

## 4. 臺灣食材政策例子（非已核可映射）

以下均為情境設計，不含正式數值、真實來源 row ID 或自稱已取得的樣本。

| 原文情境 | 可保留資訊 | 禁止自動推導／審查問題 |
| --- | --- | --- |
| 雞胸肉200g | 雞肉／胸名稱證據、食譜宣告200g；尋找符合條目候選 | raw、帶皮、脂肪規格、可食部基準不因名称而全数確定；不能套雞腿或熟雞肉 |
| 洋蔥半顆 | 半→0.5顆及原詞 | 顆的大小／重量未知，不換g；商品平均顆重不是這顆洋蔥的測量 |
| 雞蛋1顆／荷包蛋 | 原料蛋與料理名稱分開 | 帶殼／去殼、全蛋／蛋白、煎油及成品份量不同，不能直接用raw全蛋替代荷包蛋 |
| 絲瓜／菜瓜 | 俗名可列待核查候選 | alias地區與物種需核實；去皮／蒂頭與購買重差異不憑名稱推斷 |
| 豆腐／油豆腐 | 保留產品、加工描述 | 凝固、含水量、油炸／非油炸差異，不能視為同一營養身分 |
| 醬油2大匙／蠔油1匙 | 調味產品、原單位及用量 | 配方／品牌／匙規格未明，不能自選某品牌，也不能使用通用匙→g |
| 牛肉500g＋切片 | 食譜克數與slice步驟各自保存 | 切片不決定部位、肥瘦、生熟、熟重、損耗；不讓範例synthetic_cut_a變為真實部位 |

## 5. 第一版最小範圍與待決

先審查5–10個來源紀錄及少量別名；固定 provider+snapshot 的人工身分綁定，
測試同名歧義、raw/cooked、部位、未知加工、跨快照及來源修改後失效。
不建立大型本體、模型自動核可或跨來源營養合併。

待決：詞彙表與unknown/not_applicable差異；平均樣品／特定品牌的用途限制；
身分逐欄證據與人工審查角色；哪種差異可在何種用途視為等價。核可流程需要
具體版本和測試後才實作，現有Mock不因本草案而放寬。
