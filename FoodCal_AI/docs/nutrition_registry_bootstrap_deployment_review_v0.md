# Registry 生命週期與可信 Bootstrap 部署審查 v0

查核日期：2026-10-12；HEAD e2dbc09、branch master。
本輪只新增本設計文件；不修改 Registry schema、Provider、Validator、Aggregator 或正式 Contract。
用途仍 local_controlled/test_only、submitter_authenticated=false、nutrition_eligible=false。

## 1. 實際信任邊界與能力

查核 context_provider.py、nutrition_context_provider_v0.md、Review Validator／Aggregator 及
test_nutrition_context_provider.py 的 25 個測試。Registry 的可信程度取決於 bootstrap，
不是 Registry 內的 authority_ref、reviewer_id 或 evidence_ref 字串能自行建立。

| 邊界 | 現有機制 | 外部必要前提 |
| --- | --- | --- |
| 不可信呼叫端 → Provider | 只收 context_id、期望 recipe/candidate scope、basis_target；不能覆寫 pin/角色 | 不讓輸入配置 constructor、registry_reader、clock 或 reviews |
| Registry → bootstrap pin | 每次 SHA-256 比對；authority/profile/revision 與 bootstrap 精確相等 | pin 與 revision 由受控 operator 核對，而非从待審 Registry 自動採信 |
| Context → Registry | input/context bytes pins、完整 scope／purpose／target | 登記紀錄確實經受控人工核對；不自動新增未知 Context |
| Reviewer → grant | active、精確 attributes/rules/process、scope/source registration、時間區間 | reviewer 真實身分與授權能力由 operator 確認；ref 非人員認證 |
| Source → Context | 食品 ID／snapshot hashes／URL／取得時間／版次相等 | 官方取得鏈、授權及文件適用性另由來源登記程序保證 |
| native 附件 → review | bootstrap 與每次處理深拷貝；Validator 核對原件與值 | native 物件由受控程式重建，不接受請求 pickle 或自造 SourceLookup |
| 舊版本 →現行部署 | revision 等於 constructor 指定版本 | 沒有查詢全域最新版／持久高水位；需要啟動器與部署紀錄 |
| clock → 到期檢查 | 帶時區、instance 內不倒退、[valid_from, expires_at) | 真實時間可信度與跨重啟時間下限由部署層提供 |

Provider 回傳聚合前再讀 Registry，但只有成功的驗證／聚合路徑有第二次核對。
驗證失敗時沒有 decision，因此不能把 provider_gate=passed 單獨當成可使用結果。
當前 now 在一次操作開始時讀取；回傳前不再次檢查時鐘或有效期，不能宣稱具有即時到期原子性。
失敗 scope/hash 是完整性／授權錯誤，不等於食材本身 mismatch。

### 已確認的重播行為

本輪只在記憶體使用既有 OwnerFixture：

1. 未撤銷紀錄：passed。
2. 新 Registry revision=2 撤銷 Context，新 instance：blocked。
3. 舊 instance 仍讀取更新後的 Registry bytes：blocked（舊 pin 不符）。
4. 用撤銷前 Registry bytes、舊 pin、舊 revision 重建 instance：passed。

第 4 項不是 hash 比對失效；它顯示「合法但已過時的整套信任根」不會被 Provider 識別。
現有重播負面測試驗證的是舊 bytes 相對於新 pin 被拒絕，不證明重新配置舊 bootstrap 也被拒絕。
同一合法紀錄在同一有效政策下重複診斷可接受；重播以繞過撤銷／更新才是需要阻擋的風險。

## 2. 最小本機部署方案

採單一受控 operator、離線啟動器、版本化 Registry／證據包及受保護的 active deployment 紀錄。
不建立新帳號平台、網路服務、簽章或通用權限框架。以下檔名／格式為下一輪提案，尚未建立。

### 檔案與修改權限

