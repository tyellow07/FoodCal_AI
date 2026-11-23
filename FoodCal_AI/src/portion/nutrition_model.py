"""Rough nutrition per food: visible ingredients x their kcal, plus recipe-based hidden oil and sugar, kept apart.

A food is a list of components (ingredient key, share of the food's weight in percent) and a cooking method.
- Visible energy per 100 g = sum(share x ingredient kcal per 100 g as eaten). Ingredient values come from the TFDA
  food composition database (食品營養成分資料庫, 整合編號 given per row); raw meat and fish are converted to cooked
  weight with an approximate cooking yield.
- Hidden energy per 100 g = cooking-method oil and sugar ranges (from recipes and published notes) x 8.84 kcal/g
  for oil (TFDA M1100101 soybean oil, 884 kcal/100 g) and 3.84 kcal/g for sugar (TFDA N0100301, 384 kcal/100 g).
  It is skipped for ingredients whose TFDA value already includes the cooking (for example braised minced pork),
  so nothing is counted twice. Salt adds sodium, not energy.
- Density per food = sum(share x ingredient density), for converting volume to grams.
- Other nutrients health-minded eaters look at (fibre, sugar, saturated fat, cholesterol, sodium, potassium,
  calcium, iron) come from the same TFDA rows with the same conversions (MICROS). A blank TFDA cell means "not
  analysed", not zero: it is filled only from another TFDA sample of the same food, or with a structural zero
  (animal foods have no dietary fibre, plant foods no cholesterol); otherwise it stays None and the food's amount
  is flagged partial (a lower bound).
- Recipe additions follow the cooking method like hidden energy: oil adds saturated fat (TFDA soybean oil 16.1 g
  per 100 g), added sugar adds sugar, and seasoning adds sodium (SEASONING_SODIUM). Most sodium in a cooked dish
  comes from salt and sauces, so ingredient sodium alone would badly understate it.

Hidden energy and the recipe additions are a recipe prior, not something the photo shows; the page shows them apart.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP

D = Decimal
OIL_KCAL_PER_G = D('8.84')
SUGAR_KCAL_PER_G = D('3.84')
OIL_SAT_FAT_PER_G = D('0.161')  # TFDA M1100101 大豆油：飽和脂肪 16.1 g / 100 g
SALT_SODIUM_MG_PER_G = D('393')  # NaCl 含鈉 22.99 / 58.44 = 39.3%
TFDA = 'https://consumer.fda.gov.tw/Food/TFND.aspx?nodeID=178'
TFDA_OPEN_DATA = 'https://data.gov.tw/dataset/8543'

# key: (name, kcal/100 g as eaten, density g/mL, value already includes cooking, source note)
INGREDIENTS = {
    'cooked_rice': ('白飯', D('183'), D('0.79'), False, 'TFDA A0550601 白飯（熟）；密度：中短粒熟飯 1 杯約 186 g'),
    'noodles': ('麵條（熟／濕）', D('163'), D('0.59'), False, 'TFDA R2001101 衛生油麵（濕麵條）；密度：熟麵 1 杯約 140 g'),
    'instant_noodles': ('泡麵（泡開）', D('189'), D('0.6'), False, 'TFDA R2100201 泡麵 473 kcal（乾）÷ 吸水約 2.5 倍（假設）'),
    'rice_noodles': ('米粉（熟）', D('110'), D('0.6'), False, '假設值：資料庫沒有熟米粉，待補'),
    'leafy_vegetable': ('葉菜類', D('20'), D('0.65'), False, 'TFDA E5600101 蕹菜 22、E5000101 菠菜 18、E3000101 甘藍 24'),
    'gourd_vegetable': ('瓜類蔬菜', D('19'), D('0.9'), False, 'TFDA E6800101 絲瓜（生）19'),
    'pickles': ('醬菜', D('89'), D('0.8'), True, 'TFDA R4401601 醃漬花胡瓜 89、R4400401 花瓜罐頭 89'),
    'egg': ('蛋（煎／煮，不含油）', D('162'), D('0.95'), False, 'TFDA K0123101 荷包蛋（不加油）162'),
    'braised_egg': ('滷蛋', D('178'), D('1.0'), True, 'TFDA K0120102 雞滷蛋（現煮）178'),
    'tofu': ('豆腐', D('88'), D('1.0'), False, 'TFDA R4700901 傳統豆腐 88'),
    'dried_tofu': ('豆干', D('170'), D('1.0'), False, 'TFDA R4700201 豆干絲 170'),
    'chicken': ('雞肉（熟）', D('212'), D('0.85'), False, 'TFDA I0404302 棒棒腿（肉雞，生）159 ÷ 烹調產率約 0.75'),
    'chicken_cutlet': ('雞排肉（熟，不含裹粉）', D('277'), D('0.85'), False, 'TFDA I0404402 雞排（肉雞，生）208 ÷ 約 0.75'),
    'pork': ('豬肉（熟）', D('300'), D('0.85'), False, '假設值：五花與瘦肉差異大，待補 TFDA 部位'),
    'braised_pork_mince': ('肉燥', D('397'), D('0.95'), True, 'TFDA P1000301 肉燥 397'),
    'beef': ('牛肉（熟）', D('199'), D('0.85'), False, 'TFDA I0108301 牛後腿腱子心（生）139 ÷ 約 0.70'),
    'fish': ('魚肉（熟）', D('149'), D('0.85'), False, 'TFDA J0414201 高麗馬加鰆（生）119 ÷ 約 0.80'),
    'thick_soup': ('羹湯（含肉羹）', D('80'), D('1.05'), True, '假設值：TFDA R5900101 肉羹 247 約佔 1/4，其餘為勾芡湯'),
    'clear_broth': ('清湯', D('9'), D('1.0'), False, 'TFDA I0420601 雞胸骨湯 9'),
    'meatball_bawan': ('肉圓（未炸）', D('136'), D('1.0'), False, 'TFDA R0700101 肉圓（未炸半成品，不加醬料）136'),
    'pancake_skin': ('蛋餅皮', D('231'), D('0.9'), False, 'TFDA R2700301 冷凍蛋餅皮 231'),
    'batter_coating': ('裹粉', D('333'), D('0.6'), False, 'TFDA P1500201 炸雞粉（乾粉）333'),
}

# Protein, fat, carbohydrate g per 100 g as eaten (same TFDA rows as above; cooked meat = raw / the same yield).
MACROS = {
    'cooked_rice': (D('3.1'), D('0.3'), D('41.0')),
    'noodles': (D('5.5'), D('1.8'), D('31.2')),
    'instant_noodles': (D('3.8'), D('9.8'), D('21.4')),
    'rice_noodles': (D('2.0'), D('0.3'), D('25.0')),       # assumption, like its energy value
    'leafy_vegetable': (D('2.0'), D('0.3'), D('3.3')),
    'gourd_vegetable': (D('1.1'), D('0.1'), D('3.9')),
    'pickles': (D('3.0'), D('0.5'), D('18.1')),
    'egg': (D('14.0'), D('11.2'), D('1.4')),
    'braised_egg': (D('16.6'), D('11.1'), D('3.1')),
    'tofu': (D('8.5'), D('3.4'), D('6.0')),
    'dried_tofu': (D('18.3'), D('8.6'), D('4.8')),
    'chicken': (D('22.4'), D('12.9'), D('0.4')),
    'chicken_cutlet': (D('20.4'), D('21.1'), D('0.0')),
    'pork': (D('20.0'), D('25.0'), D('0.0')),             # assumption, like its energy value
    'braised_pork_mince': (D('9.5'), D('38.1'), D('4.2')),
    'beef': (D('28.3'), D('8.6'), D('0.0')),
    'fish': (D('24.3'), D('5.0'), D('0.4')),
    'thick_soup': (D('2.5'), D('3.7'), D('9.0')),         # assumption, like its energy value
    'clear_broth': (D('1.5'), D('0.3'), D('0.2')),
    'meatball_bawan': (D('3.1'), D('1.6'), D('27.3')),
    'pancake_skin': (D('5.7'), D('3.7'), D('43.7')),
    'batter_coating': (D('8.3'), D('1.7'), D('71.0')),
}

# Other nutrients: (key, name, unit, display step), in the order of the MICROS tuples.
NUTRIENTS = (('fiber_g', '膳食纖維', 'g', D('0.1')), ('sugar_g', '糖', 'g', D('0.1')),
             ('sat_fat_g', '飽和脂肪', 'g', D('0.1')), ('cholesterol_mg', '膽固醇', 'mg', D('1')),
             ('sodium_mg', '鈉', 'mg', D('1')), ('potassium_mg', '鉀', 'mg', D('1')),
             ('calcium_mg', '鈣', 'mg', D('1')), ('iron_mg', '鐵', 'mg', D('0.1')))

# Per 100 g as eaten, from the TFDA open data set (食品營養成分資料集, data.gov.tw 8543) for the same rows and
# conversions as INGREDIENTS; MICRO_NOTES says how each blank TFDA cell was handled. None = no TFDA value.
# (fibre g, sugar g, saturated fat g, cholesterol mg, sodium mg, potassium mg, calcium mg, iron mg)
MICROS = {
    'cooked_rice': (D('0.6'), D('0.05'), D('0.1'), D('0'), D('2'), D('40'), D('1'), D('0.2')),
    'noodles': (D('0.6'), D('0.72'), D('0.3'), D('0'), D('67'), D('49'), D('12'), D('0.4')),
    'instant_noodles': (D('0.72'), D('0.64'), D('4.96'), D('0'), D('830.8'), D('74.4'), D('5.2'), D('0.6')),
    'rice_noodles': (D('0.31'), None, None, D('0'), D('61'), D('2.5'), D('0.9'), D('0.3')),
    'leafy_vegetable': (D('1.93'), D('0.9'), D('0.07'), D('0'), D('72.3'), D('266.3'), D('67.3'), D('2.2')),
    'gourd_vegetable': (D('1'), D('2.1'), D('0.1'), D('0'), D('0'), D('117'), D('10'), D('0.2')),
    'pickles': (D('1.5'), D('14.5'), D('0.1'), D('0'), D('1530.5'), D('116'), D('20.5'), D('2.3')),
    'egg': (D('0'), D('0.2'), D('3.8'), D('449'), D('151'), D('139'), D('52'), D('3.4')),
    'braised_egg': (D('0'), D('4.3'), D('3.7'), D('411'), D('1278'), D('195'), D('54'), D('2.4')),
    'tofu': (D('0.6'), D('0'), D('1'), D('0'), D('2'), D('180'), D('140'), D('2')),
    'dried_tofu': (D('2.6'), D('0.4'), D('1.4'), D('0'), D('549'), D('45'), D('287'), D('6.2')),
    'chicken': (D('0'), D('0'), D('3.87'), D('117.3'), D('145.3'), D('317.3'), D('10.7'), D('1.9')),
    'chicken_cutlet': (D('0'), D('0'), D('6.67'), D('100'), D('101.3'), D('317.3'), D('10.7'), D('1.3')),
    'pork': (D('0'), D('0'), D('7.2'), D('82.7'), D('77.3'), D('421.3'), D('12'), D('1.1')),
    'braised_pork_mince': (None, None, D('14.2'), D('109'), D('941'), D('294'), D('9'), D('1.3')),
    'beef': (D('0'), D('0'), D('3.71'), D('108.6'), D('112.9'), D('425.7'), D('11.4'), D('4.3')),
    'fish': (D('0'), D('0'), D('2'), D('80'), D('57.5'), D('560'), D('7.5'), D('1')),
    'thick_soup': (D('0.23'), D('0.4'), D('1.33'), D('12.5'), None, D('25.5'), D('52.8'), D('0.3')),
    'clear_broth': (D('0'), D('0'), D('0.1'), D('3'), D('24'), D('46'), D('2'), D('0.1')),
    'meatball_bawan': (D('0.3'), D('2'), D('0.6'), D('8'), D('217'), D('49'), D('9'), D('0.9')),
    'pancake_skin': (D('1.3'), D('4.4'), D('1.4'), D('4'), D('291'), D('77'), D('15'), D('0.7')),
    'batter_coating': (D('6.2'), None, D('0.6'), D('0'), D('3790'), D('220'), D('19'), D('2.7')),
}
PLANT_NO_CHOLESTEROL = '植物性，膽固醇 0（同類較新樣品如 R4700902 傳統豆腐、E3000104 甘藍記 0）'
ANIMAL_NO_FIBER = '動物性，膳食纖維 0（同類較新樣品如 I0108302 牛腱、K01001 雞蛋平均值記 0）'
MICRO_NOTES = {
    'cooked_rice': '糖：A05002 稉米平均值 0.1 g × 41.0/77.8（生米到熟飯的碳水比例）；' + PLANT_NO_CHOLESTEROL,
    'noodles': '糖：R2000201 油麵條（乾）1.6 g × 163/361（熱量比例）；' + PLANT_NO_CHOLESTEROL,
    'instant_noodles': 'R2100201 × 0.4（泡開，同熱量）；鈉已含調味包',
    'rice_noodles': '假設：R1100301 細米粉（乾）× 110/355；糖、飽和脂肪沒有資料；' + PLANT_NO_CHOLESTEROL,
    'leafy_vegetable': ('三筆平均；E5600101 蕹菜缺的糖、飽和脂肪用 E5600102（4 月樣品），E3000101 甘藍缺的糖用 '
                        'E30001 甘藍平均值、飽和脂肪用 E3000104；' + PLANT_NO_CHOLESTEROL),
    'gourd_vegetable': PLANT_NO_CHOLESTEROL,
    'pickles': '兩筆平均；R4401601 缺的糖、飽和脂肪用 R4400401 花瓜罐頭；' + PLANT_NO_CHOLESTEROL,
    'egg': '糖：K01001 雞蛋平均值；' + ANIMAL_NO_FIBER,
    'braised_egg': '糖：K0120101 雞滷蛋（市售）；' + ANIMAL_NO_FIBER,
    'tofu': '糖：R4700902 傳統豆腐（2022 年樣品）；' + PLANT_NO_CHOLESTEROL,
    'dried_tofu': '糖：R4700202 五香豆干；' + PLANT_NO_CHOLESTEROL,
    'chicken': '÷ 烹調產率 0.75；' + ANIMAL_NO_FIBER,
    'chicken_cutlet': '÷ 0.75；碳水 0 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'pork': '假設：I03104 豬絞肉平均值 ÷ 0.75 代表豬肉；' + ANIMAL_NO_FIBER,
    'braised_pork_mince': '膳食纖維、糖沒有資料（含醬油、蔥乾）',
    'beef': '÷ 0.70；糖：I0108302 牛後腿腱子心（2022 年樣品）；' + ANIMAL_NO_FIBER,
    'fish': '÷ 0.80；碳水 0.3 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'thick_soup': '假設：R5900101 肉羹 × 1/4；勾芡湯底的調味沒有資料，鈉不算',
    'clear_broth': '碳水 0.2 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'meatball_bawan': 'R0700101 各項都有資料',
    'pancake_skin': 'R2700301 各項都有資料',
    'batter_coating': '糖沒有資料；' + PLANT_NO_CHOLESTEROL,
}

# key: (name, oil g per 100 g (low, high), sugar g per 100 g (low, high), source note)
COOKING_METHODS = {
    'none': ('生食／不另外調理', (D(0), D(0)), (D(0), D(0)), '—'),
    'boiled_or_steamed': ('水煮／蒸', (D(0), D(0)), (D(0), D(0)), '—'),
    'blanched_with_oil': ('燙青菜淋油', (D(2), D(5)), (D(0), D(0)), '營養師：燙青菜常淋油，不一定比炒的少（edh.tw）'),
    'stir_fried': ('炒', (D(3), D(8)), (D(0), D('0.5')), '約 100 g 炒青菜用 5 g 油（edh.tw）；一小盤用約 3 小匙油（TVBS）'),
    'pan_fried': ('煎', (D(2), D(5)), (D(0), D(0)), 'TFDA 荷包蛋加 1 小匙油 192 vs 不加油 162 kcal，約多 3.4 g 油'),
    'deep_fried': ('炸', (D(10), D(20)), (D(0), D(0)), '炸雞排、裹粉炸物吸油約 10–20%（The Standard）；裹粉炸魚約 12%（美國專利）'),
    'braised': ('滷／紅燒', (D(0), D(2)), (D(2), D(4)), '滷肉食譜每 600 g 豬肉用約 60 g 冰糖、80 g 醬油（三立新聞）；肉約吸收 1/4–1/2'),
    'soup': ('煮湯', (D('0.5'), D(2)), (D(0), D('0.5')), '假設值：湯面浮油'),
    'sweet_sauce': ('糖醋／甜醬', (D(2), D(5)), (D(5), D(10)), '假設值：糖醋醬含糖高'),
}

# Sodium from salt and sauces, mg per 100 g of food (low, high), per cooking method. Like the oil and sugar, it is a
# recipe prior and is skipped for ingredients whose TFDA value already includes the cooking.
_SALT = SALT_SODIUM_MG_PER_G
SEASONING_SODIUM = {
    'none': ((D(0), D(0)), '不另外調味'),
    'boiled_or_steamed': ((D(0), D(0)), '不另外加鹽；沾醬沒有算'),
    'blanched_with_oil': ((5 * D('40.5'), 10 * D('40.5')),
                          '假設每 100 g 淋 5–10 g 醬油膏 × TFDA P0700701 醬油膏 4050 mg / 100 g'),
    'stir_fried': ((D('0.5') * _SALT, 1 * _SALT),
                   '假設每 100 g 加 0.5–1 g 鹽（500 g 青菜 1 小匙約 5 g 鹽為上限）；對照 TFDA R2100101 炒板條 307 mg'),
    'pan_fried': ((D(0), D('0.5') * _SALT),
                  '假設 0–0.5 g 鹽；TFDA 煎蛋（不加油）145 mg 與 K01001 雞蛋平均值 138 mg 幾乎相同（實驗沒加鹽）'),
    'deep_fried': ((D(0), D('0.5') * _SALT), '假設醃料 0–0.5 g 鹽；裹粉本身的鈉已算在裹粉食材'),
    'braised': ((D(187) - D(69), D(1278) - D(124)),
                'TFDA：R5101101 滷豬腳 187 − I0322201 生豬後腳 69 = 118；K0120102 雞滷蛋（現煮）1278 − K0111101 雞水煮蛋 124 = 1154'),
    'soup': ((D('0.5') * _SALT, 1 * _SALT), '假設每 100 g 湯 0.5–1 g 鹽；對照 TFDA R2100401 擔仔麵（去湯）312 mg'),
    'sweet_sauce': ((D('0.10') * 1858, D('0.20') * 1858), '假設醬汁佔 10–20% × TFDA P1003401 糖醋醬 1858 mg / 100 g'),
}

# Default composition for the 18 known food keys (used when Gemini gives none, and for hand-added dishes).
DEFAULT_COMPONENTS = {
    'cooked_rice_region': ([('cooked_rice', 100)], 'none'),
    'cooked_noodle_region': ([('noodles', 100)], 'boiled_or_steamed'),
    'visible_fried_egg': ([('egg', 100)], 'pan_fried'),
    'Over-easy egg': ([('egg', 100)], 'pan_fried'),
    'bawan': ([('meatball_bawan', 100)], 'deep_fried'),
    'beef_noodles': ([('noodles', 35), ('clear_broth', 50), ('beef', 12), ('leafy_vegetable', 3)], 'soup'),
    'braised_napa_cabbage': ([('leafy_vegetable', 80), ('clear_broth', 20)], 'braised'),
    'braised_pork_over_rice': ([('cooked_rice', 80), ('braised_pork_mince', 20)], 'none'),
    'chicken_mushroom_soup': ([('clear_broth', 70), ('chicken', 25), ('leafy_vegetable', 5)], 'soup'),
    'chinese_pickled_cucumber': ([('pickles', 100)], 'none'),
    'cold_noodle': ([('noodles', 85), ('leafy_vegetable', 15)], 'sweet_sauce'),
    'deep-fried_chicken_cutlets': ([('chicken_cutlet', 85), ('batter_coating', 15)], 'deep_fried'),
    'egg_pancake_roll': ([('pancake_skin', 60), ('egg', 40)], 'pan_fried'),
    'fried-spanish_mackerel_thick_soup': ([('thick_soup', 75), ('fish', 25)], 'deep_fried'),
    'fried_instant_noodles': ([('instant_noodles', 85), ('leafy_vegetable', 15)], 'stir_fried'),
    'fried_rice_noodles': ([('rice_noodles', 85), ('leafy_vegetable', 15)], 'stir_fried'),
    'loofah': ([('gourd_vegetable', 100)], 'stir_fried'),
    'turkey_rice': ([('cooked_rice', 85), ('chicken', 15)], 'none'),
}


def _bounds(low, high, step=D('1')):
    return low.quantize(step, rounding=ROUND_FLOOR), high.quantize(step, rounding=ROUND_CEILING)


def food_nutrition(components, method):
    """{'density', 'visible_kcal_per_100g', 'hidden_kcal_per_100g': (low, high), 'known_share', 'parts'} or None.

    components: [(ingredient key, share percent)]; unknown keys are left out and reported through known_share.
    """
    known = [(key, D(str(share))) for key, share in components or [] if key in INGREDIENTS and D(str(share)) > 0]
    total = sum((share for _, share in known), D(0))
    if not known or total == 0:
        return None
    oil, sugar = COOKING_METHODS.get(method, COOKING_METHODS['none'])[1:3]
    sodium = SEASONING_SODIUM.get(method, SEASONING_SODIUM['none'])[0]
    density = visible = hidden_low = hidden_high = cooked_share = D(0)
    protein = fat = carb = D(0)
    micros = {key: D(0) for key, *_ in NUTRIENTS}
    partial = set()
    for key, share in known:
        _, kcal, ingredient_density, includes_cooking, _ = INGREDIENTS[key]
        weight = share / total
        density += weight * ingredient_density
        visible += weight * kcal
        p, f, c = MACROS[key]
        protein, fat, carb = protein + weight * p, fat + weight * f, carb + weight * c
        for (name, *_), value in zip(NUTRIENTS, MICROS[key]):
            if value is None:  # not analysed in TFDA: the food's amount becomes a lower bound
                partial.add(name)
            else:
                micros[name] += weight * value
        if not includes_cooking:  # the TFDA value of a cooked dish already holds its oil and sugar
            cooked_share += weight
            hidden_low += weight * (oil[0] * OIL_KCAL_PER_G + sugar[0] * SUGAR_KCAL_PER_G)
            hidden_high += weight * (oil[1] * OIL_KCAL_PER_G + sugar[1] * SUGAR_KCAL_PER_G)
    stated = sum((D(str(share)) for _, share in components if D(str(share)) > 0), D(0))
    return {'density': density.quantize(D('0.01')), 'visible_kcal_per_100g': visible.quantize(D('0.1')),
            'hidden_kcal_per_100g': (hidden_low.quantize(D('0.1')), hidden_high.quantize(D('0.1'))),
            'known_share': (total / stated).quantize(D('0.01')) if stated else D(0),
            'parts': [(INGREDIENTS[key][0], int(share)) for key, share in known],
            'macros_per_100g': {'protein': protein, 'fat': fat, 'carb': carb},
            'hidden_fat_g_per_100g': (cooked_share * oil[0], cooked_share * oil[1]),
            'hidden_sugar_g_per_100g': (cooked_share * sugar[0], cooked_share * sugar[1]),
            'nutrients_per_100g': micros, 'nutrients_partial': sorted(partial),
            'hidden_sat_fat_g_per_100g': (cooked_share * oil[0] * OIL_SAT_FAT_PER_G,
                                          cooked_share * oil[1] * OIL_SAT_FAT_PER_G),
            'hidden_sodium_mg_per_100g': (cooked_share * sodium[0], cooked_share * sodium[1]),
            'method': COOKING_METHODS.get(method, COOKING_METHODS['none'])[0]}


def energy_for_grams(grams, nutrition):
    """(visible kcal interval, hidden kcal interval) for a grams interval {'low','mid','high'}."""
    visible = {k: (grams[k] * nutrition['visible_kcal_per_100g'] / 100).quantize(D('1')) for k in ('low', 'mid', 'high')}
    low, high = nutrition['hidden_kcal_per_100g']
    hidden_low, hidden_high = _bounds(grams['low'] * low / 100, grams['high'] * high / 100)
    hidden_mid = (grams['mid'] * (low + high) / 2 / 100).quantize(D('1'))
    return visible, {'low': hidden_low, 'mid': hidden_mid, 'high': hidden_high}


def macros_for_grams(grams, nutrition):
    """Point estimate {'protein', 'carb', 'fat'} in grams for the middle weight, hidden oil and sugar included."""
    mid = grams['mid'] / 100
    m = nutrition['macros_per_100g']
    fat = m['fat'] + sum(nutrition['hidden_fat_g_per_100g']) / 2
    carb = m['carb'] + sum(nutrition['hidden_sugar_g_per_100g']) / 2
    return {'protein': (m['protein'] * mid).quantize(D('0.1')), 'carb': (carb * mid).quantize(D('0.1')),
            'fat': (fat * mid).quantize(D('0.1'))}


def _q(value, step):
    return value.quantize(step, rounding=ROUND_HALF_UP)


def nutrients_for_grams(grams, nutrition):
    """Other nutrients for the middle weight: {key: {'visible', 'hidden', 'hidden_low', 'hidden_high', 'partial'}}.

    visible comes from the ingredients (TFDA); hidden is the recipe part (oil -> saturated fat, added sugar ->
    sugar, seasoning -> sodium) as the middle of its range, with the range alongside. partial means an ingredient
    has no TFDA value for this nutrient, so the real amount can only be higher.
    """
    mid = grams['mid'] / 100
    recipe = {'sugar_g': nutrition['hidden_sugar_g_per_100g'], 'sat_fat_g': nutrition['hidden_sat_fat_g_per_100g'],
              'sodium_mg': nutrition['hidden_sodium_mg_per_100g']}
    out = {}
    for key, _, _, step in NUTRIENTS:
        low, high = recipe.get(key, (D(0), D(0)))
        out[key] = {'visible': _q(nutrition['nutrients_per_100g'][key] * mid, step),
                    'hidden': _q((low + high) / 2 * mid, step),
                    'hidden_low': _q(low * mid, step), 'hidden_high': _q(high * mid, step),
                    'partial': key in nutrition['nutrients_partial']}
    return out


def empty_nutrients():
    return {key: {'visible': D(0), 'hidden': D(0), 'hidden_low': D(0), 'hidden_high': D(0), 'partial': False}
            for key, *_ in NUTRIENTS}


def add_nutrients(total, part):
    """Add one dish's nutrients_for_grams result into a running total (returns a new dict)."""
    return {key: {**{field: total[key][field] + part[key][field] for field in ('visible', 'hidden', 'hidden_low',
                                                                              'hidden_high')},
                  'partial': total[key]['partial'] or part[key]['partial']} for key in total}
