# Ingredient Identity Review / Nutrition Basis Review Contract v1（草案）

日期：2026-10-12；HEAD e2dbc09。設計文件，尚無型別／Validator／審查引擎實作。
兩項審查分開產出，不修改Candidate Lookup、Parsing／Mapping Contract或Source回傳。
依據：[Identity Policy](ingredient_identity_policy_v1.md)、[Nutrition Policy](nutrition_reference_policy_v1.md)、
[Candidate Lookup](nutrition_candidate_lookup_v1.md)及本機八筆原始小樣本。沒有新的官方查詢或政策授權。

## 1. 範圍與獨立結論

本v1用途固定 `diagnostic/test_only`，`nutrition_eligible=false`，即使所有屬性verified也如此。
Source found、link_check verified、Mock matched、Parsing parsed、caller說approved，均不是本審查的核可。
`identity_decision`只描述特定食材與特定條目的身分相容性；`basis_decision`只描述指定營養基準用途。
兩者compatible也不代替授權、資料完整性、上游人工審查或照片份量等正式資格gate。
原資料與所有原狀態保留；本文件的結論作獨立sidecar，不回填Candidate identity_status／basis_status。

## 2. Review Input（最小結構）

所有欄位必要；明確可null處除外。不接受額外approval／eligible欄。完整附件由呼叫端提供，
不讀模型指定的檔案／URL。引用缺少附件即invalid，不連網補資料。

| 欄位 | 型別／規則 |
| --- | --- |
| contract / schema_version | `foodcal.ingredient_source_review` / 整數1 |
| purpose | 固定 `diagnostic/test_only` |
| recipe_scope | input_id、ingredient_id、recipe_text_sha256；須等於已驗證Parsing附件。hash為UTF-8原文SHA-256 |
| candidate_scope | provider=`tfda`、dataset=`8543`、food_id、original_name、data_type、archive_sha256、member_sha256、sample_sha256；不得用synthetic entry_id替換food_id |
| attachment_refs | parsing_snapshot、expected_recipe_source、candidate_lookup、source_provenance及證據原件附件清單；每項含attachment_id、representation（bytes/native）、sha256。bytes需原件SHA，native只能null，須按下文核對，不冒充原件hash |
| basis_target | 固定 `recipe_declared_ingredient_reference`；不是照片計算。required_nutrients為original_name＋expected_unit的非空清單，original_name唯一；不得默選熱量或修正熱量 |
| claims | Claim陣列，見下文；缺少某屬性claim表示missing，不補常見值 |
| evidence | Evidence陣列；evidence_id唯一，引用必須存在且屬同一scope |

附件格式：既有完整Parsing、可信expected_source、CandidateLookup及Source原件／provenance。
Parsing及expected_source可為native，必須由可信resolver核對完整Parsing Contract、expected_source相等及原文hash。
CandidateLookup可為native，須核對全部scope、request與Source逐列證據到獨立提供的原件，不把link_check字樣當證明。
source_provenance、實際sample／官方文件等證據原件必須為bytes附件，並核對固定pin及原始物件hash。
Python含Decimal的附件不能用repr或浮點化JSON作穩定hash。沒有原件bytes的native物件不能作Evidence原件。
本v1只定義Python native引用與原件bytes，不要求新JSON編碼器；若日後保存衍生JSON，另定可重現編碼。
候選必須source_status=found、link_check=verified且唯一精確條目；歧義保留待審查，不選第一筆。

Claim欄位：`claim_id`（唯一）、`domain`（identity/basis）、`attribute`、`nutrient_key`、`side`（recipe/source）、
`value_state`（known/missing/unknown）、`value_kind`（text/text_list/decimal）、`value`、
`unit`（string或null）、`evidence_ids`（唯一ID清單）。
known必須有值及證據；decimal以有限非負ASCII十進位字串保存，原值另留在Evidence。
missing／unknown的value為null，分別表示沒有提供及有資訊但無法確定；unknown須有原因證據。
nutrient_key僅在nutrient_units／nutrient_definition使用required_nutrients的original_name，其餘為null；不發明官方營養素ID。
重複同一domain/side/attribute/nutrient_key的claim不合併，先判輸入invalid；多個競爭解釋須放入一筆claim的證據／待審查原因。
processing的text_list空清單不能宣稱已確認「無加工」；需明確文字證據或保持unknown。
text的unknown字樣不是有效已知值，不能把synthetic詞彙當作正式食品規格。

## 3. Evidence、優先序與审查执行上下文

