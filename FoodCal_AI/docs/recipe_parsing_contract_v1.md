# Recipe Parsing Contract v1

狀態：設計規格，尚未實作 Adapter 或正式驗證器。基準為 checkpoint `61b93d4` 的 Retrieval API、實驗七欄 schema 與五案例評估。本規格沒有改寫原案例、參考答案、實驗結果或公開 API。

完整 JSON 設計範例：`examples/recipe_parsing_contract_v1_cases.json`，含 case_01～05，每筆可獨立讀取。它們是依原文建立的**契約預期範例**，不是新的 Gemma 輸出，也不是既有結果的靜默補正。特別是 case_05 的群組混合步驟，是新設計示範；原實驗的三個 null 仍維持 needs_review。

## 1. 邊界與資料流

```text
Retrieval candidate + caller-provided source text
  → independent parsing result (this contract)
  → structural/evidence checks + separate semantic review
  → reviewed candidate information; still a recipe prior
```

一次解析只處理一份食譜文字快照，不解析整張圖片的多個候選。多物件／多候選由呼叫端分別保存 binding，不能把相同類別或重疊偵測合併成一份實際食材／份量。食譜文字的 quantity 是原文食譜用量，不是圖片重量。

不產生營養、熱量、圖片份量或來源核可狀態；不引入特定 LLM、網路服務或新 HTTP API。穩定 ID、hash、binding、驗證狀態由可信任的程式建立，不讓模型任意宣告通過或 approved。

## 2. 結果外層

v1 所有下列欄位均必要。各型別拒絕額外欄位；不能把 Retrieval schema_version=1 當成本契約。未來變更契約版本另行明訂，不默默接受未知重大版本。

| 欄位 | 型別／值 | 語意 |
| --- | --- | --- |
| contract | 固定 `foodcal.recipe_parsing` | 契約命名空間 |
| schema_version | 整數1，非bool | 與舊七欄陣列明確區別 |
| input_id | 非空字串 | 呼叫端食譜快照識別，不是 detection_id |
| status | parsed / partial / unavailable / failed | 由驗證與錯誤政策導出，見第7節 |
| review_status | unreviewed / needs_review / approved | 解析審核狀態，與來源審核獨立；只有人工流程能給approved |
| source | Source或null | 只於unavailable/failed可為null |
| retrieval_binding | Binding或null | standalone合成實驗為null，不偽造catalog身份 |
| provenance | Provenance | 解析器／輸出來源紀錄 |
| ingredients | Ingredient陣列 | 食材宣告，不儲存preparation_method |
| preparation_steps | PreparationStep陣列 | 一個動作一筆，單項或群組共用同一結構 |
| evidence | Evidence陣列 | 逐字原文片段的登錄表 |
| issues | Issue陣列 | 缺失、未解析、關聯不明與失敗原因 |

Source欄位：`source_id`、`reference`為呼叫端提供的非空字串；`kind`為production/test_fixture/unverified；`review_status`為approved/test_only/unreviewed（合法配對分別為production:approved或unreviewed、test_fixture:test_only、unverified:unreviewed）；`text`為不可變非空原文；`text_sha256`為64字元小寫十六進位，等於SHA-256(text.encode('utf-8'))。這是解析用文字hash，不是原始檔案bytes hash。檔案BOM或解碼前bytes若需稽核，由外部audit保存。

原文以接收到的Unicode字串為準，不先strip、轉簡繁、修改標點／空白或做Unicode正規化再定位。只能在建立快照前由呼叫端明訂解碼方式；不同文字就形成不同快照。`reference`用公開URL或repository相對locator，不放使用者home、私人絕對路徑或credentials。來源metadata是宣告，不證明授權／真實性。

Binding欄位：`source_id`、`recipe_id`、`catalog_sha256`（皆非空，hash格式如上）。三者由原Retrieval候選與catalog snapshot複製；source_id必須等於Source.source_id。此binding只是銜接原候選，不表示解析完成或食譜吻合圖片。不存在可信catalog時保持null。不同偵測物件共享同一候選解析時，物件與解析結果的關聯由呼叫端另存，不把confidence/retrieval_score合成解析機率。

