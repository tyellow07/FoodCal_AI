# FoodCal AI

**Understand the meal, not just the label — one photo shows what this meal adds up to.**

Campus cafeteria meals come without a nutrition label. FoodCal AI turns a photo of your plate into an estimate of its calories and nutrients: Google Gemini recognises the dishes, your student card sets the scale for portions, and the Taiwan FDA food composition database gives the nutrition. It is the final project of Group 1 in the I2P(I) course at National Tsing Hua University (NTHU).

This guide shows how to take a good photo, how to use the page, and how to run it on your own computer.

## 1. Take the photo

You need your meal and your student card.

1. **Put the student card next to the plate, back side up.** Lay it flat on the table, beside the plate or bowl rather than on top of the food. The card's known size (85.6 × 54 mm) turns pixels into centimetres. It can lie at any angle.
2. **Shoot from straight above.** Get the whole meal and the whole card in the frame, and keep faces out of the shot.
3. **Optional: add a side photo.** A top-down photo only measures area. A second photo from the side measures how tall each dish is and how full the bowls and cups are. Crouch so the camera is about **15–35° above the table**, keep the card flat and back up, with its long side facing you.

## 2. Use the page

1. **Pick a language** in the top-right corner: 繁體中文, English, 日本語, 한국어, Bahasa Indonesia or Tiếng Việt.
2. **Upload the top-down photo.** On a phone you can take it directly from the upload box. If sample photos are set up, you can press **Use this photo** under one of them instead.
3. **Add the side photo if you have one** (optional).
4. **Tick the consent box and press Start recognition →.** Nothing leaves your device before you agree. Only a downscaled copy without camera data (including GPS) is sent to Google Gemini, and the student card is painted over first. Recognition usually takes 5–30 seconds.
5. **Check the student card.** A green outline marks the card, and the caption under the photo says how it was found:
   - matched the whole card back, the printed text, or the EasyCard (悠遊卡) text and logo, which are the most reliable;
   - or guessed from the card's shape, in which case a note asks you to check it.

   If the outline is wrong or missing, press **Mark the student card** (or **Re-mark the student card**) and **click the card's four corners** in any order. A tilted card is fine. Drag a corner to adjust it, or press **Start over**. The outline applies after the fourth corner.
6. **Check the dishes.** Each dish has a number on the photo and a row with its ingredients and an estimated weight.
   - **Wrong dish**: tick it to leave a misrecognised dish out (untick to bring it back).
   - **+ Add a dish**: pick a dish Gemini missed, then drag a box around it on the photo (for a bowl, box the whole rim).
   - **Grams**: type a weight if you know it; it replaces the estimate.
   - **Show boxes**: turn the recognition boxes on the photo on or off.
7. **Read the nutrition panel.** It shows the calories with a range, then protein, carbohydrate and fat, then other nutrients such as saturated fat, sugar, sodium and dietary fibre.
   - **Orange lines** are oil, sugar and seasoning that the photo cannot show, estimated from how each dish is cooked. They are kept apart from what the photo shows, because a recipe can explain hidden calories but is not visual proof.
   - **"≥"** means some ingredients lack that value, so the real amount may be higher.
   - The table at the bottom lists each dish's calories, split into what the photo shows (Photo) and what the recipe estimate adds (Recipe).
8. **Start again** with **Choose another photo**.

No API key yet? Choose **Synthetic demo (no model)** at the top of the page and press **Show the demo result** to see a built-in test case (not a real photo analysis).

## 3. Run it on your own computer

You need Python 3.12 and a free Gemini API key from [Google AI Studio](https://aistudio.google.com/).

**macOS / Linux**

```bash
git clone https://github.com/tyellow07/FoodCal_AI.git
cd FoodCal_AI/FoodCal_AI
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-ui.txt -r requirements-vision.txt
.venv/bin/python -m streamlit run src/ui/app.py
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/tyellow07/FoodCal_AI.git
cd FoodCal_AI\FoodCal_AI
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-ui.txt -r requirements-vision.txt
.\.venv\Scripts\python.exe -m streamlit run src/ui/app.py
```

Then open the address it prints (usually http://localhost:8501).

**Gemini API key.** Create `FoodCal_AI/.streamlit/secrets.toml` with this line, or set the `GEMINI_API_KEY` environment variable instead:

```toml
GEMINI_API_KEY = "your key"
```

The file is ignored by Git, so the key stays on your computer.

**Automatic card finding (optional).** Without setup, the page still guesses the card from its shape, and you can always mark the four corners yourself. For reliable automatic finding, give it one photo of your card's back, cropped to the card edges, plus a crop of the EasyCard logo from that same photo:

```bash
.venv/bin/python -m tools.setup_card_reference --logo logo.png --card-photo card_back.jpg
```

It stores a grey copy of the card back with the card number painted over, in the Git-ignored `data/card_reference/` folder. A card from another class year can be added with `--card-photo <that card back> --design <name>`.

**Tests**

```bash
.venv/bin/python -m unittest discover -s tests
```

## Limits

- The results are **rough estimates with a range**. They have not yet been checked against many weighed meals, and they are not medical or dietary advice.
- Portions come from each dish's size on the photo and typical depths of plates and bowls. Food hidden under other food, or a very full bowl, can be off. A side photo helps.
- The card must lie on the same table as the food. A card held in the hand or propped up gives the wrong scale.
- Gemini's free tier allows Google to use what is sent to improve its products, which is why the page asks first and paints the card over.

## Code

The code, tests and technical documents (in Chinese) are in [`FoodCal_AI/`](FoodCal_AI/). [`FoodCal_AI/README.md`](FoodCal_AI/README.md) records the development history and the full setup notes.
