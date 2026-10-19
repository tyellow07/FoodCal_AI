# Nutrition Reference Policy v1（草案）

日期：2026-10-11。政策用途：下一個 Nutrition Source Adapter 的資料保存與
適用性審查，不是現在的熱量介面，也不修改 Mapping Contract／Mock／Parsing。
與 [Identity Policy](ingredient_identity_policy_v1.md) 共同使用；官方調查與查閱
紀錄見 [Nutrition Source Assessment](nutrition_source_assessment_v1.md)。

## 1. Reference 所需資訊（外部來源登錄草案）

保留供應商原 row 與明確轉換的衍生紀錄；重用 Mapping 的 entry_id、identity、
source及nutrient_basis概念，後續正式型別需另行審查，不能向目前Mock注入production。

| 資訊 | 政策 |
| --- | --- |
| reference_id／revision | 系統管理；綁定 provider、dataset、record、資料型別、版本／snapshot，不使用名称當ID |
| provider_id／source_record_id | TFDA與USDA命名空間分開；FDC ID、NDB、GTIN、樣品／整合編號分開保存，不假定相同含義 |
| dataset_version | 官方版次／釋出日期若取得則保存；未知null並記reason。不可把查閱日期或metadata更新日冒充版次 |
| snapshot | 實際取得時間、官方resource URL、原件bytes SHA-256、record locator／row hash、encoding與讀取工具版本 |
| documentation／license | 文件URL與版次、授權名稱／版本／URL、核對時間、顯名聲明、snapshot／必要保存指紋及允許用途 |
| source identity | 樣品原名稱、俗名、分類、內容描述、資料類別、品牌／市場（若提供），對應已審查canonical revision |
| measurement_basis | 明示 amount＋unit（如100、g或ml）、可食部／總重、食品狀態、乾燥／復水／調理條件；未知必須標明 |
| nutrient observations | 每項保留 provider nutrient ID／原名稱、明確單位、原值／原文字、數值狀態、方法／推導分類、證據field locator |
| precision／sample metadata | n、SD／SE、範圍、LOQ、label rounding、取樣或發佈時間等有提供才保存；缺少用null，SD不直接当圖片熱量區間 |
| transform provenance | 原值、轉換規則／版本、衍生單位及理由；不覆寫原件。不混淆相同名稱但定義不同的營養素 |

沒有正式release tag時，可用取得時間＋原件hash識別本地snapshot，保留official
version=null；只有確認供應商政策與schema後，才允許該snapshot作核可單位。
資料查詢時「latest」不能代替固定版本；更新需新revision及重新檢查身分／基準。

## 2. 營養值與缺失狀態

草案每項 observation 含 status、value、unit、raw_value、qualifier／limit（可null）、
derivation、evidence_locator。數值需有限，不接受bool／NaN／Infinity；不自動修正負值
或字串。沒有任何數值時仍可保留可追溯條目，完整性狀態另列。

| status（提案） | normalized value | 意義 |
| --- | --- | --- |
| reported | 官方數值；明示0可保存0 | 依官方方法／推導分類解讀，不等於直接量測或確定真零 |
| missing | null | 未提供、空白或無此項；保留reason，不能当0 |
| below_limit | null，另存limit與operator | <或LOQ等，不能把低於量測限制當確切0 |
| label_rounded | 官方標示數值，可為0 | 保留標示／四捨五入性質，不升級為化學分析真零 |
| unparsed | null | 原文符號或代碼尚未定義，保留原值、issue與待核對狀態 |
| invalid | null | 型別、非有限值、負值或基準／單位矛盾；保留拒絕理由 |