Provenance必要欄位：`parser_id`非空字串、`parser_kind`為model/manual/legacy_import/design_example；`model`為null或`{identifier,digest}`（非空字串，digest可null表示未取得）；`prompt_sha256`與`raw_output_sha256`為null或64字元hash；`settings`為JSON object。model模式必須有model.identifier及實際settings；無法取得digest/hash時填null並記issue，不虛構。原始thinking與reply另存受控audit，不在此契約散佈私人log。design_example使用model=null、settings={}，不能冒充模型推論。

## 3. 食材 Ingredient

| 欄位 | 型別／要求 |
| --- | --- |
| ingredient_id | `ing_<start>_<end>`；快照內唯一，見第5節 |
| anchor | `{start:int, end:int}`；宣告中食材名稱的原文跨度 |
| ingredient_name | 非空逐字名稱，等於source.text[start:end]，不默默改同義詞 |
| quantity | 有限、非負number或null；拒絕bool、NaN、Infinity |
| quantity_raw | 數量／模糊量詞的逐字字串或null |
| quantity_state | exact / vague / missing / unparsed |
| quantity_rule | numeric_literal / half_literal / null |
| unit | 逐字字串或null，不自行換算 |
| optional | bool；true僅於原文明示可選 |
| optional_state | explicit_optional / explicit_required / not_marked / unresolved |
| uncertainty | bool；食材欄位缺失或不明的保守旗標，不是機率 |
| evidence_links | `[{evidence_id, supports:[role,...]}]`，非空 |

Ingredient的合法role為name、quantity、unit、optionality。名稱必須有name證據；非null quantity_raw、unit與true optional必須各有對應證據。證據支持的是原文主張，不能以檢查通過證明模型沒漏掉其他資訊。

數量政策：

- exact：quantity為number，quantity_raw非空；numeric_literal只解析明示數字，half_literal只將明示「半」表示為0.5。quantity_rule是可稽核的文字解析規則，不是LLM估量。半顆保持unit=顆，不轉為克。
- vague：quantity=null，quantity_raw為適量／少許等原詞，quantity_rule=null，uncertainty=true。為沿用既有語意，unit也可保存「適量」「少許」，但它是模糊量詞，不是可換算的物理單位。
- missing：quantity=null、quantity_raw=null、quantity_rule=null、uncertainty=true。unit若也未明示為null，不補0或預設g。
- unparsed：看到數量但v1無法安全表示，如「1～2」「一碗多一點」或互相矛盾用量；quantity=null，quantity_raw保留原文，quantity_rule=null，uncertainty=true，必須warning issue並使結果partial。區間不取中點或上／下限。

精確數量也可unit=null，表示原文或解析未提供單位；uncertainty=true並記issue，不能假設單位。明示0是有效數值，缺失不等於0。unit只能保存明示文字，沒有量綱轉換規則。

explicit_optional ⇒ optional=true、有明示證據；其餘狀態optional=false。not_marked表示沒有抽取到可選標記，**不代表必須加入**。unresolved表示可選範圍不清，uncertainty=true、warning且partial。explicit_required需原文明示必須的證據。optional=false本身不能用作必需性的事實。

uncertainty至少在quantity_state不為exact、unit=null或optional_state=unresolved時為true；有其他歧義也可為true，但須issues記原因。它不表示食材是否真的出現在圖片，也不因模型高confidence而變false。

## 4. 料理步驟 PreparationStep

