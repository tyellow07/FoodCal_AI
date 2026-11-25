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

- Drinks are components too (tea, milk, creamer milk tea, pearls, ice ...); their sweetness level (全糖 ... 無糖) is
  the "cooking method" and adds sugar per 100 g of the cup, from TFDA measurements of shop drinks.
- A food measured whole or in its peel (shape 'whole') counts edible grams only: WHOLE_REFUSE holds the TFDA refuse
  rate (廢棄率) of each such ingredient.

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
    # Fruit (raw, edible part)
    'banana': ('香蕉', D('85'), D('0.63'), False, 'TFDA D08001 北蕉平均值 85；密度：USDA 9040 切片 1 杯 150 g'),
    'guava': ('芭樂', D('40'), D('0.7'), False, 'TFDA D15002 芭樂平均值（白肉）40；密度：USDA 9139 1 杯 165 g'),
    'apple': ('蘋果', D('51'), D('0.53'), False, 'TFDA D32004 蘋果平均值（混色，去皮及核）51；密度：USDA 9003 切塊 1 杯 125 g'),
    'orange': ('柳丁／柳橙', D('46'), D('0.76'), False, 'TFDA D36001 甜橙平均值 46；密度：USDA 9200 瓣 1 杯 180 g'),
    'watermelon': ('西瓜', D('33'), D('0.64'), False, 'TFDA D19002 西瓜平均值（紅肉小瓜）33；密度：USDA 9326 切丁 1 杯 152 g'),
    'papaya': ('木瓜', D('38'), D('0.61'), False, 'TFDA D02001 木瓜平均值 38；密度：USDA 9226 切塊 1 杯 145 g'),
    'pineapple': ('鳳梨', D('53'), D('0.7'), False, 'TFDA D11002 鳳梨平均值 53；密度：USDA 9266 切塊 1 杯 165 g'),
    'grapes': ('葡萄', D('64'), D('0.64'), False, 'TFDA D1700201 巨峰葡萄（去皮及籽）64；密度：USDA 9132 1 杯 151 g'),
    'mango': ('芒果', D('50'), D('0.7'), False, 'TFDA D21002 芒果平均值（西洋種，如愛文）50；密度：USDA 9176 切塊 1 杯 165 g'),
    'kiwi': ('奇異果', D('56'), D('0.76'), False, 'TFDA D0500201 奇異果（綠肉）56；密度：USDA 9148 切片 1 杯 180 g'),
    'dragon_fruit': ('火龍果', D('50'), D('0.7'), False, 'TFDA D0700201 紅龍果（紅肉）50；密度：假設值，比照切塊水果'),
    'cherry_tomato': ('小番茄', D('33'), D('0.63'), False, 'TFDA E74004 小番茄平均值（紅色系）33；密度：USDA 11529 小番茄 1 杯 149 g'),
    'tangerine': ('橘子', D('40'), D('0.82'), False, 'TFDA D3400101 椪柑 40；密度：USDA 9218 橘子瓣 1 杯 195 g'),
    'strawberry': ('草莓', D('39'), D('0.61'), False, 'TFDA D1400101 草莓 39；密度：USDA 9316 整顆 1 杯 144 g'),
    # Drinks (added sugar comes from the sweetness method drink_*)
    'tea': ('無糖茶（紅茶、綠茶、烏龍茶）', D('0'), D('1.0'), False, 'TFDA O0700501 紅茶茶湯 0（綠茶、烏龍茶湯也是 0）；密度：USDA 14355 紅茶 1 杯 237 g'),
    'black_coffee': ('黑咖啡（美式，無糖）', D('2'), D('1.0'), False, 'TFDA O0700101 美式咖啡（無糖）2；密度：USDA 14209 咖啡 1 杯 237 g'),
    'milk': ('鮮奶（全脂）', D('63'), D('1.03'), False, 'TFDA L01021 全脂鮮乳平均值 63；密度：USDA 1077 全脂牛奶 1 杯 244 g'),
    'milk_tea': ('奶茶（奶精，未加糖）', D('44'), D('1.02'), False, '組合：茶湯 92% ＋ TFDA P1400202 奶精粉 8%，奶精比例讓脂肪對上 O0701701–O0701901 烏龍奶茶（去冰）實測 2.6–2.9 g；密度：假設值'),
    'fresh_milk_tea': ('鮮奶茶（未加糖）', D('27'), D('1.02'), False, 'TFDA O0701501 鮮奶茶（無糖）27；密度：假設值，介於茶與鮮奶之間'),
    'soy_milk': ('豆漿（無糖）', D('35'), D('1.03'), False, 'TFDA H1150201 豆漿（無糖）35；密度：USDA 16120 豆漿 1 杯 243 g'),
    'tapioca_pearls': ('珍珠（粉圓，煮熟）', D('138'), D('1.05'), False, 'TFDA R4100201 粉圓（未烹調）346 ÷ 2.5（煮熟吸水，假設值）；密度：假設值'),
    'orange_juice': ('柳橙汁（100%）', D('48'), D('1.05'), False, 'TFDA O0100401 柳橙汁（100%）48；密度：USDA 9206 柳橙汁 1 杯 248 g'),
    'cola': ('可樂（含糖汽水）', D('51'), D('1.04'), True, 'TFDA O0600101 可樂 51；密度：USDA 14148 12 fl oz 370 g'),
    'yogurt': ('優格（含糖）', D('97'), D('1.04'), True, 'TFDA L0322101 全脂凝態發酵乳 97；密度：USDA 1116 優格 1 杯 245 g'),
    'ice': ('冰塊', D('0'), D('0.92'), False, '冰（水）沒有熱量；密度：冰 0.917 g/mL'),
    # Bread and breakfast
    'toast': ('吐司', D('289'), D('0.22'), False, 'TFDA Q1000101 土司 289；密度：USDA 18069 白吐司 1 片 29 g ÷ 約 10×11×1.2 cm（尺寸為假設）'),
    'burger_bun': ('漢堡麵包', D('322'), D('0.24'), False, 'TFDA Q1001001 漢堡包 322；密度：USDA 18350 漢堡麵包 1 個 44 g ÷ 約 180 mL（假設）'),
    'croissant': ('可頌', D('452'), D('0.25'), False, 'TFDA Q0700101 可頌 452；密度：假設值'),
    'ham': ('火腿（切片）', D('147'), D('1.05'), False, 'TFDA R5100301 切片火腿（豬肉）147；密度：假設值'),
    'bacon': ('培根', D('372'), D('1.0'), False, 'TFDA R5100801 培根（生）372；密度：假設值'),
    'cheese_slice': ('起司片', D('309'), D('1.1'), False, 'TFDA L0500101 切片乾酪 309；密度：假設值'),
    'chicken_nuggets': ('雞塊', D('228'), D('0.9'), False, 'TFDA R5500201 冷凍雞塊（炸之前）228；密度：假設值'),
    'french_fries': ('薯條／薯餅（炸之前）', D('151'), D('0.45'), False, 'TFDA R4100401 冷凍馬鈴薯條 151，薯餅比照；密度：假設值（堆疊的薯條）'),
    'radish_cake': ('蘿蔔糕', D('111'), D('1.05'), False, 'TFDA Q0100401 冷藏廣式蘿蔔糕 111（煎之前）；密度：假設值'),
    # Meat, seafood, sauces
    'pork_loin': ('豬里肌（熟）', D('283'), D('0.85'), False, 'TFDA I0304101 豬大里肌（生）212 ÷ 烹調產率約 0.75'),
    'pork_belly': ('五花肉（熟）', D('480'), D('0.85'), False, 'TFDA I0308101 豬去皮腹脇肉（生）360 ÷ 約 0.75（沒有扣煎、滷出的油，可能偏高）'),
    'salmon': ('鮭魚（熟）', D('276'), D('0.85'), False, 'TFDA J04024 大西洋鮭魚平均值（去皮，生）221 ÷ 約 0.80'),
    'milkfish': ('虱目魚（熟）', D('224'), D('0.85'), False, 'TFDA J04010 虱目魚平均值（去皮，生）179 ÷ 約 0.80'),
    'shrimp': ('蝦仁（熟）', D('124'), D('0.85'), False, 'TFDA J21009 白對蝦平均值（生）99 ÷ 約 0.80'),
    'squid': ('花枝／魷魚（熟）', D('71'), D('0.85'), False, 'TFDA J35001 真烏賊平均值（生）57 ÷ 約 0.80'),
    'fish_cake': ('甜不辣（魚漿炸物）', D('176'), D('1.0'), True, 'TFDA R6700401 天婦羅（魚漿，已炸）176；密度：假設值'),
    'pork_meatball': ('貢丸', D('244'), D('1.0'), False, 'TFDA R5400201 冷凍貢丸 244；密度：假設值'),
    'sausage': ('香腸', D('354'), D('1.0'), False, 'TFDA R5300201 香腸（生）354；密度：假設值'),
    'curry_sauce': ('咖哩醬（不含料）', D('80'), D('1.05'), True, 'TFDA P0200501 咖哩塊 515 × 15.5%（好侍佛蒙特 230 g 一盒配水 1250 mL）；密度：假設值'),
    # Vegetables (raw values; cooking oil and salt come from the method)
    'carrot': ('紅蘿蔔', D('39'), D('0.66'), False, 'TFDA E02001 胡蘿蔔平均值 39；密度：USDA 11125 熟胡蘿蔔 1 大匙 9.7 g'),
    'broccoli': ('青花菜（綠花椰）', D('38'), D('0.66'), False, 'TFDA E5800402 青花菜（2021 年）38；密度：USDA 11091 熟青花菜 1/2 杯 78 g'),
    'bean_sprouts': ('豆芽菜', D('24'), D('0.52'), False, 'TFDA E7700701 綠豆芽 24；密度：USDA 11044 熟綠豆芽 1 杯 124 g'),
    'mushroom': ('菇類', D('39'), D('0.61'), False, 'TFDA G08001 香菇 39、G13002 杏鮑菇 41、G1600101 金針菇 37 平均；密度：USDA 11269 熟香菇 1 杯 145 g'),
    'eggplant': ('茄子', D('25'), D('0.42'), False, 'TFDA E7300101 長茄子 25；密度：USDA 11210 熟茄子丁 1 杯 99 g'),
    'corn': ('玉米', D('107'), D('0.63'), False, 'TFDA A0400301 甜玉米 107；密度：USDA 11168 熟玉米粒 1 杯 149 g'),
    'potato': ('馬鈴薯', D('70'), D('0.66'), False, 'TFDA B0700202 馬鈴薯（2022 年）70；密度：USDA 11365 熟馬鈴薯 1/2 杯 78 g'),
    'sweet_potato': ('地瓜', D('121'), D('0.85'), False, 'TFDA B0400601 黃肉甘藷 121（生；烤地瓜失水，可能偏低）；密度：USDA 11508 烤地瓜 1 杯 200 g'),
    'pumpkin': ('南瓜', D('74'), D('0.5'), False, 'TFDA E63001 南瓜平均值 74；密度：USDA 11422 南瓜塊 1 杯 116 g'),
    'onion': ('洋蔥', D('42'), D('0.89'), False, 'TFDA E2400301 黃洋蔥 42；密度：USDA 11283 熟洋蔥 1 杯 210 g'),
    'tomato': ('番茄', D('19'), D('0.76'), False, 'TFDA E74001 大番茄平均值（紅色系）19；密度：USDA 11529 切塊 1 杯 180 g'),
    'kelp': ('海帶', D('19'), D('0.6'), False, 'TFDA F07001 海帶平均值 19；密度：假設值'),
    # Staples
    'brown_rice': ('糙米飯／紫米飯（熟）', D('192'), D('0.82'), False, 'TFDA A05013 糙稉米平均值（生）364 × 41.0/77.8（白米到白飯的碳水比例）；密度：USDA 20041 糙米飯 1 杯 195 g'),
    'dumplings': ('水餃（熟）', D('235'), D('0.7'), False, 'TFDA R2200801 冷凍豬肉熟水餃 235；密度：假設值（盤上的水餃）'),
    'steamed_bun': ('饅頭', D('248'), D('0.4'), False, 'TFDA R2400201 白饅頭 248；密度：假設值'),
    'meat_bun': ('肉包', D('266'), D('0.5'), False, 'TFDA R2300901 冷凍豬肉包子 266；密度：假設值'),
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
    # Fruit (raw, edible part)
    'banana': (D('1.5'), D('0.1'), D('22.1')),
    'guava': (D('0.7'), D('0.1'), D('10.2')),
    'apple': (D('0.2'), D('0.1'), D('13.9')),
    'orange': (D('0.8'), D('0.2'), D('11.3')),
    'watermelon': (D('0.8'), D('0.1'), D('8')),
    'papaya': (D('0.6'), D('0.1'), D('9.9')),
    'pineapple': (D('0.7'), D('0.1'), D('13.6')),
    'grapes': (D('0.5'), D('0.3'), D('16.6')),
    'mango': (D('0.6'), D('0.2'), D('13')),
    'kiwi': (D('1.1'), D('0.3'), D('14')),
    'dragon_fruit': (D('1.1'), D('0.2'), D('12.3')),
    'cherry_tomato': (D('0.9'), D('0.2'), D('7.4')),
    'tangerine': (D('0.8'), D('0.2'), D('10')),
    'strawberry': (D('1'), D('0.2'), D('9.3')),
    # Drinks (added sugar comes from the sweetness method drink_*)
    'tea': (D('0.1'), D('0'), D('0')),
    'black_coffee': (D('0.2'), D('0.1'), D('0.3')),
    'milk': (D('3.1'), D('3.6'), D('4.8')),
    'milk_tea': (D('0.3'), D('2.8'), D('4.6')),
    'fresh_milk_tea': (D('2'), D('1.4'), D('1.4')),
    'soy_milk': (D('3.6'), D('1.9'), D('0.7')),
    'tapioca_pearls': (D('0.1'), D('0'), D('34.5')),
    'orange_juice': (D('0.6'), D('0.3'), D('10.7')),
    'cola': (D('0'), D('0'), D('12.8')),
    'yogurt': (D('3.1'), D('3.3'), D('13.6')),
    'ice': (D('0'), D('0'), D('0')),
    # Bread and breakfast
    'toast': (D('9.5'), D('6.3'), D('48.6')),
    'burger_bun': (D('9.1'), D('7.4'), D('54.9')),
    'croissant': (D('7.3'), D('26.1'), D('47.1')),
    'ham': (D('16.5'), D('4.9'), D('9')),
    'bacon': (D('13.5'), D('35.6'), D('0')),
    'cheese_slice': (D('18.3'), D('23.7'), D('5.9')),
    'chicken_nuggets': (D('15.3'), D('12.9'), D('12.6')),
    'french_fries': (D('2.2'), D('4.5'), D('25.5')),
    'radish_cake': (D('2.2'), D('2.3'), D('20.4')),
    # Meat, seafood, sauces
    'pork_loin': (D('25.6'), D('19.2'), D('0')),
    'pork_belly': (D('19.9'), D('43.9'), D('0.7')),
    'salmon': (D('25.3'), D('18.6'), D('0')),
    'milkfish': (D('27.3'), D('11.9'), D('0.3')),
    'shrimp': (D('26.6'), D('1.1'), D('0')),
    'squid': (D('15.3'), D('0.8'), D('4.6')),
    'fish_cake': (D('12.4'), D('4.7'), D('21')),
    'pork_meatball': (D('16.6'), D('20.3'), D('0')),
    'sausage': (D('17'), D('26.3'), D('12.3')),
    'curry_sauce': (D('0.9'), D('5.3'), D('7')),
    # Vegetables (raw values; cooking oil and salt come from the method)
    'carrot': (D('1.1'), D('0.1'), D('8.9')),
    'broccoli': (D('3.4'), D('0.4'), D('6.5')),
    'bean_sprouts': (D('2.3'), D('0.2'), D('4.1')),
    'mushroom': (D('2.8'), D('0.2'), D('7.7')),
    'eggplant': (D('1.2'), D('0.2'), D('5.3')),
    'corn': (D('3.3'), D('2.5'), D('17.8')),
    'potato': (D('2.3'), D('0'), D('14.9')),
    'sweet_potato': (D('1.3'), D('0.2'), D('27.8')),
    'pumpkin': (D('1.9'), D('0.2'), D('17.3')),
    'onion': (D('1'), D('0.2'), D('9.5')),
    'tomato': (D('0.8'), D('0.1'), D('4.1')),
    'kelp': (D('0.7'), D('0.1'), D('4.2')),
    # Staples
    'brown_rice': (D('3.9'), D('1.2'), D('40.4')),
    'dumplings': (D('7.2'), D('10.1'), D('28.9')),
    'steamed_bun': (D('8.1'), D('1.2'), D('51.3')),
    'meat_bun': (D('7.6'), D('9.2'), D('38.2')),
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
    # Fruit (raw, edible part)
    'banana': (D('1.6'), D('18'), D('0'), D('0'), D('0'), D('368'), D('5'), D('0.4')),
    'guava': (D('3.5'), D('5.5'), D('0.1'), D('0'), D('1'), D('160'), D('8'), D('0.1')),
    'apple': (D('1.3'), D('11.1'), D('0'), D('0'), D('1'), D('114'), D('4'), D('0.1')),
    'orange': (D('2'), D('9.1'), D('0.1'), D('0'), D('3'), D('149'), D('35'), D('0.2')),
    'watermelon': (D('0.3'), D('7.5'), D('0'), D('0'), D('1'), D('121'), D('7'), D('0.2')),
    'papaya': (D('1.4'), D('6.2'), D('0'), D('0'), D('3'), D('186'), D('23'), D('0.3')),
    'pineapple': (D('1.1'), D('11.6'), D('0.1'), D('0'), D('0'), D('162'), D('10'), D('0.3')),
    'grapes': (D('0.2'), D('15.6'), D('0.1'), D('0'), D('1'), D('122'), D('5'), D('0.1')),
    'mango': (D('1.2'), D('9.8'), D('0.1'), D('0'), D('5'), D('119'), D('8'), D('0.3')),
    'kiwi': (D('2.7'), D('9.3'), D('0.1'), D('0'), D('3'), D('291'), D('28'), D('0.3')),
    'dragon_fruit': (D('1.3'), D('9.2'), D('0.1'), D('0'), D('0'), D('219'), D('9'), D('0.8')),
    'cherry_tomato': (D('1.7'), D('5.1'), D('0.1'), D('0'), D('6'), D('269'), D('14'), D('0.8')),
    'tangerine': (D('1.5'), D('6.8'), D('0'), D('0'), D('2'), D('74'), D('21'), D('0.3')),
    'strawberry': (D('1.8'), D('6.1'), D('0.1'), D('0'), D('7'), D('199'), D('16'), D('0.3')),
    # Drinks (added sugar comes from the sweetness method drink_*)
    'tea': (D('0'), D('0'), D('0'), D('0'), D('7'), D('15'), D('2'), D('0.1')),
    'black_coffee': (D('0'), D('0'), D('0'), D('0'), D('2'), D('62'), D('2'), D('0.1')),
    'milk': (D('0'), D('4.4'), D('2.5'), D('13'), D('37'), D('147'), D('104'), D('0.1')),
    'milk_tea': (D('0'), D('0.7'), D('2.5'), D('0'), D('25.9'), D('81.2'), D('13.1'), D('0.1')),
    'fresh_milk_tea': (D('0'), D('1.8'), D('1'), D('7'), D('12'), D('62'), D('33'), D('0')),
    'soy_milk': (D('1.3'), D('0.4'), D('0.4'), D('0'), D('2'), D('141'), D('14'), D('0.4')),
    'tapioca_pearls': (D('0.12'), D('0'), D('0'), D('0'), D('5.2'), D('10'), D('17.2'), D('0')),
    'orange_juice': (D('0.3'), D('7.9'), D('0.2'), D('0'), D('8'), D('180'), D('11'), D('0')),
    'cola': (D('0'), D('12.8'), D('0'), D('0'), D('7'), D('0'), D('1'), D('0.1')),
    'yogurt': (D('0'), D('12.2'), D('2.3'), D('13'), D('69'), D('174'), D('103'), D('0.1')),
    'ice': (D('0'), D('0'), D('0'), D('0'), D('0'), D('0'), D('0'), D('0')),
    # Bread and breakfast
    'toast': (D('3'), D('4.5'), D('3.1'), D('7'), D('443'), D('101'), D('23'), D('1.4')),
    'burger_bun': (D('3'), D('4.9'), D('5.2'), D('12'), D('286'), D('131'), D('35'), D('1.2')),
    'croissant': (D('2.1'), D('6.6'), D('14.1'), D('32'), D('360'), D('122'), D('36'), D('0.7')),
    'ham': (None, D('2.2'), D('1.8'), D('33'), D('1006'), D('281'), D('36'), D('1.3')),
    'bacon': (D('0'), D('1.4'), D('13.3'), D('55'), D('610'), D('209'), D('3'), D('0.5')),
    'cheese_slice': (D('0'), D('1.7'), D('17.5'), D('78'), D('1594'), D('70'), D('606'), D('1')),
    'chicken_nuggets': (None, D('2.1'), D('5.5'), D('33'), D('596'), D('200'), D('7'), D('0.6')),
    'french_fries': (D('3.5'), D('0'), D('1.2'), D('0'), D('18'), D('357'), D('11'), D('0.6')),
    'radish_cake': (D('0.9'), D('0'), D('0.8'), D('5'), D('251'), D('31'), D('6'), D('0.4')),
    # Meat, seafood, sauces
    'pork_loin': (D('0'), D('0'), D('7.2'), D('78.7'), D('53.3'), D('453.3'), D('5.3'), D('0.8')),
    'pork_belly': (D('0'), D('0'), D('17.07'), D('92'), D('58.7'), D('349.3'), D('6.7'), D('0.8')),
    'salmon': (D('0'), D('0.13'), D('5'), D('83.8'), D('56.3'), D('418.8'), D('7.5'), D('0.1')),
    'milkfish': (D('0'), D('0'), D('4.13'), D('73.8'), D('51.3'), D('437.5'), D('20'), D('0.8')),
    'shrimp': (D('0'), D('0.13'), D('0.38'), D('213.8'), D('262.5'), D('377.5'), D('106.3'), D('3')),
    'squid': (D('0'), D('0'), D('0.25'), D('241.3'), D('85'), D('101.3'), D('12.5'), D('0.1')),
    'fish_cake': (D('0.2'), D('5.2'), D('1.2'), D('20'), D('684'), D('96'), D('17'), D('0.7')),
    'pork_meatball': (D('0'), D('1.5'), D('7.4'), D('42'), D('580'), D('195'), D('6'), D('0.7')),
    'sausage': (None, D('10.2'), D('10'), D('62'), D('1026'), D('284'), D('2'), D('1.6')),
    'curry_sauce': (D('0.76'), D('1.55'), D('3.1'), D('7.4'), D('647.9'), D('42.2'), D('12.9'), D('0.2')),
    # Vegetables (raw values; cooking oil and salt come from the method)
    'carrot': (D('2.6'), D('6.3'), D('0'), D('0'), D('89'), D('198'), D('27'), D('0.3')),
    'broccoli': (D('2.8'), D('1.8'), D('0.2'), D('0'), D('13'), D('349'), D('30'), D('1.2')),
    'bean_sprouts': (D('1.3'), D('1.3'), D('0'), D('0'), D('16'), D('128'), D('56'), D('0.8')),
    'mushroom': (D('3.07'), D('2.1'), D('0.07'), D('0'), D('2'), D('311.3'), D('1.7'), D('0.6')),
    'eggplant': (D('2.7'), D('2.3'), D('0.1'), D('0'), D('2'), D('221'), D('16'), D('0.4')),
    'corn': (D('4.7'), D('7'), D('0.7'), D('0'), D('2'), D('269'), D('3'), D('0.5')),
    'potato': (D('1.2'), D('0.4'), D('0'), D('0'), D('2'), D('431'), D('10'), D('0.6')),
    'sweet_potato': (D('2.5'), D('4.8'), D('0.1'), D('0'), D('51'), D('276'), D('46'), D('0.3')),
    'pumpkin': (D('2.5'), D('5.4'), D('0.1'), D('0'), D('1'), D('426'), D('14'), D('0.5')),
    'onion': (D('1.4'), D('5.5'), D('0.1'), D('0'), D('2'), D('157'), D('20'), D('0.4')),
    'tomato': (D('1'), D('2.2'), D('0'), D('0'), D('2'), D('217'), D('10'), D('0.3')),
    'kelp': (D('2.5'), D('0'), D('0'), D('0'), D('216'), D('6'), D('80'), D('0.9')),
    # Staples
    'brown_rice': (D('2.37'), D('0.26'), D('0.32'), D('0'), D('1.6'), D('115.9'), D('6.9'), D('0.5')),
    'dumplings': (D('2.1'), D('1'), D('3.5'), D('17'), D('333'), D('114'), D('8'), D('0.8')),
    'steamed_bun': (D('1.1'), None, D('1.4'), D('0'), D('182'), D('66'), D('21'), D('0.4')),
    'meat_bun': (D('1.5'), D('6.4'), D('3.5'), D('14'), D('230'), D('114'), D('19'), D('0.7')),
}
PLANT_NO_CHOLESTEROL = '植物性，膽固醇 0（同類較新樣品如 R4700902 傳統豆腐、E3000104 甘藍記 0）'
ANIMAL_NO_FIBER = '動物性，膳食纖維 0（同類較新樣品如 I0108302 牛腱、K01001 雞蛋平均值記 0）'
LOW_FAT_NO_SAT_FAT = '粗脂肪 ≤ 0.2 g，缺的飽和脂肪以 0 計（飽和脂肪不超過粗脂肪）'
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
    # Fruit (raw, edible part)
    'banana': PLANT_NO_CHOLESTEROL,
    'guava': PLANT_NO_CHOLESTEROL,
    'apple': PLANT_NO_CHOLESTEROL,
    'orange': PLANT_NO_CHOLESTEROL,
    'watermelon': PLANT_NO_CHOLESTEROL,
    'papaya': LOW_FAT_NO_SAT_FAT + '；' + PLANT_NO_CHOLESTEROL,
    'pineapple': PLANT_NO_CHOLESTEROL,
    'grapes': PLANT_NO_CHOLESTEROL,
    'mango': PLANT_NO_CHOLESTEROL,
    'kiwi': PLANT_NO_CHOLESTEROL,
    'dragon_fruit': PLANT_NO_CHOLESTEROL,
    'cherry_tomato': PLANT_NO_CHOLESTEROL,
    'tangerine': PLANT_NO_CHOLESTEROL,
    'strawberry': PLANT_NO_CHOLESTEROL,
    # Drinks (added sugar comes from the sweetness method drink_*)
    'tea': '鈉、鉀、鈣、鐵用 O0700901 瓶裝烏龍茶；脂肪、糖、飽和脂肪未分析，碳水 0 g、脂肪極低，以 0 計；' + PLANT_NO_CHOLESTEROL,
    'black_coffee': '膳食纖維用 O0700201 無糖咖啡飲料；' + PLANT_NO_CHOLESTEROL,
    'milk': 'L01021 各項都有資料',
    'milk_tea': '茶湯 92%（礦物質用 O0700901）＋ 奶精粉 8%；加的糖另依甜度算（烹調方式）；' + PLANT_NO_CHOLESTEROL,
    'fresh_milk_tea': '膳食纖維未分析，組成的紅茶茶湯、鮮乳都記 0，以 0 計',
    'soy_milk': PLANT_NO_CHOLESTEROL,
    'tapioca_pearls': '假設：R4100201 粉圓（未烹調）÷ 2.5；泡珍珠的糖漿算在飲料甜度',
    'orange_juice': 'O0100401 各項都有資料',
    'cola': '糖未分析：碳酸水加糖，碳水 12.8 g 都算糖；蛋白質、脂肪未分析，以 0 計',
    'yogurt': '乳製品，膳食纖維 0',
    'ice': '冰塊（水）沒有熱量和營養素',
    # Bread and breakfast
    'toast': 'Q1000101 各項都有資料',
    'burger_bun': 'Q1001001 各項都有資料',
    'croissant': 'Q0700101 各項都有資料',
    'ham': '膳食纖維沒有資料（加工肉品含澱粉）',
    'bacon': '碳水 0 g；' + ANIMAL_NO_FIBER,
    'cheese_slice': '乳製品，膳食纖維 0',
    'chicken_nuggets': '膳食纖維沒有資料（含裹粉）',
    'french_fries': 'R4100401 各項都有資料',
    'radish_cake': 'Q0100401 各項都有資料',
    # Meat, seafood, sauces
    'pork_loin': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'pork_belly': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'salmon': '÷ 0.80；' + ANIMAL_NO_FIBER,
    'milkfish': '÷ 0.80；碳水 0.2 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'shrimp': '÷ 0.80；J21009 各項都有資料',
    'squid': '÷ 0.80；' + ANIMAL_NO_FIBER,
    'fish_cake': 'R6700401 各項都有資料',
    'pork_meatball': '碳水 0 g；' + ANIMAL_NO_FIBER,
    'sausage': '膳食纖維沒有資料（加工肉品）',
    'curry_sauce': '咖哩塊 × 230/(230+1250)',
    # Vegetables (raw values; cooking oil and salt come from the method)
    'carrot': PLANT_NO_CHOLESTEROL,
    'broccoli': 'E5800402 各項都有資料',
    'bean_sprouts': LOW_FAT_NO_SAT_FAT + '；' + PLANT_NO_CHOLESTEROL,
    'mushroom': '三筆平均；' + PLANT_NO_CHOLESTEROL,
    'eggplant': PLANT_NO_CHOLESTEROL,
    'corn': PLANT_NO_CHOLESTEROL,
    'potato': 'B0700202 各項都有資料',
    'sweet_potato': PLANT_NO_CHOLESTEROL,
    'pumpkin': PLANT_NO_CHOLESTEROL,
    'onion': PLANT_NO_CHOLESTEROL,
    'tomato': PLANT_NO_CHOLESTEROL,
    'kelp': PLANT_NO_CHOLESTEROL,
    # Staples
    'brown_rice': '× 41.0/77.8（生米到熟飯）；紫米（黑糯糙米）熱量相近；' + PLANT_NO_CHOLESTEROL,
    'dumplings': 'R2200801 各項都有資料',
    'steamed_bun': '飽和脂肪用 R2400101 冷凍白饅頭；糖沒有資料；以麵粉為主，膽固醇以 0 計',
    'meat_bun': 'R2300901 各項都有資料',
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
    # Drink sweetness (手搖飲甜度): added sugar per 100 g of what is in the cup, ice included. TFDA measured large
    # cups (about 700 mL) without ice; a shop doses the syrup per cup, so with ice the cup holds about the same sugar.
    # Added sugar = TFDA total sugar minus what the drink holds unsweetened (the creamer's 0.7 g in milk tea).
    'drink_no_sugar': ('飲料：無糖', (D(0), D(0)), (D(0), D(0)), '不另外加糖'),
    'drink_light_sugar': ('飲料：微糖', (D(0), D(0)), (D('3.1'), D('4.9')),
                          'TFDA 現調飲料（大杯約 700 mL，去冰）加的糖 g / 100 g：O0701001 烏龍茶 3.6、O0701701 烏龍奶茶 '
                          '3.8 − 0.7、O0702001 珍珠奶茶 5.6 − 0.7；台大羅宇軒團隊：微糖約 25 g（÷ 700 ≈ 3.6）'),
    'drink_half_sugar': ('飲料：半糖', (D(0), D(0)), (D('4.0'), D('6.6')),
                         'TFDA O0701101 烏龍茶 4.9、O0701801 烏龍奶茶 4.7 − 0.7、O0702101 珍珠奶茶 6.4 − 0.7；'
                         '台大：半糖約 35 g，700 mL 實測平均 46.5 g（÷ 700 ≈ 5.0、6.6）'),
    'drink_less_sugar': ('飲料：少糖', (D(0), D(0)), (D('4.9'), D('8.2')),
                         '假設：介於半糖與全糖之間（TFDA 烏龍茶半糖 4.9、全糖 8.2）'),
    'drink_full_sugar': ('飲料：全糖', (D(0), D(0)), (D('7.1'), D('8.2')),
                         'TFDA O0701201 烏龍茶 8.2、O0701901 烏龍奶茶 8.6 − 0.7、O0702201 珍珠奶茶 8.8 − 0.7；'
                         '台大：全糖超過 50 g（÷ 700 ≈ 7.1）'),
    'drink_sugar_unknown': ('飲料：甜度看不出來', (D(0), D(0)), (D(0), D('8.2')),
                            '假設：從無糖到全糖（全糖上限同上）'),
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
    **{key: ((D(0), D(0)), '飲料不加鹽') for key in ('drink_no_sugar', 'drink_light_sugar', 'drink_half_sugar',
                                                 'drink_less_sugar', 'drink_full_sugar', 'drink_sugar_unknown')},
}