| Evidence欄位 | 定義 |
| --- | --- |
| evidence_id / origin | 唯一ID；origin為official_record、official_document、recipe_text、caller_assertion、unverified_claim |
| attachment_ref / attachment_sha256 | 指向已核對附件；hash不符不能用此證據產生verified |
| locator | official row使用sample_filename、source_row_index、source_object_sha256、field_name；recipe_text使用start/end（Unicode code point、零起算半開區間）；文件使用page/section |
| raw_value | 原始字串／null／JSON scalar；不trim、不更名、不補值。包含數字0與字串零時保留型別 |
| evidence_text | 原文文字片段或null；文字跨度必須精確等於附件原文切片；raw_value須等於原field |
| statement_role | food_identity、sample_preparation、recipe_processing、measurement_basis、version、nutrient_definition或caller_claim；不可混用角色 |

來源類型由可信resolver按附件來源判定；輸入自稱official_record或提供同樣URL不會升級證據。
證據比較以**聲明範圍**為先，並非一條「官方勝過原文」的全域排序：

1. 對TFDA樣品欄位：hash核對的official_record直接聲明優先；適用同資料版次的official_document提供基準／定義。
   歷史文件只證明歷史說明，不推定目前每條樣品適用。兩者有矛盾時停留unresolved並列conflicting_evidence，不默選一方。
2. 對食譜宣告：hash與跨度核對的recipe_text是原文證據；caller_assertion可補候選解釋但不是原文事實。
   官方樣品資料不能覆蓋食譜本身；生／熟兩邊明確不同，才是mismatch。
3. caller_assertion始終標為呼叫端聲明。僅有此聲明、evidence_reference非空或聲稱approved，不能verified。
   要升為已核對屬性，須由審查者核對原始證據及適用規則，保留此核對的來源。
4. unverified_claim（包含模型推論）只能列待核驗；不支持verified或not_applicable。

審查執行context由系統另外提供：reviewer_ref、method（manual/controlled_rule）、rule_id及rule_version、reviewed_at。
manual使用rule_id=`manual_evidence_comparison_v1`；controlled_rule需明確規則版本。
字串reviewer_ref不是認證；無可辨識且受控的執行context，輸出不能被當已核可結果。
本輪不設計通用角色／簽核registry。相容性結果仍test_only，未授予正式資格。

## 4. Ingredient Identity Review

每個食材／候選恰有下列五個Attribute Review Result，不能略去未知欄。

| attribute | 核對內容 | TFDA可用原欄位／限制 |
| --- | --- | --- |
| food | 食品種類、必要物種／產品身分 | 樣品名稱、俗名、食品分類、內容物描述；同名／別名只發現候選 |
| part | 部位／全體或成分，如胸、全蛋／蛋白 | 名稱／描述可能明示，無獨立部位欄；整合編號不當部位證據 |
| state | 相關raw/cooked/as_sold狀態 | 部分描述有「樣品狀態:生」，無獨立生熟欄；沒寫不預設生 |
| processing | 對身分有影響的去皮、去骨、乾燥、油炸等 | 內容物描述；sample_preparation的混合均質不等於recipe_processing |
| quality_composition | 品種、帶皮／骨、組成或樣品／平均值的適用差異 | 名稱、描述及資料類別，缺規格不填synthetic_unspecified_grade |

weight_basis放入下一部分，避免食品身分與重量用途合併。
不建立新canonical ontology。未定詞彙的跨語言、同義詞或多加工正規化須有版本化規則／人工核對，否則unresolved。
名稱寫「帶骨」、前處理寫「去骨」可能是在描述不同階段，不先當資料矛盾；未釐清階段則unresolved。

## 5. Nutrition Basis Review

每個候選必須有以下屬性，nutrient_units與nutrient_definition按required_nutrients逐項展開。

| attribute | 核對內容／禁止推論 |
| --- | --- |
| reference_measure | 原欄「每100克含量」支持100 g標籤；不因此核可可食部、乾重或熟重 |
| edible_portion | 營養資料是否適用可食部，以及與目标質量相容；廢棄率／前處理不是通用可食部核可 |
| weight_basis | 原料／成品、生／熟、乾／濕、帶骨／去骨等質量基準是否相容，與身分state審查分開 |
| recipe_declared_mass | 原quantity、quantity_state、unit及克數證據；exact 0可保留，missing/unknown不能變0；不做顆／匙／ml換算 |
| photo_consumed_mass | 本用途不審查照片份量；只可按固定purpose規則not_applicable，不宣稱已知照片重量 |
| nutrient_units | 每個指定原分析項的含量單位與目標一致；無單位unresolved，不轉g/mg/ug或kcal/kJ |
| nutrient_definition | 每項方法、推導／分析定義、缺值／零與精度是否足夠此用途；reported不等於直接量測或真零 |
| data_version | 官方版本與scope一致；null為unresolved，取得日／hash／OpenAPI版本不冒充正式版次 |