| 欄位 | 型別／要求 |
| --- | --- |
| step_id | `step_<start>_<end>`；動作詞原文跨度，快照內唯一 |
| anchor | `{start:int,end:int}`；逐字動作詞跨度 |
| action_raw | 非空原文動作詞，等於source.text[anchor.start:anchor.end] |
| normalized_action | mix / slice / add / null |
| normalization_rule | v1.zh.mix / v1.zh.slice / v1.zh.add / null |
| ingredient_ids | 已確認作用食材ID陣列，不重複；不是複製食材物件 |
| target_status | resolved / partial / unresolved |
| uncertainty | bool；動作正規化或作用範圍不明時true |
| evidence_links | 非空，role限action、targets、context |

v1允許的規則只是狹小的初始詞彙：明示「混合」「混合而成」→mix；「切片」→slice；「加入」→add。對未列詞彙保留action_raw，normalized_action與normalization_rule均null，標warning/uncertainty=true/partial；不憑食物名稱推導動作，例如「薑片」不能自動創造切片步驟。後续同義詞／動作詞擴充需獨立規格與測試。

正規化欄位只表達上述動作類型，不編造新文字證據。「混合醃料」可供後續人工語意解釋，但不是本契約的逐字action_raw；case_05保存「混合而成」及整個群組原文上下文，再正規化為mix。

resolved要求至少1個ID且來源scope支持全部目標；partial為至少1個已知目標但另有未解對象；unresolved為空ID陣列。後兩種必須warning、uncertainty=true、status=partial。不知道對象不能猜某個食材ID，也不能用空陣列表示所有食材。

action與targets證據可同一片段，也可多段。resolved目標要逐項提供明示名稱scope或人工確認的指代表達支持；初期prototype僅接受明示名稱scope，自動處理「它們」「全部材料」等指代尚未定義。substring能確認字串存在，不能自行證明動作與名稱的語意關係。

步驟依action anchor排列以便穩定展示，**不等於執行先後順序**。v1不推導dependency graph、溫度／時間、加入目的地或中間產物。群組混合只保存一筆step，關聯三個ID，不生成三筆相同動作，也不把醃料虛構為第四種食材。單項切片用相同结构且只有牛肉ID。optional食材的存在不自動使整個群組步驟可選；條件步驟留待後續版本。

## 5. 穩定識別与逐字 Evidence

原文跨度採**Unicode code point索引、0起算、end exclusive**，對應Python字串切片，不是UTF-8 bytes或JavaScript UTF-16位置。Evidence物件必要欄位為`evidence_id`（`ev_<start>_<end>`）、`start`、`end`、`evidence_text`。同一跨度去重登錄；不同跨度即使文字相同仍是不同證據。links引用登錄表，不重寫證據。

`0 <= start < end <= len(source.text)`，且`source.text[start:end] == evidence_text`，不trim／拼接不相鄰段落／改標點。原文中不連續的語意組合，用多段links與normalized欄位表示，不能塞進evidence_text。

食材anchor使用宣告中的原名稱跨度，後續步驟的重複名稱引用同一已知ID。ID由可信程式根據跨度建立，與模型回傳陣列次序、改名或隨機UUID無關。相同文字快照及相同anchor重跑得到相同ID；全球識別使用`(input_id, source.text_sha256, ingredient_id)`。來源文字修改後建立新快照，不沿用舊參照。

同名食材的兩次不同用量宣告不可依名字自動合併；保留不同anchor的ID，作用範圍若不能判定則partial。選哪個宣告作anchor需由初期明示宣告規則及人工仲裁確定，v1不宣稱可自動解決所有重複提及。

## 6. 可程式驗證的核心 invariants