# A food photographed whole or still in its peel, rind or core (shape 'whole': a banana, an apple, an orange, corn on
# the cob, a watermelon wedge) is weighed from its outline, so its grams include parts nobody eats. Its density then
# becomes edible grams per mL: WHOLE_DENSITY (most fresh fruit is close to water; an assumption) x (1 - the TFDA
# refuse rate 廢棄率 of the same row). Foods not listed (bread, buns, eggs) are eaten whole at their usual density.
WHOLE_DENSITY = D('0.95')
WHOLE_REFUSE = {  # percent, TFDA 廢棄率 of the row each ingredient uses
    'banana': D('35.3'), 'guava': D('20.9'), 'apple': D('17.8'), 'orange': D('30.8'), 'watermelon': D('43.7'),
    'papaya': D('24.9'), 'pineapple': D('42.0'), 'grapes': D('19.2'), 'mango': D('25.8'), 'kiwi': D('12.9'),
    'dragon_fruit': D('18.9'), 'cherry_tomato': D('0.9'), 'tangerine': D('22.1'), 'strawberry': D('2.6'),
    'corn': D('38.7'), 'potato': D('11.8'), 'sweet_potato': D('15.5'), 'tomato': D('2.0'),
}

# Default composition for the known food keys (used when Gemini gives none, and for hand-added dishes). Drinks
# assume normal ice at 25% of the cup (an assumption; Gemini reports the ice it can see) and an unknown sweetness.
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
    'bubble_milk_tea': ([('milk_tea', 60), ('tapioca_pearls', 15), ('ice', 25)], 'drink_sugar_unknown'),
    'milk_tea_drink': ([('milk_tea', 75), ('ice', 25)], 'drink_sugar_unknown'),
    'fresh_milk_tea_drink': ([('fresh_milk_tea', 75), ('ice', 25)], 'drink_sugar_unknown'),
    'tea_drink': ([('tea', 75), ('ice', 25)], 'drink_sugar_unknown'),
    'soy_milk_drink': ([('soy_milk', 100)], 'drink_sugar_unknown'),
    'latte': ([('milk', 70), ('black_coffee', 30)], 'none'),  # TFDA O0700301 拿鐵咖啡（無糖）43 kcal ≈ 0.7 x 63
    'americano': ([('black_coffee', 100)], 'none'),
}