四種重量分別保留：recipe_declared_mass、100g reference_measure、edible_portion weight及photo_consumed_mass。
不從任一者推算另一者。重量0不表示不存在或確定零熱量；未知與0不同。
本v1不允许snapshot hash代替data_version verified；若未來採固定快照替代正式版次，须另提政策修訂與限制，不暗中放寬。
生熟屬性雖identity verified，重量量測時點仍可能不同；basis必須另查，不能複製前者結論。

## 6. Attribute Review Result 與組合規則

Attribute結果必要欄位：domain、attribute、nutrient_key（僅逐營養項需，否則null）、
status（verified/mismatch/unresolved/not_applicable）、reason_code、recipe_claim_ref、source_claim_ref、
evidence_ids、rule_id、rule_version、reviewer_ref、reviewed_at。缺少claim的ref可null。

| status | 條件 |
| --- | --- |
| verified | 所需附件hash／scope正確；適用證據已核對；兩邊必要聲明一致或單邊基準定義已足夠；受控執行context及規則明確 |
| mismatch | 適用且已核對的兩邊證據明確不相容；保留兩側，不默修原值 |
| unresolved | missing/unknown、證據不足、歧義、角色／文件適用性不明或矛盾證據；不是verified，也不是確定mismatch |
| not_applicable | 固定purpose及版本化規則證明不涉及该屬性；仍需證據／原因；不能因值null、缺資料或难核對而跳過 |

此profile只允许photo_consumed_mass使用not_applicable，規則為
`diagnostic_recipe_reference_excludes_photo_v1`。其餘身份及基準屬性皆必查，不允许自由選NA。
oil的part若需whole/not_applicable語意，現階段保持unresolved，待另提適用規則；不默創造核可。
缺少任一必要屬性、重複屬性／營養key、未知狀態、無證據的verified或非法NA均是結構invalid。

Aggregate Decision必要欄位：identity_decision、basis_decision、input_integrity（verified/invalid）、
blocking_reasons、attribute_results、usage=`diagnostic/test_only`、nutrition_eligible=false。
兩項decision各為compatible/incompatible/needs_review/invalid，均由完整屬性集合計算，不接受caller提供结论。

組合順序：

1. schema／參照／scope／hash無效 → input_integrity=invalid，兩decision=invalid；attribute_results為空，列具體error path。
   診斷audit可保留原件；不能流向verified、核可或計算。hash錯是完整性錯誤，不是食品不相容。
2. integrity正確後，各domain若有mismatch → incompatible；同時保留其他unresolved blocker，不掩蓋待審查。
3. 沒有mismatch但有unresolved → needs_review。所有必要項verified且僅合規NA → compatible。
4. identity compatible不推导basis compatible；basis compatible也不消除identity不符。
5. 任一invalid/incompatible/needs_review，fail-closed：無可計算條目、無自動換算／fallback；只允許診斷保留。
   本設計連兩者compatible也仍nutrition_eligible=false。正式资格須另通過来源使用／完整性gate及上游限制。

稳定reason示例：part_ambiguous、state_mismatch、processing_role_unresolved、edible_basis_unknown、
recipe_mass_missing、weight_basis_unknown、nutrient_unit_missing、nutrient_definition_unresolved、
official_version_unknown、evidence_hash_mismatch、scope_mismatch、caller_claim_only、invalid_not_applicable。

## 7. 結構示例（單項及聚合摘要，非完整Review Input）

```json
{
  "attribute": {
    "domain": "basis", "attribute": "data_version", "nutrient_key": null,
    "status": "unresolved", "reason_code": "official_version_unknown",
    "recipe_claim_ref": null, "source_claim_ref": "claim_version",
    "evidence_ids": ["ev_source_version"], "rule_id": "official_version_required_v1",
    "rule_version": "1", "reviewer_ref": "test-reviewer",
    "reviewed_at": "2026-10-12T00:00:00+08:00"
  },
  "aggregate_summary": {
    "identity_decision": "needs_review", "basis_decision": "needs_review",
    "input_integrity": "verified", "blocking_reasons": ["official_version_unknown"],
    "usage": "diagnostic/test_only", "nutrition_eligible": false
  }
}
```

示例的reviewer與時間是設計測試值，不是已完成真實審查；完整aggregate還必須含全部attribute_results及其blockers。