| 位置（概念名稱） | 內容 | 修改者／讀取者 |
| --- | --- | --- |
| protected-control/active_deployment.json | 現行 revision、Registry pin、部署 ID、權威 ID、loader 版本／程式 pin | operator 才能更新；runtime 唯讀；request 不可指定位置 |
| protected-control/high_water.json | 已啟用最高 revision、對應 Registry pin、部署 ID、最後可信時間下限 | operator／受控啟動器；runtime/request 不可回寫或復原舊值 |
| registry-releases/revision-N.json | 完整來源、人員、grants、Contexts | operator 發布新檔；runtime 唯讀；不覆寫歷史 release |
| review-evidence/ | 不可變 input/context bytes、原始證據與重建清單 | 受控證據產生流程；登記後只讀；修改須新版本與新 pins |
| deployment-audit/ | 部署事件、前後 pins、撤銷理由與驗證結果 | operator 受控追加；runtime 不得刪除或改寫歷史事件 |
| request-workspace/ | 未信任請求或暫存輸入 | 呼叫端可寫；永不作 bootstrap 配置來源 |

以上不要求新增帳號服務。若 runtime 與不可信輸入只是資料而不是可執行 Python，
單一 operator 可維持人工管控與只讀配置。若呼叫端能任意执行程式、以同一 OS 寫入身分修改
protected-control，便無法宣稱檔案隔離有效；須以現有 OS 權限／隔離執行身分限制写入，
否則只能標示「誠實單一操作員實驗」。本輪不建立或修改 ACL，也未驗證任何主機權限配置。

把 Registry 與 pin 放不同檔案或資料夾不自動形成安全隔離；重要的是誰能修改二者及啟動器。
高水位與 active deployment 若也可被同一攻擊者一起回復，不能防完整磁碟／備份回滾。
OS 管理員、可信 operator 或程序控制者不在本方案防護範圍內。

### 初始 Bootstrap

1. Operator 確认受審程式版本、來源取得／授權證據、reviewer 與 process 登記，不採信 model 的 approved。
2. 使用受控 evidence loader 建立 RegisteredReview：Parsing 與 expected_source 經既有驗證；
   Candidate 由既有流程取得；TFDA 原件由既有固定 Source Adapter 核對。沒有新下載或名稱匹配。
3. 檢查全套 Registry：必要引用、每個 grant、日期、scope、snapshot 與全部登記 Context，
   不只依賴某一次 review_registered 被動檢查被用到的紀錄。
4. Operator 對確定的 UTF-8 bytes 計算 hash 並核對內容，人工批准 revision=1 與部署 ID。
   計算 hash 本身不提供認證；可信的是操作員和受保護的配置發布過程。
5. 把批准的 pin、revision、authority 及相對 release reference 發布至固定 protected-control，
   初始化高水位。不存在控制紀錄时停止，不從掃到的最高檔名或 Registry 自我宣告自動初始化。
6. 啟動器只讀固定受控 root；路徑解析必須留在批准 root，拒絕任意 request/env/CWD 覆寫或連結逃逸。
7. 載入 active deployment 與高水位，核對 revision/pin/部署 ID 一致，建立唯一可路由的 Provider。
   啟動器內注入可信 clock 與 reviews；對外只暴露既有 public review API。

Registry release 沒有必要加新欄位；deployment metadata 存 sidecar，不覆蓋現行 exact-keys 規則。
native Candidate 尚無標準序列化：下一輪需受控 resolver，先支援 synthetic bundle。
不以 repr/pickle hash 當成證據；未知 loader 或无法安全重建附件即 blocked。

## 3. 授權與版本生命週期

| 事件 | 最小規則 |
| --- | --- |
| 新增 reviewer | Operator 確認識別對應與身分證據 ref；另建立明列屬性／規則／用途／scope 的 grant；不因有 ID 取得全域權限 |
| 增加／縮減權限 | 新 revision，記錄 grant 變更理由及批准者；不要默默改現行 release；scope 擴大須逐項核驗 |
| 到期 | 不自動展延；[start,end) 的 end 即阻擋；期限／身分／時間未知也阻擋 |
| 延長 grant | 明確新版本與審查理由；新 grant 的時間亦需涵蓋 reviewed_at；若不涵蓋，重新人工審查產生新 Context，不回填舊 reviewed_at |
| 撤銷 reviewer/grant/source/Context | 新 revision 記錄狀態及理由；停止接收新查核並替換唯一 runtime，保留原始證據 |
| Source 更新 | 同食品 ID 但任一 snapshot hash／官方版次改變是新 registration；不移轉舊 Context／grant |
| Parsing 更新 | input_id、ingredient_id、原文 hash 任一改變需新 Context scope；不同原文不能沿用舊審查 |
| 規則／程式更新 | 部署 sidecar 記錄 code/profile/rule 版本／pins，重跑離線驗收；未知版本不能默默採用 |
| 恢復舊程式行為 | 採新 revision 的受控修復，不切回撤銷前 policy；有安全理由解除撤銷須明確新批准事件 |