def _bounds(low, high, step=D('1')):
    return low.quantize(step, rounding=ROUND_FLOOR), high.quantize(step, rounding=ROUND_CEILING)


def whole_density(key):
    """Edible grams per mL of a whole item's outline (WHOLE_REFUSE), or the ingredient's usual density."""
    if key in WHOLE_REFUSE:
        return (WHOLE_DENSITY * (1 - WHOLE_REFUSE[key] / 100)).quantize(D('0.01'))
    return INGREDIENTS[key][2]


def food_nutrition(components, method, whole=False):
    """{'density', 'visible_kcal_per_100g', 'hidden_kcal_per_100g': (low, high), 'known_share', 'parts'} or None.

    components: [(ingredient key, share percent)]; unknown keys are left out and reported through known_share.
    parts and method carry the Chinese names; part_keys and method_key the keys, so the page can name them in its
    own language. whole: the food was measured whole or in its peel, so the density counts edible grams only.
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
        density += weight * (whole_density(key) if whole else ingredient_density)
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
            'part_keys': [key for key, _ in known],
            'macros_per_100g': {'protein': protein, 'fat': fat, 'carb': carb},
            'hidden_fat_g_per_100g': (cooked_share * oil[0], cooked_share * oil[1]),
            'hidden_sugar_g_per_100g': (cooked_share * sugar[0], cooked_share * sugar[1]),
            'nutrients_per_100g': micros, 'nutrients_partial': sorted(partial),
            'hidden_sat_fat_g_per_100g': (cooked_share * oil[0] * OIL_SAT_FAT_PER_G,
                                          cooked_share * oil[1] * OIL_SAT_FAT_PER_G),
            'hidden_sodium_mg_per_100g': (cooked_share * sodium[0], cooked_share * sodium[1]),
            'method': COOKING_METHODS.get(method, COOKING_METHODS['none'])[0],
            'method_key': method if method in COOKING_METHODS else 'none', 'whole': bool(whole)}


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
