# FoodCal AI

**Understand the meal, not just the label — 拍一張照片，看懂這餐的熱量。**

FoodCal AI 是國立清華大學 I2P(I) Group 1 的期末專案。校園餐廳的餐點沒有營養標示，FoodCal AI 讓學生拍下自己的餐盤，就能估計這一餐的熱量和營養素。

## 怎麼運作

1. **拍照**：學生證背面朝上放在餐盤旁，拍一張俯視照（可以再加一張側面照，份量會更準）。
2. **辨識菜色**：Google Gemini 認出每一道菜、看得到的食材和烹調方式。使用者同意後照片才會送出，學生證會先塗掉。
3. **估計份量**：用學生證當比例尺量出每道菜的大小，再用「體積 × 密度」換算成克數。
4. **估計營養**：食材的營養值取自衛福部食品營養成分資料庫，算出熱量、三大營養素、鈉、膳食纖維等。看不到的油、糖和調味依烹調方式另外估計，分開顯示。

核心原則：**Recipe candidates may explain hidden calories, but are not visual proof.** 食譜只能解釋看不到的熱量，不能當作照片的證據。

網頁有繁體中文、English、日本語、한국어、Bahasa Indonesia、Tiếng Việt 六種語言。目前的數字是附上範圍的粗估，還沒有用大量秤重的真實餐點驗證。

## 程式

程式、測試與技術文件在 [`FoodCal_AI/`](FoodCal_AI/)，安裝與執行方式見 [`FoodCal_AI/README.md`](FoodCal_AI/README.md)。
