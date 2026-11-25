# FoodCal AI

**Understand the meal, not just the label — one photo shows what this meal adds up to.**

FoodCal AI is the final project of Group 1 in the I2P(I) course at National Tsing Hua University (NTHU). Campus cafeteria meals come without a nutrition label, so FoodCal AI lets students take a photo of their plate and get an estimate of the meal's calories and nutrients.

## How it works

1. **Photo**: put your student card, back side up, next to the plate and take a top-down photo (an optional side photo makes the portions more accurate).
2. **Dish recognition**: Google Gemini names every dish, its visible ingredients and how it was cooked. The photo is sent only after the user agrees, and the student card is painted over first.
3. **Portion estimation**: the student card works as a ruler to measure each dish, and volume × density turns its size into grams.
4. **Nutrition estimation**: ingredient values come from the Taiwan FDA food composition database, giving calories, macronutrients, sodium, dietary fibre and more. Oil, sugar and seasoning the photo cannot show are estimated from the cooking method and shown separately.

Key rule: **Recipe candidates may explain hidden calories, but are not visual proof.**

The web page comes in Traditional Chinese, English, Japanese, Korean, Indonesian and Vietnamese. The numbers are rough estimates with a range and have not yet been checked against many weighed meals.

## Code

The code, tests and technical docs are in [`FoodCal_AI/`](FoodCal_AI/); see [`FoodCal_AI/README.md`](FoodCal_AI/README.md) for setup and running.