1. 契約名／版本、必要key、型別與enum嚴格檢查；int不接受bool，number有限。拒絕額外未知欄位，不把JSON可解析當作整體有效。
2. parsed/partial必須有完整Source且其hash符合原文；每筆evidence及entity anchor跨度、逐字比對及由跨度導出的ID均成立。
3. ingredient_id、step_id、evidence_id各自唯一；每個link存在且supports符合entity種類；同一link roles不重複、非空。
4. 名稱anchor被至少一段name證據覆蓋；非空數量原文／unit有相符role證據；optional=true必須explicit_optional及optionality證據。
5. exact數量有非空quantity_raw及已知rule；numeric_literal按白名單語法解析後等於quantity，half_literal只接受「半」且quantity=0.5。沒有規則或不一致就是無效，不能把錯誤值改成0。
6. vague/missing/unparsed與null、quantity_raw、quantity_rule及uncertainty的條件如第3節；非空unit必須逐字存在於unit證據。null不是invalid值，也不是已知0。
7. 每個step.ingredient_ids均存在於本快照ingredients，不重複，不能跨其他候選借用ID。target_status與ID數量／issue／uncertainty一致。
8. action_raw與action anchor、action證據一致；non-null normalized_action與normalization_rule必須成對且符合白名單。action_raw是證據，normalized_action不是證據。
9. resolved step每個目標要被targets evidence的明示名稱scope覆蓋；原型可先驗字串／跨度涵蓋，再把語意歸屬留人工審核，不能將涵蓋檢查稱為語意通過。
10. 每個parsed結果至少有一項食材；所有step target resolved且無未解error/warning。partial至少有一項食材或可保存的步驟，且必有warning。unavailable/failed的ingredients、steps、evidence全空，reason以issues保存；failed必有error。
11. review_status的approved由人工流程提供；parser/模型不得提升source.kind、source.review_status或解析review_status。schema／substring通過不等於approved。
12. 若Binding存在，source_id相符、catalog hash格式正確；身份完整性以外部原候選快照比對，不用模型產生的同名值冒充來源。

數量缺失若原文確實沒有，可保留parsed + info issue + uncertainty=true；parsed只是目前抽取內容一致而無未解warning，**不保證完整抽取或真實語意**。自動驗證與人工語意評估分列，不能把模型自行標示的parsed當作可信結果。

## 7. 未知、部分、無效與失敗政策

Issue必要欄位：`code`非空穩定字串、`severity`為info/warning/error、`path`為結果JSON Pointer、`message`非空字串。初期codes至少包括quantity_missing、quantity_vague、unit_missing、quantity_unparsed、optional_unresolved、action_unmapped、targets_unresolved、unsupported_legacy_field、coverage_unknown、source_unavailable、input_empty、invalid_json、invalid_schema、invalid_evidence、dangling_reference、incomplete_generation、timeout、cancelled。

| 情況 | 行為 |
| --- | --- |
| 原文沒有數量／單位 | null、uncertainty=true、info；不虛構數值，仍可parsed |
| 看見數量但不支援／有矛盾；動作／關聯不明 | 保留逐字原文與已驗證部分；warning、partial、needs_review |
| 有合法輸入但無可解析食材／步驟 | unavailable；空陣列、coverage_unknown等reason，不宣稱無食材 |
| 無原文／空輸入 | unavailable；source可null、source_unavailable或input_empty |
| malformed JSON、錯誤型別／證據／參照 | failed；所有輸出陣列清空、error；不靜默丟棄壞欄位後稱成功 |
| timeout、length、取消、不完整串流 | failed；不接受截斷JSON作成功資料，raw輸出留外部audit |

partial不是用來洗掉結構錯誤；可保留的未知必須用契約明訂null／狀態／issues表示。bad candidate物件隔離於audit，不暴露未驗證片段為正式結果。資料經人工仲裁後產生新解析版本，保留舊版本，不覆寫原實驗。

## 8. 五案例的設計範例

所有17項食材JSON及證據hash／跨度見配套JSON；沒有硬編假的hash。範例中Source為test_fixture/test_only，Provenance為design_example，review_status=unreviewed。