## 8. 八筆快照：哪些事實有提供、哪些仍不能核可

本輪只讀保存的原件，17欄與Source FIELDS一致，沒有額外官方屬性欄位。
食品ID／名稱：A0500202稉米、I0402201帶骨去皮對切胸(肉雞)、I0306101豬小里肌、
K0100101雞蛋(白殼)、E6800101絲瓜、M1100101大豆油、R4701201嫩豆腐、I04021里肌肉平均值。

| 已觀察欄位／內容 | 無法直接核可的事項 |
| --- | --- |
| 名稱、描述、分類、俗名、整合編號 | 對某食譜的物種／產品、部位、規格等價；名稱相似不是核可 |
| 六筆描述明示生（米、雞胸、豬小里肌、蛋、絲瓜、平均雞肉） | 食譜本身生熟或成品／照片生熟；描述只證明該樣品聲明 |
| 大豆油與嫩豆腐描述沒有獨立生熟聲明 | 不預設raw／cooked／as_sold；相应state仍待核對 |
| 描述去骨、去殼、去皮、混合／打碎等 | 食譜原料採購基準、實際操作、吸油／水分或損耗；混合均質不當食譜processing |
| 廢棄率：蛋與絲瓜有數字、嫩豆腐為零字串，其餘五筆null | 當前每項數值可食部適用性及照片物件可食率；null不當0，明列0不直接核可完整可食 |
| 每單位重有含克的字串；雞胸、豬小里肌、豆腐明列零字串 | 通用每顆／每匙份量、实际照片重量，或0字串是未知標記／實際零的語意未明 |
| 熱量／修正熱量kcal、粗蛋白／粗脂肪／總碳水化合物g | 默選能量定義、每項方法／零值語意、全欄完整性；P/M/S不能當单一數值 |
| 官方版次null、原件與逐列hash可追溯 | 正式釋出年份／修訂版次；snapshot完整不等於版本verified |
| I04021為樣品平均值，其餘七筆基本資料 | 平均值可替代特定樣品／品牌或與其他条目合併 |

全部八筆目前仍不能取得正式营養资格；局部屬性可保留官方明示證據，但不因此整體核可。
可食部100g的既有官方歷史文件不自動成為本快照／目標用途的當前核可證據。

## 9. 下一輪離線案例與最小實作

| 測試 | 必要fixture／操作 | 預期 |
| --- | --- | --- |
| 完全核對 | 明標synthetic/test_only，五身份及全部基準皆具匹配證據、版本已知；唯一合法photo NA | 两decision compatible，但nutrition_eligible=false；不是八筆真實資料已核可 |
| 部位歧義 | recipe part未知或有胸／腿競爭解釋 | identity needs_review，part_ambiguous；不選單一候選 |
| 生熟不符 | 原文熟、來源描述生，两側hash／證據核對正確 | identity incompatible，state_mismatch；basis另核對，不能消除冲突 |
| 可食部未知 | 廢棄率null或資料100g標籤但可食部適用性未知 | basis needs_review，edible_basis_unknown，無換算 |
| 版本未知 | official_dataset_version=null，snapshot hash正確 | basis needs_review，official_version_unknown；不把hash當版本 |
| 證據hash不一致 | recipe／sample／source_object／附件任一hash錯 | integrity invalid、两decision invalid，屬性結果空；不修補hash |
| 缺量與零 | 缺量null對比有原文證據的0g | 前者recipe_mass_missing；後者保留known零，但不自動其他属性verified |
| 前處理角色 | 去骨／混合均質仅为样品前處理 | processing_role_unresolved；不推料理熟重或損耗 |
| 非法NA／偽造approved | 缺資料而NA或caller聲稱核可 | 結構invalid或caller_claim_only unresolved；不能跳gate |
| 單位／定義不明 | 指定分析項unit=null、missing值或熱量與修正熱量混用 | 单位／定義unresolved或有明確冲突時mismatch；不補值或轉換 |

下一輪只實作本契約的資料型別、scope／證據參照與hash Validator、純狀態聚合及合成fixture測試。
不自動解釋描述、不核發正式approved、不回寫Candidate；真實snapshot的測試明確為外部前置條件。
待決：人工核對执行context的可信取得、少量屬性詞彙與正規化規則、未來衍生JSON附件編碼、
某些食品part／quality的NA例外，以及固定snapshot能否代替官方release version的另版政策。
這些未決不阻擋合成契約Validator原型，但阻擋真實自动／正式核可流程。

本輪未實作或執行新審查測試／引擎；以上為下一輪驗收案例，不引用舊測試數字宣稱契約已測。