現有 Provider 的 grant 清單有保守行為：同 reviewer 的已引用 grant 若撤銷或過期，会先阻擋，
即使後面另有有效 grant。因此發布前檢查每個引用的生命週期，不能只確認「至少存在一個可用 grant」。
不在本輪調整其聚合或授權演算法。

### 最小更新與失敗處理

1. 在不提供查核的受控流程生成 revision N+1，保留 N 原件與差異審查紀錄。
2. 核對新 pins／引用／來源／期限、執行合成驗收，拒絕 revision 重複、下降或同 revision 換 pin。
3. 停止入口並等待正在進行的操作完成；撤銷在部署切換完成後生效。
   不宣稱可使切換前已回傳的結果自動失效；後續使用也不得將其當永久核可。
4. 發布新 release，先提高受保護高水位，再替換 active deployment，使用完整檔案替換避免半寫內容。
   不假設兩份 sidecar 更新是同一原子交易；兩者不一致時啟動／查核必須 blocked。
5. 清除應用程式對舊 Provider 的路由／cache，重新建立新 instance，核對啟動 gate 與撤銷案例後恢復入口。
6. 若中途失敗，保持停用；不可自動回退舊 Registry/pin。Operator 恢復同一批准新版本或發布更高修復版本。

舊 instance 若 reader 固定指向舊 release，現有 Provider 可繼續接受舊資料。
部署層必须禁止保留可呼叫的舊入口；僅生成新 instance 而不清除路由不足。
revision N+1 不是由 Provider 自動判斷大於 N；目前它只檢查等於 constructor 的指定版本。

## 4. 時間與防回滾邊界

啟動器提案：檢查現行 revision ≥ 持久高水位，且同一 revision 的 pin 必須相同。
正常部署更嚴格要求 active deployment 與高水位完全一致；缺少／損毀／不一致均停止。
首次啟動不能自動把舊設定設為新高水位；備份恢復需 operator 核對現行部署紀錄与撤銷事件。

Provider 的 _last_time 只活在 instance，重建後清空；初次注入的時區合法日期即使是假時間，
也可能落在舊 grant 的期限內。部署層需受控 OS clock、啟動時的可信時間確認，
以及保存最近可信使用時間作跨重啟下限。時間無法確認或低於下限就 blocked。
下限只能偵測部分倒退，不能證明真实當下時間，也不能識別慢速時鐘、第一次就錯的時間或任意快轉。
runtime 不可信時，單靠它回寫時間下限不足；下一輪先以固定時間 fixtures 測規則，不聲稱時鐘認證。

開始時有效但操作期间跨過 expires_at：現有 Provider 沒有回傳前第二次時間核對。
下一輪部署 wrapper 可在交付 decision 前重新核對受控 clock 與期限；這是新 gate 提案，
尚非現有能力，且仍非跨多程序的原子授權。最低部署範圍先限單程序、循序操作、受控停機更新。

## 5. 稽核綁定（部署 sidecar 提案）

事件必須至少包含：event_id、event_type、deployment_id、policy_revision、前後 registry_sha256、
previous_revision、authority_ref、operator_ref、approved_at、effective_at、change_reason、受控證據 refs。
另記 code/loader/profile/rule 的版本與 hash、審查範圍差異、受影響 reviewer/grant/source/context IDs。
這是本機受控稽核，不宣稱 append-only 檔案有防竄改能力；保護／備份是部署責任。

每筆診斷結果的 audit 至少綁定：

- deployment_id、registry revision/hash、context_id/context 原件 hash、Review Input bytes hash；
- input_id、ingredient_id、recipe_text_sha256、完整 basis_target；
- source_registration_id、TFDA 食品 ID、archive/member/sample hashes、官方版次或 null；
- reviewer_id、process/rule/version、使用的 grant IDs、reviewed_at、查核時間與期限；
- provider_gate、Validator state／issue codes、Aggregate 狀態與固定 test_only／eligibility=false。

現有 ProviderResult 只有 context_id、policy_revision 及驗證／聚合結果，沒有 Registry hash、deployment_id
或完整授權鏈。不能宣稱已提供上述稽核。下一輪 wrapper 由受控 manifest 與 bundle 補成獨立 audit，
不修改原始模型答案、Context ledger 或原有結果型別。
audit 不保存完整 recipe/thinking、私人絕對路徑、秘密或身份文件內容；真實登記資料預設留本機，
只提交 synthetic fixtures，發布前另作隱私審查。

