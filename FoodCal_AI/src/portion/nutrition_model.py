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

Hidden energy is a recipe prior, not something the photo shows; the page shows it on its own line.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

D = Decimal
OIL_KCAL_PER_G = D('8.84')
SUGAR_KCAL_PER_G = D('3.84')
TFDA = 'https://consumer.fda.gov.tw/Food/TFND.aspx?nodeID=178'

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
    density = visible = hidden_low = hidden_high = cooked_share = D(0)
    protein = fat = carb = D(0)
    for key, share in known:
        _, kcal, ingredient_density, includes_cooking, _ = INGREDIENTS[key]
        weight = share / total
        density += weight * ingredient_density
        visible += weight * kcal
        p, f, c = MACROS[key]
        protein, fat, carb = protein + weight * p, fat + weight * f, carb + weight * c
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