| 案例 | 食材數量／單位 | 步驟設計 |
| --- | --- | --- |
| case_01_clear_quantities | 雞胸肉200g、鹽2g、黑胡椒1g、橄欖油10ml | 無明示料理動作，steps=[]；不從菜名推測煎／炒 |
| case_02_vague_units | 薑片null/適量、蔥花null/少許、醬油2/大匙 | vague食材uncertainty=true；「準備」不當成加工步驟，「薑片」不推導切片 |
| case_03_optional_ingredients | 豬肉300g、洋蔥0.5/顆、紅蘿蔔1/根 | 半→0.5可追溯；紅蘿蔔optional=true並含「可不加」證據；無明示加工步驟 |
| case_04_missing_weight | 大蒜／辣椒／豆豉的quantity、unit皆null，uncertainty=true | 明示「加入」可作一個add step關聯三食材；原文未說加入何處，不創造目的地／混合動作 |
| case_05_composite_seasoning | 蠔油1匙、米酒1匙、糖0.5匙、牛肉500g | 一個mix step關聯前三項；另一個slice step僅關聯牛肉。展示順序不宣稱執行依賴 |

case_03原參考的telefone欄位仍保留於原檔；新契約不包含它。case_04的add與case_05的mix是依原文的**設計建模範例**，不是把舊Gemma回覆補成成功。case_05逐字證據用「混合而成」及包含三食材的整句，normalized_action=mix；不把「混合醃料」寫成虛假的連續片段。

## 9. 與Retrieval及舊實驗的相容性

現有`src.recipes.retrieve_recipes(...)`回傳候選的ingredients/cooking_methods為可選字串列表，不提供完整食譜原文。Catalog中的TEST_ONLY placeholders不能拿來當食譜文本；無可信原文時解析unavailable。Retriever不呼叫parser，parser不改catalog/source宣告。

v1結果另存，不把結構化Ingredient／Step物件直接放進現有列表。未來人工審核後可有獨立、有損的字串投影，但不能丟失群組作用資訊後聲稱與v1等價，也不在本次新增投影API。未審核v1不自動成為production/approved來源。

舊七欄格式沒有版本資訊，不能直接認定為v1。未來legacy importer須明確opt-in：依原文定位名稱／用量／證據後建立新ID，保留原件；null preparation_method不表示原文沒有步驟。非null單項方法只有在動作和作用scope均可追溯時才可轉為step；不能把同名字串合併推論為群組動作。群組mix不能由case_05三個null自動恢復，須另行解析原文或人工審查。

Import結果coverage未知時為partial/needs_review；額外或無效欄位拒絕／隔離，不能默默忽略telefone等未知欄位。原實驗telefone只是參考疑慮，並未出現在當時模型輸出，不為它修改原始參考。不存在穩定跨舊格式／新格式的自動ID對照。

## 10. 未決問題與最小原型

未決：重複食材宣告與合併政策；代名詞／跨句作用scope；中間產物與條件步驟；時間／溫度／依賴關係；量詞／範圍擴充；Unicode跨語言offset交換；正規化詞彙及人工覆核流程。v1以明示名稱和少數動詞規則限制範圍，unsupported必須visible，避免額外抽象先佔位。

下一任務最小範圍：獨立parser結果資料型別與純離線validator；由呼叫端輸入text/source/binding；注入mock或保存回覆，不先連Ollama；可信程式計算hash、span、ID與驗證狀態；回傳parsed/partial/unavailable/failed及issues。保留raw audit reference但不把私人路徑帶入可分享結果。由既有Retrieval候選顯式交接，無catalog寫回、UI或營養欄位。

建議測試：五範例正向驗證；ID在輸出重排後不變；相同詞不同跨度／同名不同宣告；group step不複製成三步；dangling/duplicate/cross-snapshot ID；偽證據／offset越界／emoji與CRLF；NaN/Infinity/bool數量；missing不變0；半顆規則與unit保持；optional範圍不明；unsupported range/action回partial；timeout/length/無效JSON回failed；source approval不被模型提升；原Retrieval字串契約與fixture opt-in仍不變。

另列原文與語意評估：名稱／數量／單位／optional、步驟動作、target scope、證據字串、正規化與群組步驟漏抽各自統計；任何substring／schema通過均不可當作語意正確。五筆合成測試只有設計示範，不能宣稱正式食譜可靠性；尚無已驗證production來源。
