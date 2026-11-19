# 易用性測試 v0（評分表第 4 項，15 分）

狀態：2026-11-19 測試流程、問卷、記錄範本與計分程式完成（`src/evaluation/usability.py`、`tools/score_usability.py`、
`tools/measure_latency.py`、`tests/test_usability.py`）；**還沒有找人測試**。

## 1. 受測者與記錄

- ≥ 5 位**不是組員**的校內學生（常用的 5 人原則）。少於 5 人時 4a、4b 記 0。
- 只記匿名編號（P1、P2…），不記姓名、學號或照片中的人臉；記錄檔放在 Git 忽略的 `work/`。
- 開始前說明：測的是網頁，不是受測者；可以隨時停止；過程中邊做邊說出想法，主持人不提示。
- 準備：一台跑 `streamlit run src/ui/app.py` 的電腦（「上傳照片辨識」需本機權重；沒有權重就用「合成展示」並在報告註明），
  三張校園餐照片，手機一支（第 4d 項第 ③ 條）。

## 2. 任務與成功標準

| 任務 | 指示（照念） | 算「完成」的條件 |
| --- | --- | --- |
| T1 | 「請用這個網頁分析這張餐點照片。」 | 自己選對模式、上傳、按下推論並看到結果摘要；記錄從開頁到看到結果的秒數 |
| T2 | 「這餐的熱量結果是什麼？代表什麼意思？」 | 說出結果（目前多為「無法估算」或 kcal 範圍），並正確說明範圍只含看得到的食物或無法估算的原因之一 |
| T3 | 「為什麼某樣東西沒有算進熱量？」 | 指出畫面上的中文原因，意思正確 |
| T4 | 「請把這次的照片和結果清掉。」 | 自己找到並按下「清除本次圖片與結果」 |

每個人×任務記一列：`completed`（自己完成）、`assisted`（需要提示）或 `failed`。只有 `completed` 算成功。
範本：`docs/examples/usability_tasks_template.csv`。

## 3. SUS 問卷（任務完成後填，1＝非常不同意，5＝非常同意）

1. 我想我會願意經常使用這個系統。
2. 我覺得這個系統過於複雜。
3. 我認為這個系統很容易使用。
4. 我想我會需要技術人員的協助才能使用這個系統。
5. 我覺得這個系統的各項功能整合得很好。
6. 我覺得這個系統有太多不一致的地方。
7. 我想大部分的人能很快學會使用這個系統。
8. 我覺得這個系統使用起來很笨拙。
9. 我對使用這個系統很有信心。
10. 我需要先學很多東西才能開始使用這個系統。

計分：奇數題（答案 − 1）、偶數題（5 − 答案），加總 × 2.5，得到 0–100（業界平均約 68）。範本：`docs/examples/usability_sus_template.csv`。

## 4. 反應時間與檢查表

```bash
.venv/bin/python -B -m tools.measure_latency --image <校園餐照片> --runs 20   # 需本機權重；第一次載入模型另列
.venv/bin/python -B -m tools.score_usability --sus <sus.csv> --tasks <tasks.csv> --latency-p95-seconds <p95> \
  --check no_raw_json_on_main_screen --check chinese_reasons_and_next_steps --check phone_width_t1_without_side_scroll
```

只對**實際確認通過**的檢查項加 `--check`：

| 檢查項 | 怎麼確認 |
| --- | --- |
| no_raw_json_on_main_screen | 不展開「開發者細節」時，主畫面沒有 JSON 或程式欄位名 |
| chinese_reasons_and_next_steps | 無法估算與錯誤時，畫面有中文原因與下一步 |
| phone_width_t1_without_side_scroll | 手機（約 390 px 寬）連到同一網路的 Streamlit，完成 T1 不需左右捲動 |

手機連線：`.streamlit/config.toml` 預設只接受本機（127.0.0.1）。測試時在**可信任的網路**上暫時執行
`.venv/bin/python -m streamlit run src/ui/app.py --server.address 0.0.0.0`，手機開 `http://<電腦 IP>:8501`，測完立刻 Ctrl+C。
服務沒有登入保護，不要在公共網路開放；設定檔本身不要改。

## 5. 給分（同評分表）

| 子項 | 給分 |
| --- | --- |
| 4a SUS 平均（5） | ≥80：5；≥68：3；≥51：1 |
| 4b 任務成功率（4） | ≥90%：4；≥75%：2 |
| 4c 速度（3） | T1 中位數 ≤ 60 秒、p95 ≤ 5 秒：兩項 3、一項 1 |
| 4d 檢查表（3） | 每項 1 分 |

修正辨識結果的功能尚未實作，所以不列為任務；之後加入時再新增 T5。