Foundation 文件指出LOQ可能存於另欄而amount=0，不能只读amount。[官方說明](https://fdc.nal.usda.gov/Foundation_Foods_Documentation/)
Branded缺值不是0，且標示0可能反映四捨五入。[官方說明](https://fdc.nal.usda.gov/GBFPD_Documentation/)
兩者查閱日期均2026-10-11。TFDA現行缺值代碼未核實，unknown符號一律unparsed，
不能擅定ND代表0；這是保守設計，不是聲稱官方採同一狀態列舉。

每項營養素保留原單位：例如能量kcal／kJ與質量g／mg／µg分開；不只用泛稱
carb或energy。不同能量推導、總碳水／其他碳水定義不得靜默合併。若後續批准
單位轉換，必須是已審查的確定性規則並保留原值，本輪不實作也不由LLM產生數值。
完整性採用途指定的required_nutrient_ids＋available/missing/invalid清單；沒有
「官方資料所以所有欄位必然完整」的假設，也不以4-4-9等補回缺失能量。

## 3. 質量／基準適用性

- 明示g只能保存recipe-declared mass。未確認去骨／去皮、生熟或乾濕狀態時，
  不能宣稱匹配per100g可食部或某份成品營養基準。
- 不把100ml當100g。ml→g須食品身分、狀態、密度及測量條件的可追溯依據；
  不能對所有液體假設密度1。
- 顆／根／匙須可信的食品特定portion紀錄及尺寸、單位、品牌／處理狀態，仍只是
  明訂範圍的reference conversion，不證明實際物件重量。沒有依據則unavailable。
- TFDA「每單位重」與USDA food_portion有欄位不等於通用換算授權；需逐項
  核對條目及描述。USDA官方明示份量重依資料類型／食品而異。[Foundation](https://fdc.nal.usda.gov/Foundation_Foods_Documentation/)
- 廢棄率不可套到已是可食部的重量，也不可從任意樣品搬到照片物件；廢棄、烹調
  損耗、水分變化、吸油與營養保留是不同作用，不用mix/slice步驟創造係數。
- recipe總用量、每份用量、實際烹調量與照片食用量分開；缺份數、產量或攝取
  證據不能互相推導。Detection confidence／retrieval_score不填補這些缺口。

## 4. 核可流程：四個獨立 gate

每個gate保存pending／passed／failed／not_applicable（後者需理由），以及
reviewer、時間、policy revision、evidence locator與reason code。授權核查使用官方
條款，不讓模型或source.review_status替代核查；核可只針對指定snapshot和用途。

| Gate | passed所需證據 | 常見阻擋 |
| --- | --- | --- |
| A source_use | 官方來源與授權範圍、取得方式、顯名／再散布義務明確 | license_scope_unconfirmed、attribution_missing、resource_unverified |
| B identity | 人工／受控規則核对來源條目與canonical，包括必要狀態／部位／加工 | identity_ambiguous、state_unknown、part_conflict |
| C basis | 食品狀態、可食部、基準單位與指定質量用途一致；轉換若有則可追溯 | basis_unknown、raw_cooked_conflict、conversion_evidence_missing、photo_mass_unknown |
| D integrity | 原件／row hash、ID／版本、欄位型別、營養定義／單位及用途必要項完整 | snapshot_missing、version_conflict、nutrient_missing、unknown_value_code |

用途範圍明訂record保存／reference查詢／recipe calculation／photo calculation。
Source Reader可提供未核可原件；A與D部分確認也不等於能計算。四gate對指定
用途都滿足才可提出正式使用資格；另須保留上游recipe/test_only、人工完整性審查
與份量條件。合法USDA來源不能提升synthetic recipe或unknown圖片重量。
本輪不授予任何來源、條目或新Mock正式資格。

not_applicable只適用確實不涉及該檢查的用途，例如原件保存不涉及特定recipe質量。
source_use與snapshot完整性不能用not_applicable跳過；recipe／photo calculation
不得用not_applicable跳過必要身分、質量基準或營養完整性檢查。

## 5. 與現有介面的關係、最小下一步

Mapping.matched、mass.available、qualification.test_only仍按既有規格，各自獨立。
這些新reference／gate資訊放在未來外部來源登錄／審查記錄，待正式型別提案
後才接入；不把它們塞進現有Mock的closed schema或更改Parsing原文。
舊NutritionDatabase沒有provenance，禁止當作已核可reference或未知資料fallback。

下一個來源核驗任務先保存5–10筆官方完整小樣本與核驗報告，不下載全量。
再提出最小只讀snapshot loader：不查照片、不換份量、不算熱量，保留原值及
缺失狀態、回傳逐条錯誤；測試零／空白／LOQ、raw/cooked、100ml／100g、
多分析項同ID、未知單位、版次更新、hash不符、顯名缺失及無正式資格。
TFDA資源可取得性與現行欄位定義未確認前，正式Adapter仍是待辦。