## 6. 離線生命週期／負面驗收

全部採 synthetic bytes、注入固定時間及臨時隔離檔案；既有項與下一輪待測項分開。
下表新 bootstrap codes 為提案，尚未實作／執行；不得以 Provider 舊測試通過宣稱部署層已安全。

| # | 操作 | 預期與層級 |
| --- | --- | --- |
| 1 | 相同合法 revision、pin、scope 重播 | 現有 Provider passed；仍 test_only、eligibility=false |
| 2 | 只修改 Registry，pin 不動 | 現有 registry_hash_mismatch，無 decision |
| 3 | Registry 與旁邊未受控 pin 一起替換 | 新 bootstrap 只讀受保護批准 pin，拒絕輸入 pin；不能自算採用 |
| 4 | 舊 bytes 對現行 pin／revision | 現有 hash 或 revision gate blocked |
| 5 | 完整舊 Registry＋舊 bootstrap 回滾至撤銷前 | 新高水位 gate blocked；現有 Provider 單獨不能防 |
| 6 | 同 revision 但不同批准 pin | 新 bootstrap_revision_collision / blocked；不能重新認定為原版本 |
| 7 | 更新中斷：高水位 N+1、active 仍 N，或半寫 sidecar | 新 deployment_state_inconsistent / blocked，不自動回退 |
| 8 | 新 revision 撤銷 reviewer／grant／Context／Source | 現有各撤銷 code；新部署確認舊 instance 不可路由 |
| 9 | 舊 instance reader 指向舊固定檔、仍保留舊入口 | 新部署 gate 拒絕非 active deployment；現有 Provider 本身可接受 |
| 10 | now 到 expires_at／reviewed_at 不在 grant 期間 | 現有 review_expired/review_not_authorized；測端點前後與時區等價 |
| 11 | 重啟後注入比最近可信時間早、但落在舊期限的日期 | 新 bootstrap_time_untrusted；現有 instance 內倒退測試不足 |
| 12 | 操作開始有效，回傳前超過期限 | 新 wrapper 交付 gate blocked；現有僅起始 now 檢查 |
| 13 | 同 TFDA ID 換 snapshot hash，或換 Parsing ingredient/text scope | 現有 source_version_mismatch／registered_scope_mismatch；不沿用舊 grant |
| 14 | pin 正確但 native resolver／程式版本不在批准 manifest | 新 deployment_code_untrusted / blocked；不使用 pickle fallback |
| 15 | PDF 必要證據、缺 attachment、偽造 reviewer | 既有 Validator／Provider fail-closed；Bootstrap 不繞過 |
| 16 | request 指定 env/CWD/相對逃逸路徑或 link 指向外部 bootstrap | 新 controlled_path_violation / blocked；受控 root 不由輸入設定 |

額外測高水位缺檔、損毀與備份恢復；都必須停止，不建立新的自動可信初始版本。
OS ACL 有效性／operator 身分真實性屬人工部署驗收，合成單元測試不能代替。

## 7. 最小下一輪與未提供保證

可以進入**獨立 Bootstrap 原型**，限制於：固定受控 root、synthetic release/bundle resolver、
active manifest＋高水位檢查、注入 clock 下限、單程序入口與最小 audit；直接沿用現有 Provider。
所有新欄位先在獨立部署 profile 定義，不修改正式 Contract；不導入帳號／簽章／網路。
真實 OS 保護尚未驗證前，仍只稱 local_controlled/test_only。

不提供的保證：人員／提交者認證、惡意 operator/管理員防護、整機備份回滾防護、
任意 Python caller 隔離、可信時間認證、跨程序同步撤銷、原子到期核可、native 標準認證、
官方文件真實性自動核驗、營養資格／質量換算／熱量計算。
Source found、Registry 授權與 Validator compatible 均不把 nutrition_eligible 改 true。

本輪驗證只包含既有 Provider／Review 離線測試與第 1 節記憶體觀察；
沒有實作或執行上述新部署 gate，也沒有變更主機權限或原始證據。
實際指令 `python -B -m unittest tests.test_nutrition_context_provider tests.test_nutrition_review -q`：
52 項全部通過；本輪未重跑完整主測試或 Gemma 實驗測試。
