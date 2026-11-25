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
    'pork': ('豬肉（熟）', D('283'), D('0.85'), False, 'TFDA I03104 豬絞肉平均值（生）212 ÷ 約 0.75；部位不明時用肥瘦平均的絞肉；梅花、五花、瘦肉另有項目'),
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
    # Bread, breakfast, spreads and snacks (2026-11-26, second batch)
    'scallion_pancake': ('蔥抓餅／蔥油餅（煎好）', D('305'), D('0.55'), True, 'TFDA R2700101 蔥油餅 305；密度：假設值'),
    'tortilla': ('墨西哥餅皮（捲餅皮）', D('306'), D('0.55'), False, 'TFDA 沒有，用 USDA SR Legacy 18364 麵粉餅皮 306；密度：USDA 7–8 吋一張 49 g，厚度假設 0.3 cm'),
    'bagel': ('貝果', D('264'), D('0.47'), False, 'TFDA 沒有，用 USDA SR Legacy 18001 原味貝果 264；密度：USDA 中型（9.5 cm）105 g，高度假設 3.5 cm'),
    'hot_dog': ('熱狗／德式香腸', D('275'), D('1.0'), False, 'TFDA R5300601 德國香腸 275（R5300501 熱狗 257 相近）；密度：假設值'),
    'burger_patty': ('漢堡肉排', D('249'), D('1.0'), False, 'TFDA R5500101 冷凍牛肉漢堡肉 249；密度：假設值'),
    'tuna': ('鮪魚（罐頭）', D('261'), D('0.9'), True, 'TFDA R6900101 香筍鮪魚罐頭 261；密度：假設值'),
    'pork_floss': ('肉鬆', D('513'), D('0.15'), True, 'TFDA R5600201 豬肉酥 513；密度：假設值（蓬鬆）'),
    'mayonnaise': ('沙拉醬（美乃滋）', D('630'), D('0.93'), True, 'TFDA P1100102 沙拉醬（2021 年）630；密度：USDA 4025 美乃滋 1 杯 220 g'),
    'peanut_butter': ('花生醬', D('657'), D('1.09'), True, 'TFDA P1300901 花生醬 657；密度：USDA 16398 花生醬 1 杯 258 g'),
    'jam': ('果醬', D('256'), D('1.3'), True, 'TFDA P1300301 草莓 272、P1300501 柚子 268、P1300601 桑葚 227 果醬平均；密度：假設值'),
    'chocolate_spread': ('巧克力醬', D('277'), D('1.3'), True, 'TFDA P1300201 巧克力醬（液體）277；密度：假設值'),
    'butter': ('奶油／乳瑪琳', D('711'), D('0.96'), True, 'TFDA M0900201 奶油（加鹽）699、M1000102 人造奶油 722 平均；密度：USDA 1001 奶油 1 杯 227 g'),
    'condensed_milk': ('煉乳', D('325'), D('1.29'), True, 'TFDA L0400201 加糖部份脫脂煉乳 325；密度：USDA 1095 煉乳 1 杯 306 g'),
    'milk_spread': ('奶酥醬', D('596'), D('1.0'), True, 'TFDA P1300101 法式奶酥醬 596；密度：假設值'),
    'youtiao': ('油條', D('551'), D('0.3'), True, 'TFDA R3000101 油條 551；密度：假設值（蓬鬆）'),
    'croquette': ('可樂餅（炸之前）', D('114'), D('0.8'), False, 'TFDA R3100101 冷凍可樂餅（奶汁）114；密度：假設值'),
    'onion_rings': ('洋蔥圈（炸之前）', D('281'), D('0.4'), False, 'TFDA R4400501 冷凍洋蔥圈 281；密度：假設值'),
    'sweet_bread': ('甜麵包（菠蘿、奶酥）', D('378'), D('0.3'), True, 'TFDA Q1000801 菠蘿麵包 378、Q1000401 奶酥麵包 377 平均；密度：假設值'),
    'cake': ('蛋糕', D('306'), D('0.45'), True, 'TFDA Q0900201 海綿蛋糕 344、Q0900101 乳酪蛋糕 296、Q0900701 黑森林蛋糕 277 平均；密度：假設值'),
    'cookies': ('餅乾', D('515'), D('0.5'), True, 'TFDA Q4100201 夾心餅乾 517、Q4100701 蘇打餅乾 512 平均；密度：假設值'),
    'potato_chips': ('洋芋片', D('553'), D('0.15'), True, 'TFDA Q4200201 洋芋片 553；密度：假設值（蓬鬆）'),
    'chocolate': ('巧克力', D('546'), D('1.2'), True, 'TFDA Q6100101 牛奶巧克力 546；密度：假設值'),
    'ice_cream': ('冰淇淋', D('182'), D('0.56'), True, 'TFDA Q2200201 香草冰淇淋 182；密度：USDA 19095 香草冰淇淋 1/2 杯 66 g'),
    'rice_ball': ('糯米飯糰（含油條、肉鬆）', D('335'), D('0.9'), True, 'TFDA R0300201 糯米飯糰 335；密度：假設值'),
    'onigiri': ('三角飯糰', D('219'), D('0.9'), True, 'TFDA R0300101 三角飯糰（肉鬆）219；密度：假設值'),
    'sticky_rice': ('油飯／米糕', D('246'), D('0.8'), True, 'TFDA R0500401 筒仔米糕 246；密度：假設值'),
    # Meat, seafood, eggs and blood products
    'chicken_breast': ('雞胸肉（熟）', D('156'), D('0.85'), False, 'TFDA I0402402 去皮清肉（肉雞，生）117 ÷ 約 0.75'),
    'duck': ('鴨肉（熟）', D('376'), D('0.85'), False, 'TFDA I0504101 鴨腿（生）282 ÷ 約 0.75'),
    'char_siu': ('叉燒', D('253'), D('0.9'), True, 'TFDA R5100101 叉燒肉 253；密度：假設值'),
    'beef_slices': ('牛肉片（火鍋、牛丼）', D('333'), D('0.85'), False, 'TFDA I01101 牛肉火鍋片平均值（生）250 ÷ 約 0.75'),
    'beef_tendon': ('牛筋（滷）', D('119'), D('0.9'), True, 'TFDA R5101001 滷牛筋 119；密度：假設值'),
    'pork_liver': ('豬肝（熟）', D('183'), D('0.9'), False, 'TFDA I0320801 豬肝（生）137 ÷ 約 0.75；密度：假設值'),
    'mackerel': ('鯖魚（烤）', D('412'), D('0.85'), False, 'TFDA J0414803 鯖魚（烤 150 度 10 分）412'),
    'clam': ('蛤蠣（去殼）', D('46'), D('0.85'), False, 'TFDA J3101001 文蛤（去殼，生）37 ÷ 約 0.80'),
    'fish_ball': ('魚丸', D('160'), D('0.95'), False, 'TFDA R6100401 冷凍旗魚丸 160；密度：假設值'),
    'duck_blood': ('鴨血', D('29'), D('1.0'), False, 'TFDA I0520101 鴨血 29；密度：假設值'),
    'pig_blood_cake': ('豬血糕（米血）', D('194'), D('1.05'), False, 'TFDA R0600101 豬血糕 194；密度：假設值'),
    'century_egg': ('皮蛋', D('134'), D('1.0'), True, 'TFDA K01131 雞皮蛋平均值 134；密度：假設值'),
    'wonton': ('餛飩／抄手（煮熟）', D('145'), D('0.7'), False, 'TFDA R2201301 溫州餛飩（去湯）145；密度：假設值，比照水餃'),
    # Tofu, beans and nuts
    'fried_tofu_skin': ('油豆皮（炸豆皮）', D('388'), D('0.6'), True, 'TFDA R4700501 日式炸豆皮 388；密度：假設值'),
    'fried_tofu': ('油豆腐', D('160'), D('0.7'), True, 'TFDA R4701101 小三角油豆腐 160；密度：假設值'),
    'baiye_tofu': ('百頁豆腐', D('196'), D('1.0'), False, 'TFDA R4700801 百頁豆腐 196；密度：假設值'),
    'edamame': ('毛豆', D('129'), D('0.66'), False, 'TFDA H1100101 毛豆仁 129；密度：USDA 11212 毛豆 1 杯 155 g'),
    'peanuts': ('花生', D('558'), D('0.62'), False, 'TFDA C1705201 去膜花生仁（生）558；密度：USDA 16087 花生 1 杯 146 g'),
    'red_beans': ('紅豆（煮熟，未加糖）', D('128'), D('0.97'), False, 'TFDA H0405101 紅豆（乾）328 × 128/329（USDA 16002/16001 紅豆煮熟與乾的熱量比）；密度：USDA 16002 1 杯 230 g'),
    'mung_beans': ('綠豆（煮熟，未加糖）', D('104'), D('0.85'), False, 'TFDA H0705102 綠豆（乾）344 × 105/347（USDA 16081/16080）；密度：USDA 16081 1 杯 202 g'),
    'job_tears': ('薏仁（煮熟）', D('137'), D('0.66'), False, 'TFDA A1000102 薏仁（生）392 × 123/352（USDA 20006/20005 大麥仁煮熟與生的比例，假設薏仁相同）；密度：USDA 20006 1 杯 157 g'),
    # Vegetables
    'cucumber': ('小黃瓜', D('15'), D('0.5'), False, 'TFDA E6400201 花胡瓜 15；密度：假設值，比照切片蔬菜'),
    'daikon': ('白蘿蔔', D('18'), D('0.62'), False, 'TFDA E04001 白蘿蔔平均值 18；密度：USDA 11431 熟白蘿蔔 1 杯 147 g'),
    'bell_pepper': ('青椒／甜椒', D('29'), D('0.63'), False, 'TFDA E75001 甜椒平均值（青皮）29；密度：USDA 11333 青椒丁 1 杯 149 g'),
    'wood_ear': ('木耳', D('38'), D('0.45'), False, 'TFDA G0100101 木耳 38；密度：假設值'),
    'taro': ('芋頭', D('107'), D('0.56'), False, 'TFDA B0500302 檳榔心芋（2021 年）107；密度：USDA 11519 熟芋頭 1 杯 132 g'),
    'kimchi': ('泡菜（韓式）', D('35'), D('0.6'), True, 'TFDA R4400301 韓式泡菜 35；密度：假設值'),
    'green_beans': ('四季豆', D('30'), D('0.53'), False, 'TFDA H1000201 敏豆莢 30；密度：USDA 11053 熟四季豆 1 杯 125 g'),
    'bamboo_shoot': ('竹筍', D('25'), D('0.51'), False, 'TFDA E1500101 綠竹筍 25；密度：USDA 11027 熟竹筍 1 杯 120 g'),
    # Staples and noodles
    'pasta': ('義大利麵（熟）', D('158'), D('0.58'), False, 'USDA SR Legacy 20121 煮熟義大利麵 158（TFDA 只有乾通心麵）；密度：USDA 1 杯 124–151 g'),
    'udon': ('烏龍麵', D('129'), D('0.59'), False, 'TFDA R2001301 冷凍烏龍麵 129；密度：比照麵條'),
    'glass_noodles': ('冬粉（煮熟）', D('100'), D('0.6'), False, 'TFDA R4600201 冬粉（乾）351 ÷ 3.5（營養師：乾冬粉吸水可達原重 2–3 倍，取 2.5 倍，假設值）；密度：假設值'),
    'flat_rice_noodles': ('粄條／河粉', D('129'), D('0.6'), False, 'TFDA R1100501 米粄條 129；密度：比照米粉'),
    'rice_cake': ('年糕', D('220'), D('1.1'), False, 'TFDA Q0100101 冷藏寧波年糕 220；密度：假設值'),
    'sweet_bun': ('甜包子（豆沙、芋泥、奶黃）', D('255'), D('0.45'), False, 'TFDA R2300401 冷凍豆沙包 247、R2300301 冷凍芋泥包 263 平均；密度：比照肉包'),
    'congee': ('粥（廣東粥）', D('81'), D('1.0'), True, 'TFDA R0100101 廣東粥 81（含肉、蛋）；密度：假設值'),
    # Sauces
    'coconut_milk': ('椰漿（泰式咖哩湯汁）', D('208'), D('0.96'), False, 'TFDA P0201101 椰漿 208；密度：USDA 12118 椰奶 1 杯 226 g'),
    'tomato_sauce': ('番茄醬汁（紅醬）', D('70'), D('1.12'), True, 'TFDA P1001301 蕃茄義大利麵醬 70；密度：USDA 6931 1/2 杯 132 g'),
    'cream_sauce': ('白醬', D('61'), D('1.05'), True, 'TFDA P1001101 白醬 61；密度：假設值'),
    'pesto': ('青醬', D('83'), D('1.05'), True, 'TFDA P1001201 青醬 83；密度：假設值'),
    'black_pepper_sauce': ('黑胡椒醬', D('117'), D('1.1'), True, 'TFDA P1002801 黑胡椒醬 117；密度：假設值'),
    'mushroom_sauce': ('蘑菇醬', D('58'), D('1.05'), True, 'TFDA P1003701 蘑菇醬 58；密度：假設值'),
    'sesame_paste': ('芝麻醬（麻醬、胡麻醬）', D('601'), D('1.01'), True, 'TFDA C1610102 白芝麻（熟，2022 年）601，芝麻醬幾乎全是芝麻；密度：USDA 12166 芝麻醬 1 大匙 15 g'),
    # Drinks
    'rice_milk': ('米漿', D('62'), D('1.04'), True, 'TFDA R1300101 米漿（散裝）62；密度：假設值'),
    'winter_melon_tea': ('冬瓜茶', D('33'), D('1.03'), True, 'TFDA O0702801 冬瓜茶 33；密度：假設值'),
    'sugarcane_juice': ('甘蔗汁', D('76'), D('1.07'), False, 'TFDA D3930101 紅甘蔗汁 76；密度：假設值'),
    'apple_juice': ('蘋果汁（100%）', D('51'), D('1.05'), False, 'TFDA O0101301 蘋果汁（100%）51；密度：USDA 9016 1 杯 248 g'),
    'yakult': ('養樂多（多多）', D('68'), D('1.05'), True, 'TFDA L0308101 脫脂稀釋發酵乳 68；密度：假設值'),
    'cocoa_milk': ('可可（鮮奶可可）', D('80'), D('1.05'), True, 'TFDA L1000101 鮮奶可可 80（含糖）；密度：假設值'),
    'oat_milk': ('燕麥奶／燕麥漿', D('44'), D('1.03'), True, 'TFDA R1500101 燕麥奶 44；密度：假設值'),
    'sports_drink': ('運動飲料', D('28'), D('1.02'), True, 'TFDA O0703301 運動飲料 28；密度：假設值'),
    'diet_soda': ('無糖汽水（零卡可樂）', D('1'), D('1.0'), True, 'TFDA O0600201 可樂（低熱量）1；密度：假設值'),
    'water': ('水（氣泡水）', D('0'), D('1.0'), False, '水沒有熱量'),
    # Desserts and toppings (sugar syrup comes from the method sweet_soup)
    'jelly_topping': ('仙草凍／愛玉／寒天', D('19'), D('1.0'), False, 'TFDA Q2100102 仙草凍（2020 年）19，愛玉、寒天熱量更低；密度：假設值'),
    'pudding': ('布丁', D('110'), D('1.05'), True, 'TFDA Q2102301 黃布丁 110；密度：假設值'),
    'douhua': ('豆花（未加糖水）', D('39'), D('1.0'), False, 'TFDA Q2104201 傳統豆花（未加糖）39；密度：假設值'),
    'taro_balls': ('芋圓／地瓜圓／小湯圓', D('214'), D('1.1'), False, 'TFDA R4100101 芋圓（未烹調）214，煮熟吸水後會略低；密度：假設值'),
    'cream': ('鮮奶油（奶蓋）', D('340'), D('0.6'), False, 'TFDA M0900301 奶油（液態）340；密度：假設值（打發）'),
    # ---- meats (2026-11-26, docs/nutrition_model_v0.md section 9) ----
    'chicken_thigh': ('雞腿肉（去骨，熟）', D('220'), D('0.85'), False, 'TFDA I0404501 去皮去骨雞腿（肉雞，生）165 ÷ 約 0.75'),
    'chicken_wing': ('雞翅（熟，可吃部分）', D('280'), D('0.85'), False, 'TFDA I04062 三節翅平均值（生，去骨含皮）210 ÷ 約 0.75；整隻帶骨廢棄率 42.4%'),
    'chicken_skin': ('雞皮（熟）', D('589'), D('0.9'), False, 'TFDA I0421001 雞皮（肉雞，生）442 ÷ 約 0.75（煎烤會逼出部分油，可能偏高）'),
    'chicken_gizzard': ('雞胗（熟）', D('119'), D('0.95'), False, 'TFDA I0420401 雞胗（肉雞，生）89 ÷ 約 0.75；密度：假設值'),
    'chicken_heart': ('雞心（熟）', D('253'), D('0.95'), False, 'TFDA I0420101 雞心（肉雞，生）190 ÷ 約 0.75；密度：假設值'),
    'chicken_liver': ('雞肝（熟）', D('148'), D('0.95'), False, 'TFDA I0420301 雞肝（肉雞，生）111 ÷ 約 0.75；密度：假設值'),
    'chicken_feet': ('雞腳（滷，可吃部分）', D('209'), D('0.9'), False, 'TFDA I0420801 雞腳（肉雞，生，去骨）209；燉滷後與生的相近（USDA SR Legacy 5335 煮熟雞腳 215），不除產率；整隻帶骨廢棄率 58.2%'),
    'turkey': ('火雞肉（熟）', D('188'), D('0.85'), False, 'TFDA I0700101 火雞肉（生，含皮去骨）141 ÷ 約 0.75'),
    'pork_shoulder': ('梅花肉／豬肩胛（熟）', D('393'), D('0.85'), False, 'TFDA I0300401 豬肩胛肉（生）295 ÷ 約 0.75'),
    'pork_jowl': ('松阪豬／豬頸肉（熟）', D('379'), D('0.85'), False, 'TFDA I0310301 豬頸肉（生）284 ÷ 約 0.75'),
    'pork_lean': ('豬瘦肉（後腿，熟）', D('160'), D('0.85'), False, 'TFDA I0302301 豬後腿瘦肉（生）120 ÷ 約 0.75'),
    'pork_tenderloin': ('豬小里肌（熟）', D('185'), D('0.85'), False, 'TFDA I0306101 豬小里肌（生）139 ÷ 約 0.75'),
    'pork_ribs': ('豬小排／肋排（熟，可吃部分）', D('383'), D('0.85'), False, 'TFDA I0304301 豬小排（生，去骨）287 ÷ 約 0.75；整塊帶骨廢棄率 40.9%'),
    'pork_knuckle': ('豬腳（滷，可吃部分）', D('237'), D('0.95'), True, 'TFDA R5101101 滷豬腳（去骨）237；帶骨廢棄率 37%；密度：假設值'),
    'pork_intestine': ('豬大腸（熟）', D('264'), D('0.95'), False, 'TFDA I0321801 豬大腸（生）198 ÷ 約 0.75；密度：假設值'),
    'pork_stomach': ('豬肚（熟）', D('137'), D('0.95'), False, 'TFDA I0321201 熟豬肚（水煮）137；密度：假設值'),
    'pig_blood': ('豬血（豬血湯的豬血）', D('29'), D('1.0'), False, 'TFDA I0320601 豬血 29；密度：假設值'),
    'pork_kidney': ('豬腰（熟）', D('109'), D('0.95'), False, 'TFDA I0321601 豬腎（生）82 ÷ 約 0.75；密度：假設值'),
    'pork_heart': ('豬心（熟）', D('171'), D('0.95'), False, 'TFDA I0320101 豬心（生）128 ÷ 約 0.75；密度：假設值'),
    'pork_ear': ('豬耳朵（滷）', D('292'), D('0.95'), False, 'TFDA I0320401 豬耳（生）219 ÷ 約 0.75；密度：假設值'),
    'pork_tongue': ('豬舌（熟）', D('247'), D('0.95'), False, 'TFDA I0320501 豬舌（生）185 ÷ 約 0.75；密度：假設值'),
    'beef_brisket': ('牛腩（熟）', D('322'), D('0.85'), False, 'TFDA I0104101 牛前胸肉 331、I0104201 牛修清前胸肉 120（生）平均 ÷ 約 0.70（比照牛肉）；牛腩肥瘦不一，取平均'),
    'beef_steak': ('牛排（熟）', D('260'), D('0.85'), False, 'TFDA I0100301 沙朗 162、I0106201 腓力 184、I0106101 紐約克 200（生）平均 ÷ 約 0.70'),
    'beef_short_rib': ('牛小排（熟）', D('414'), D('0.85'), False, 'TFDA I0100101 去骨牛小排（生）290 ÷ 約 0.70'),
    'beef_tripe': ('牛肚（熟）', D('75'), D('0.95'), False, 'TFDA I01201 牛肚平均值（生）56 ÷ 約 0.75；密度：假設值'),
    'lamb': ('羊肉（熟）', D('245'), D('0.85'), False, 'TFDA I0210101 綿羊肉切片 198、I0200301 山羊後腿肉火鍋片 170（生）平均 ÷ 約 0.75'),
    'goose': ('鵝肉（熟）', D('249'), D('0.85'), False, 'TFDA I0600101 鵝肉（生，含皮去骨）187 ÷ 約 0.75'),
    'duck_wing': ('鴨翅（滷，可吃部分）', D('227'), D('0.85'), False, 'TFDA I0520601 鴨翅（生，去骨）170 ÷ 約 0.75；整隻帶骨廢棄率 57.2%'),
    'duck_gizzard': ('鴨胗（滷）', D('129'), D('0.95'), False, 'TFDA I0520201 鴨胗（生）97 ÷ 約 0.75；密度：假設值'),
    'duck_intestine': ('鴨腸（熟）', D('125'), D('0.95'), False, 'TFDA I0520401 鴨腸（生）94 ÷ 約 0.75；密度：假設值'),
    'jerky': ('肉乾（豬、牛）', D('336'), D('0.9'), True, 'TFDA R5200101 牛肉乾 335、R5200201 片狀豬肉乾 329、R5200301 條狀豬肉乾 345 平均；密度：假設值'),
    'chinese_bacon': ('臘肉', D('525'), D('1.0'), True, 'TFDA R5100901 臘肉（五花肉）525；密度：假設值'),
    'luncheon_meat': ('午餐肉（罐頭）', D('315'), D('1.0'), True, 'TFDA 沒有，用 USDA SR Legacy 7908 豬肉火腿午餐肉罐頭 315；密度：假設值'),
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
    'pork': (D('24.9'), D('19.5'), D('0')),
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
    # Bread, breakfast, spreads and snacks (2026-11-26, second batch)
    'scallion_pancake': (D('8.3'), D('9.3'), D('46.9')),
    'tortilla': (D('8.2'), D('8'), D('49.4')),
    'bagel': (D('10.6'), D('1.3'), D('52.4')),
    'hot_dog': (D('15.9'), D('21.4'), D('4.6')),
    'burger_patty': (D('14.4'), D('18.5'), D('6.3')),
    'tuna': (D('10'), D('24.1'), D('0.9')),
    'pork_floss': (D('33'), D('28.3'), D('31.6')),
    'mayonnaise': (D('1.9'), D('61.7'), D('16.8')),
    'peanut_butter': (D('24'), D('54.3'), D('17.9')),
    'jam': (D('0.6'), D('0.8'), D('61.5')),
    'chocolate_spread': (D('2'), D('0.8'), D('65.4')),
    'butter': (D('0.4'), D('79.6'), D('2.7')),
    'condensed_milk': (D('7.5'), D('7.8'), D('56.2')),
    'milk_spread': (D('6.7'), D('46.5'), D('37.6')),
    'youtiao': (D('10.1'), D('40.2'), D('37.3')),
    'croquette': (D('3.6'), D('2.3'), D('19.8')),
    'onion_rings': (D('4'), D('11'), D('41.4')),
    'sweet_bread': (D('9.2'), D('15.4'), D('50.7')),
    'cake': (D('5.4'), D('19.1'), D('28.1')),
    'cookies': (D('6.3'), D('26.1'), D('63.7')),
    'potato_chips': (D('5.2'), D('34.3'), D('55.8')),
    'chocolate': (D('9.4'), D('32.6'), D('54')),
    'ice_cream': (D('2.5'), D('9.1'), D('22.6')),
    'rice_ball': (D('5.9'), D('17.6'), D('38.3')),
    'onigiri': (D('5.8'), D('5.5'), D('36.6')),
    'sticky_rice': (D('6.6'), D('8.6'), D('35.5')),
    # Meat, seafood, eggs and blood products
    'chicken_breast': (D('31.1'), D('2.5'), D('0.8')),
    'duck': (D('19.2'), D('32.7'), D('4.5')),
    'char_siu': (D('17.2'), D('13.1'), D('16.5')),
    'beef_slices': (D('25.5'), D('24.9'), D('0')),
    'beef_tendon': (D('21.1'), D('2.8'), D('2.6')),
    'pork_liver': (D('27.7'), D('7.1'), D('2.3')),
    'mackerel': (D('16.5'), D('37.8'), D('0')),
    'clam': (D('9.5'), D('0.6'), D('3.4')),
    'fish_ball': (D('12.8'), D('6.7'), D('12')),
    'duck_blood': (D('6'), D('0.3'), D('0')),
    'pig_blood_cake': (D('8.6'), D('0.9'), D('37.8')),
    'century_egg': (D('12.5'), D('8.1'), D('3')),
    'wonton': (D('6.5'), D('7.9'), D('11.9')),
    # Tofu, beans and nuts
    'fried_tofu_skin': (D('19.2'), D('32.4'), D('4.9')),
    'fried_tofu': (D('12.7'), D('13.3'), D('0')),
    'baiye_tofu': (D('13.4'), D('13.1'), D('6.3')),
    'edamame': (D('14.6'), D('3.3'), D('12.5')),
    'peanuts': (D('28.8'), D('45.4'), D('19.1')),
    'red_beans': (D('8.1'), D('0.2'), D('23.9')),
    'mung_beans': (D('6.9'), D('0.3'), D('19.1')),
    'job_tears': (D('5.2'), D('2.5'), D('23.2')),
    # Vegetables
    'cucumber': (D('0.8'), D('0.1'), D('3.1')),
    'daikon': (D('0.5'), D('0.1'), D('3.9')),
    'bell_pepper': (D('1'), D('0.4'), D('5.9')),
    'wood_ear': (D('0.9'), D('0.1'), D('8.8')),
    'taro': (D('1.1'), D('0'), D('24.8')),
    'kimchi': (D('2'), D('0.4'), D('5.8')),
    'green_beans': (D('1.7'), D('0.2'), D('5.3')),
    'bamboo_shoot': (D('1.7'), D('0.2'), D('4.7')),
    # Staples and noodles
    'pasta': (D('5.8'), D('0.9'), D('30.9')),
    'udon': (D('3.4'), D('0.3'), D('28.2')),
    'glass_noodles': (D('0'), D('0'), D('25')),
    'flat_rice_noodles': (D('1.1'), D('0.9'), D('29.2')),
    'rice_cake': (D('3.9'), D('0.6'), D('49.7')),
    'sweet_bun': (D('7'), D('2.4'), D('51.5')),
    'congee': (D('4.9'), D('2.8'), D('9.1')),
    # Sauces
    'coconut_milk': (D('1.1'), D('23.1'), D('0')),
    'tomato_sauce': (D('3.2'), D('2.5'), D('8.9')),
    'cream_sauce': (D('1.2'), D('3.2'), D('6.8')),
    'pesto': (D('0.9'), D('5.4'), D('7.7')),
    'black_pepper_sauce': (D('2.5'), D('0.5'), D('25.5')),
    'mushroom_sauce': (D('1.5'), D('2.4'), D('7.7')),
    'sesame_paste': (D('21.7'), D('52.4'), D('21.2')),
    # Drinks
    'rice_milk': (D('0.6'), D('0.5'), D('13.7')),
    'winter_melon_tea': (D('0'), D('0'), D('8.3')),
    'sugarcane_juice': (D('0.5'), D('0.2'), D('20.2')),
    'apple_juice': (D('0.1'), D('0.2'), D('12.2')),
    'yakult': (D('1.1'), D('0.1'), D('15.8')),
    'cocoa_milk': (D('2.1'), D('2.7'), D('11.6')),
    'oat_milk': (D('1'), D('0.8'), D('8.1')),
    'sports_drink': (D('0.1'), D('0'), D('7')),
    'diet_soda': (D('0'), D('0'), D('0.3')),
    'water': (D('0'), D('0'), D('0')),
    # Desserts and toppings (sugar syrup comes from the method sweet_soup)
    'jelly_topping': (D('0.3'), D('0.2'), D('4.1')),
    'pudding': (D('2.3'), D('3.5'), D('17.3')),
    'douhua': (D('3.2'), D('1.9'), D('2.1')),
    'taro_balls': (D('0.9'), D('0.2'), D('52.1')),
    'cream': (D('2'), D('39.1'), D('0')),
    # ---- meats (2026-11-26, docs/nutrition_model_v0.md section 9) ----
    'chicken_thigh': (D('26.4'), D('11.9'), D('0')),
    'chicken_wing': (D('24.4'), D('19.5'), D('0')),
    'chicken_skin': (D('15.6'), D('57.9'), D('0')),
    'chicken_gizzard': (D('22.1'), D('2.7'), D('0.4')),
    'chicken_heart': (D('17.7'), D('19.7'), D('0.1')),
    'chicken_liver': (D('25.1'), D('4.7'), D('3.6')),
    'chicken_feet': (D('20.7'), D('13.3'), D('1.2')),
    'turkey': (D('28.1'), D('7.5'), D('0')),
    'pork_shoulder': (D('22'), D('33.1'), D('0')),
    'pork_jowl': (D('22.9'), D('31.1'), D('1.1')),
    'pork_lean': (D('28.1'), D('4.5'), D('0.4')),
    'pork_tenderloin': (D('28.1'), D('7.2'), D('0')),
    'pork_ribs': (D('24'), D('31.1'), D('0')),
    'pork_knuckle': (D('28.4'), D('15.4'), D('0')),
    'pork_intestine': (D('8.8'), D('25.2'), D('2.5')),
    'pork_stomach': (D('16'), D('7.6'), D('2.7')),
    'pig_blood': (D('6.3'), D('0.3'), D('0.5')),
    'pork_kidney': (D('20'), D('2.5'), D('2.3')),
    'pork_heart': (D('18.1'), D('10.4'), D('1.3')),
    'pork_ear': (D('32.3'), D('17.1'), D('1.2')),
    'pork_tongue': (D('24.7'), D('15.7'), D('0')),
    'beef_brisket': (D('26.5'), D('23.2'), D('0.6')),
    'beef_steak': (D('29.7'), D('14.8'), D('0.8')),
    'beef_short_rib': (D('24.4'), D('34.3'), D('0')),
    'beef_tripe': (D('15.2'), D('1.1'), D('0.1')),
    'lamb': (D('25.1'), D('15.3'), D('0.1')),
    'goose': (D('20.8'), D('17.9'), D('3.2')),
    'duck_wing': (D('30.4'), D('10.8'), D('0')),
    'duck_gizzard': (D('27.3'), D('1.5'), D('0.4')),
    'duck_intestine': (D('18.4'), D('5.2'), D('0')),
    'jerky': (D('34.5'), D('6'), D('36')),
    'chinese_bacon': (D('18.5'), D('49'), D('2.4')),
    'luncheon_meat': (D('13.4'), D('26.6'), D('4.6')),
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
    # Bread, breakfast, spreads and snacks (2026-11-26, second batch)
    'scallion_pancake': (D('2.4'), D('2.3'), D('1.9'), D('6'), D('257'), D('63'), D('19'), D('0.4')),
    'tortilla': (D('3.5'), D('3.71'), D('2.92'), D('0'), D('736'), D('125'), D('146'), D('3.6')),
    'bagel': (D('1.6'), D('8.43'), D('0.36'), D('0'), D('422'), D('107'), D('219'), D('3.6')),
    'hot_dog': (D('1.4'), D('1.7'), D('7.9'), D('64'), D('547'), D('201'), D('18'), D('0.9')),
    'burger_patty': (D('1.5'), D('0'), D('9.3'), D('66'), D('302'), D('197'), D('14'), D('2')),
    'tuna': (D('1.4'), None, D('3.8'), D('20'), D('588'), D('136'), D('13'), D('1.2')),
    'pork_floss': (None, D('25.1'), D('9.2'), D('103'), D('1467'), D('572'), D('90'), D('3.6')),
    'mayonnaise': (D('0'), D('7.4'), D('8.7'), D('91'), D('687'), D('18'), D('14'), D('0.5')),
    'peanut_butter': (D('10.6'), None, D('10.4'), D('0'), D('306'), D('572'), D('22'), D('2.4')),
    'jam': (D('1.7'), D('53.53'), D('0.4'), D('0'), D('34.7'), D('92'), D('18'), D('0.2')),
    'chocolate_spread': (D('2.1'), D('51.7'), D('0.5'), D('0'), D('68'), D('185'), D('15'), D('2.4')),
    'butter': (D('0'), D('0.1'), D('49.3'), D('115'), D('588.5'), D('13.5'), D('11.5'), D('0.1')),
    'condensed_milk': (D('0'), D('52.4'), D('3.9'), D('22'), D('88'), D('371'), D('267'), D('0.1')),
    'milk_spread': (D('0'), D('34.8'), D('20'), D('48'), D('177'), D('331'), D('200'), D('0.4')),
    'youtiao': (D('1.6'), D('0.5'), D('7.1'), D('0'), D('341'), D('96'), D('9'), D('0.9')),
    'croquette': (D('1.1'), None, D('1.5'), D('7'), D('338'), D('79'), D('7'), D('0.6')),
    'onion_rings': (D('2.8'), D('4'), D('2.4'), D('0'), D('503'), D('173'), D('130'), D('0.3')),
    'sweet_bread': (D('1'), D('13.85'), D('8.95'), D('48'), D('209.5'), D('151'), D('63.5'), D('0.7')),
    'cake': (D('1.6'), D('17.4'), D('9.37'), D('147.7'), D('145.7'), D('152'), D('49.7'), D('1.4')),
    'cookies': (D('2.05'), D('13.25'), D('13.3'), D('7.5'), D('345.5'), D('126.5'), D('22.5'), D('1.3')),
    'potato_chips': (D('2.2'), D('1.9'), D('12.8'), D('0'), D('383'), D('817'), D('17'), D('1.7')),
    'chocolate': (None, D('51.9'), D('21.5'), D('9'), D('101'), D('757'), D('250'), D('3.3')),
    'ice_cream': (D('0'), D('16.1'), D('7.9'), D('10'), D('102'), D('233'), D('66'), D('0.1')),
    'rice_ball': (D('1.1'), None, D('3.9'), D('0'), D('393'), D('86'), D('15'), D('0.6')),
    'onigiri': (D('1.9'), D('1.9'), D('0.8'), D('6'), D('224'), D('74'), D('12'), D('0.3')),
    'sticky_rice': (D('1.1'), None, D('3.1'), D('17'), D('299'), D('79'), D('16'), D('1.6')),
    # Meat, seafood, eggs and blood products
    'chicken_breast': (D('0'), D('0'), D('0.8'), D('93.3'), D('44'), D('444'), D('5.3'), D('0.5')),
    'duck': (D('0'), None, D('10.27'), D('101.3'), D('64'), D('324'), D('8'), D('3.1')),
    'char_siu': (None, D('14.9'), D('5'), D('65'), D('841'), D('243'), D('17'), D('0.7')),
    'beef_slices': (D('0'), D('0'), D('11.87'), D('89.3'), D('64'), D('364'), D('6.7'), D('2.4')),
    'beef_tendon': (D('0'), D('0'), D('0.8'), D('49'), D('524'), D('39'), D('11'), D('0.1')),
    'pork_liver': (D('0'), D('0'), D('3.2'), D('384'), D('112'), D('396'), D('5.3'), D('13.6')),
    'mackerel': (D('0'), D('0'), D('8.4'), D('77'), D('58'), D('333'), D('5'), D('1.7')),
    'clam': (D('0'), D('0'), D('0.25'), D('48.8'), D('557.5'), D('130'), D('132.5'), D('10.3')),
    'fish_ball': (D('0.8'), D('4.9'), D('2.5'), D('30'), D('700'), D('80'), D('490'), D('0.7')),
    'duck_blood': (D('0'), D('0'), D('0.1'), D('41'), D('131'), D('9'), D('5'), D('15.6')),
    'pig_blood_cake': (D('0.9'), D('0'), D('0.3'), D('24'), D('414'), D('83'), D('9'), D('12.8')),
    'century_egg': (D('0'), D('0'), D('2.7'), D('400'), D('599'), D('134'), D('45'), D('1.6')),
    'wonton': (D('0.6'), D('0.8'), D('2.8'), D('21'), D('381'), D('87'), D('7'), D('1.4')),
    # Tofu, beans and nuts
    'fried_tofu_skin': (D('2.3'), None, D('5.1'), D('0'), D('1'), D('137'), D('292'), D('2.5')),
    'fried_tofu': (D('0.7'), D('0'), D('2.3'), D('0'), D('1'), D('196'), D('216'), D('2.5')),
    'baiye_tofu': (D('0.5'), D('0.1'), D('2.1'), D('0'), D('425'), D('17'), D('33'), D('2.1')),
    'edamame': (D('6.4'), D('0.4'), D('0.9'), D('0'), D('1'), D('654'), D('44'), D('3.7')),
    'peanuts': (D('6.6'), D('4.5'), D('10.1'), D('0'), D('2'), D('660'), D('50'), D('2.2')),
    'red_beans': (D('7.2'), D('0.16'), D('0.08'), D('0'), D('0.4'), D('468'), D('33.8'), D('2.8')),
    'mung_beans': (D('4.78'), D('0.39'), D('0.12'), D('0'), D('0.3'), D('286.9'), D('32.7'), D('1.5')),
    'job_tears': (D('1.82'), D('0.28'), D('0.42'), D('0'), D('0'), D('138.4'), D('2.8'), D('1.3')),
    # Vegetables
    'cucumber': (D('0.7'), D('1.3'), D('0.1'), D('0'), D('1'), D('219'), D('18'), D('0.2')),
    'daikon': (D('1.1'), D('2.4'), D('0'), D('0'), D('46'), D('125'), D('24'), D('0.1')),
    'bell_pepper': (D('3'), D('2.2'), D('0.2'), D('0'), D('2'), D('196'), D('12'), D('0.4')),
    'wood_ear': (D('7.4'), D('0.2'), D('0'), D('0'), D('12'), D('56'), D('27'), D('0.8')),
    'taro': (D('1.7'), D('1.6'), D('0'), D('0'), D('0'), D('326'), D('20'), D('0.5')),
    'kimchi': (D('2.8'), D('0.7'), D('0.1'), D('24'), D('487'), D('244'), D('63'), D('1.1')),
    'green_beans': (D('2'), D('1.8'), D('0.1'), D('0'), D('4'), D('193'), D('40'), D('0.6')),
    'bamboo_shoot': (D('1.7'), D('2.2'), D('0.2'), D('0'), D('0'), D('303'), D('10'), D('0.8')),
    # Staples and noodles
    'pasta': (D('1.8'), D('0.56'), D('0.18'), D('0'), D('1'), D('44'), D('7'), D('1.3')),
    'udon': (D('1.5'), D('0.4'), D('0.1'), D('0'), D('192'), D('12'), D('7'), D('0')),
    'glass_noodles': (D('0.4'), None, D('0'), D('0'), D('2.9'), D('3.7'), D('0.6'), D('0.5')),
    'flat_rice_noodles': (D('1.4'), D('0.2'), D('0.2'), D('0'), D('82'), D('3'), D('11'), D('0.3')),
    'rice_cake': (D('0.6'), D('0.1'), D('0.2'), D('0'), D('62'), D('22'), D('4'), D('0.5')),
    'sweet_bun': (D('2.05'), D('12.4'), D('0.95'), D('0'), D('53'), D('116.5'), D('18.5'), D('0.6')),
    'congee': (D('0.2'), D('0'), D('0.8'), D('54'), D('271'), D('53'), D('5'), D('1.7')),
    # Sauces
    'coconut_milk': (D('0.3'), D('1.8'), D('21.4'), D('0'), D('23'), D('183'), D('4'), D('0.9')),
    'tomato_sauce': (D('1.6'), D('6.6'), D('0.4'), D('0'), D('768'), D('217'), D('20'), D('0.6')),
    'cream_sauce': (D('1.4'), D('3.9'), D('1.1'), D('3'), D('934'), D('48'), D('27'), D('0.3')),
    'pesto': (D('2.3'), D('4.6'), D('1.2'), D('0'), D('1211'), D('67'), D('39'), D('1.1')),
    'black_pepper_sauce': (D('1.5'), D('8.9'), D('0.2'), D('0'), D('1762'), D('115'), D('41'), D('0.5')),
    'mushroom_sauce': (D('1.3'), None, D('1'), None, D('355'), D('90'), D('6'), D('0.5')),
    'sesame_paste': (D('10.1'), D('0.9'), D('8.4'), D('0'), D('18'), D('493'), D('318'), D('7.4')),
    # Drinks
    'rice_milk': (D('0.7'), D('10'), D('0.1'), D('0'), D('2'), D('13'), D('4'), D('0.1')),
    'winter_melon_tea': (D('0'), D('8.3'), D('0'), D('0'), D('16'), D('2'), D('3'), D('0.2')),
    'sugarcane_juice': (D('0.1'), D('18'), D('0.1'), D('0'), D('0'), D('22'), D('23'), D('0.6')),
    'apple_juice': (D('0.1'), D('11.1'), D('0.1'), D('0'), D('1'), D('96'), D('2'), D('0')),
    'yakult': (D('0'), D('14.8'), D('0'), D('1'), D('21'), D('59'), D('34'), D('0.1')),
    'cocoa_milk': (D('1.2'), D('9'), D('1.9'), D('8'), D('38'), D('162'), D('105'), D('1.5')),
    'oat_milk': (D('1.1'), D('3.8'), D('0.2'), D('0'), D('14'), D('31'), D('4'), D('0.1')),
    'sports_drink': (D('0'), D('7'), D('0'), D('0'), D('41'), D('18'), D('2'), D('0')),
    'diet_soda': (D('0'), D('0'), D('0'), D('0'), D('10'), D('0'), D('1'), D('0.1')),
    'water': (D('0'), D('0'), D('0'), D('0'), D('0'), D('0'), D('0'), D('0')),
    # Desserts and toppings (sugar syrup comes from the method sweet_soup)
    'jelly_topping': (D('0.1'), D('2.6'), D('0.1'), D('0'), D('56'), D('31'), D('12'), D('0.7')),
    'pudding': (D('0'), D('17'), D('2.4'), D('10'), D('66'), D('117'), D('57'), D('0.1')),
    'douhua': (D('0.6'), D('0.5'), D('0.3'), D('0'), D('22'), D('123'), D('300'), D('0.7')),
    'taro_balls': (D('0.8'), D('2.8'), D('0.1'), D('0'), D('2'), D('183'), D('29'), D('3')),
    'cream': (D('0'), D('3.2'), D('27.1'), D('127'), D('24'), D('98'), D('71'), D('0.4')),
    # ---- meats (2026-11-26, docs/nutrition_model_v0.md section 9) ----
    'chicken_thigh': (D('0'), D('0'), D('3.2'), D('154.7'), D('90.7'), D('397.3'), D('8'), D('1.1')),
    'chicken_wing': (D('0'), D('0'), D('5.47'), D('136'), D('100'), D('237.3'), D('10.7'), D('0.7')),
    'chicken_skin': (D('0'), D('0'), D('14.93'), D('181.3'), D('53.3'), D('194.7'), D('5.3'), D('0.8')),
    'chicken_gizzard': (D('0'), D('0'), D('0.8'), D('272'), D('76'), D('249.3'), D('9.3'), D('2.9')),
    'chicken_heart': (D('0'), D('0'), D('5.87'), D('200'), D('116'), D('282.7'), D('5.3'), D('5.9')),
    'chicken_liver': (D('0'), None, D('2'), D('457.3'), D('124'), D('370.7'), D('5.3'), D('4')),
    'chicken_feet': (D('0'), D('0'), D('2.9'), D('121'), D('102'), D('64'), D('24'), D('0.8')),
    'turkey': (D('0'), D('0'), D('2.8'), D('72'), D('68'), D('482.7'), D('4'), D('1.3')),
    'pork_shoulder': (D('0'), D('0'), D('13.07'), D('94.7'), D('70.7'), D('384'), D('4'), D('1.2')),
    'pork_jowl': (D('0'), D('0.27'), D('11.2'), D('88'), D('57.3'), D('286.7'), D('4'), D('0.7')),
    'pork_lean': (D('0'), D('0.13'), D('1.6'), D('76'), D('54.7'), D('541.3'), D('4'), D('1.5')),
    'pork_tenderloin': (D('0'), D('0'), D('2.93'), D('90.7'), D('61.3'), D('514.7'), D('5.3'), D('1.9')),
    'pork_ribs': (D('0'), D('0'), D('12.8'), D('104'), D('98.7'), D('338.7'), D('44'), D('1.3')),
    'pork_knuckle': (D('0'), D('0'), D('6.9'), D('53'), D('187'), D('190'), D('40'), D('1.3')),
    'pork_intestine': (D('0'), None, D('13.2'), D('166.7'), D('60'), D('22.7'), D('20'), D('2.3')),
    'pork_stomach': (D('0'), None, D('3.7'), D('227'), D('30'), D('28'), D('17'), D('1.8')),
    'pig_blood': (D('0'), D('0'), D('0.1'), D('50'), D('179'), D('30'), D('7'), D('28')),
    'pork_kidney': (D('0'), None, D('0.8'), D('453.3'), D('136'), D('284'), D('9.3'), D('9.6')),
    'pork_heart': (D('0'), None, D('4'), D('86.7'), D('98.7'), D('265.3'), D('4'), D('5.5')),
    'pork_ear': (D('0'), D('0'), D('5.73'), D('129.3'), D('233.3'), D('56'), D('21.3'), D('1.7')),
    'pork_tongue': (D('0'), D('0'), D('5.73'), D('148'), D('158.7'), D('281.3'), D('6.7'), D('4')),
    'beef_brisket': (D('0'), None, D('11.79'), D('92.1'), D('73.6'), D('482.9'), D('7.1'), D('3.2')),
    'beef_steak': (D('0'), D('0.05'), D('7.38'), D('89'), D('67.6'), D('546.2'), D('8.6'), D('3.9')),
    'beef_short_rib': (D('0'), D('0'), D('15.43'), D('100'), D('84.3'), D('457.1'), D('11.4'), D('3.6')),
    'beef_tripe': (D('0'), D('0'), D('0.53'), D('137.3'), D('109.3'), D('2.7'), D('58.7'), D('0.8')),
    'lamb': (D('0'), D('0'), D('7.87'), D('68.7'), D('114.7'), D('404.7'), D('6'), D('2.4')),
    'goose': (D('0'), None, D('4.67'), D('94.7'), D('72'), D('312'), D('14.7'), D('2.5')),
    'duck_wing': (D('0'), D('0'), D('3.2'), D('176'), D('89.3'), D('188'), D('18.7'), D('2.4')),
    'duck_gizzard': (D('0'), D('0'), D('0.53'), D('249.3'), D('105.3'), D('432'), D('4'), D('6.9')),
    'duck_intestine': (D('0'), D('0'), D('2'), D('380'), D('70.7'), D('4'), D('36'), D('2')),
    'jerky': (None, D('23.7'), D('2.43'), D('110.7'), D('1273'), D('683'), D('9.3'), D('2.6')),
    'chinese_bacon': (D('0'), D('0'), D('18.8'), D('76'), D('1539'), D('221'), D('5'), D('0.9')),
    'luncheon_meat': (D('0'), D('0'), D('9.99'), D('71'), D('1411'), D('409'), D('0'), D('0.6')),
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
    'pork': '÷ 0.75；' + ANIMAL_NO_FIBER,
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
    # Bread, breakfast, spreads and snacks (2026-11-26, second batch)
    'scallion_pancake': 'R2700101 各項都有資料',
    'tortilla': 'USDA 18364 各項都有資料',
    'bagel': 'USDA 18001 各項都有資料',
    'hot_dog': 'R5300601 各項都有資料',
    'burger_patty': 'R5500101 各項都有資料',
    'tuna': '糖沒有資料',
    'pork_floss': '膳食纖維沒有資料',
    'mayonnaise': 'P1100102 各項都有資料',
    'peanut_butter': '糖沒有資料；' + PLANT_NO_CHOLESTEROL,
    'jam': '三筆平均；草莓果醬缺的糖用 P1300501 柚子果醬；' + PLANT_NO_CHOLESTEROL,
    'chocolate_spread': PLANT_NO_CHOLESTEROL,
    'butter': '油脂，膳食纖維 0' + '；兩筆平均',
    'condensed_milk': '乳製品，膳食纖維 0',
    'milk_spread': '奶油、糖、奶粉，膳食纖維 0',
    'youtiao': 'R3000101 各項都有資料',
    'croquette': '糖沒有資料',
    'onion_rings': 'R4400501 各項都有資料',
    'sweet_bread': '兩筆平均，各項都有資料',
    'cake': '三筆平均，各項都有資料',
    'cookies': '兩筆平均，各項都有資料',
    'potato_chips': 'Q4200201 各項都有資料',
    'chocolate': '膳食纖維沒有資料',
    'ice_cream': '乳製品，膳食纖維以 0 計',
    'rice_ball': '糖沒有資料',
    'onigiri': 'R0300101 各項都有資料',
    'sticky_rice': '糖沒有資料',
    # Meat, seafood, eggs and blood products
    'chicken_breast': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'duck': '÷ 0.75；糖沒有資料；' + ANIMAL_NO_FIBER,
    'char_siu': '膳食纖維沒有資料（含糖醃料）',
    'beef_slices': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'beef_tendon': ANIMAL_NO_FIBER,
    'pork_liver': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'mackerel': '碳水 0 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'clam': '÷ 0.80；' + ANIMAL_NO_FIBER,
    'fish_ball': 'R6100401 各項都有資料',
    'duck_blood': ANIMAL_NO_FIBER,
    'pig_blood_cake': 'R0600101 各項都有資料',
    'century_egg': ANIMAL_NO_FIBER,
    'wonton': 'R2201301 各項都有資料',
    # Tofu, beans and nuts
    'fried_tofu_skin': '糖沒有資料；' + PLANT_NO_CHOLESTEROL,
    'fried_tofu': PLANT_NO_CHOLESTEROL,
    'baiye_tofu': PLANT_NO_CHOLESTEROL,
    'edamame': PLANT_NO_CHOLESTEROL,
    'peanuts': PLANT_NO_CHOLESTEROL,
    'red_beans': '× 128/329（USDA 煮熟／乾）；' + PLANT_NO_CHOLESTEROL,
    'mung_beans': '× 105/347（USDA 煮熟／乾）；' + PLANT_NO_CHOLESTEROL,
    'job_tears': '× 123/352（USDA 大麥仁煮熟／生，假設相同）；' + PLANT_NO_CHOLESTEROL,
    # Vegetables
    'cucumber': PLANT_NO_CHOLESTEROL,
    'daikon': PLANT_NO_CHOLESTEROL,
    'bell_pepper': PLANT_NO_CHOLESTEROL,
    'wood_ear': PLANT_NO_CHOLESTEROL,
    'taro': 'B0500302 各項都有資料',
    'kimchi': 'R4400301 各項都有資料',
    'green_beans': PLANT_NO_CHOLESTEROL,
    'bamboo_shoot': PLANT_NO_CHOLESTEROL,
    # Staples and noodles
    'pasta': 'USDA 20121 各項都有資料',
    'udon': 'R2001301 各項都有資料',
    'glass_noodles': '÷ 3.5；糖沒有資料；' + LOW_FAT_NO_SAT_FAT + '；' + PLANT_NO_CHOLESTEROL,
    'flat_rice_noodles': PLANT_NO_CHOLESTEROL,
    'rice_cake': PLANT_NO_CHOLESTEROL,
    'sweet_bun': '兩筆平均；以麵粉、豆沙、芋頭為主，膽固醇以 0 計（R2300301 記 0）',
    'congee': 'R0100101 各項都有資料',
    # Sauces
    'coconut_milk': PLANT_NO_CHOLESTEROL,
    'tomato_sauce': PLANT_NO_CHOLESTEROL,
    'cream_sauce': 'P1001101 各項都有資料',
    'pesto': 'P1001201 各項都有資料',
    'black_pepper_sauce': 'P1002801 各項都有資料',
    'mushroom_sauce': '糖、膽固醇沒有資料（含肉骨醬）',
    'sesame_paste': 'C1610102 各項都有資料',
    # Drinks
    'rice_milk': '糖用 R1300201 保久米漿',
    'winter_melon_tea': '糖未分析：冬瓜加糖，碳水 8.3 g 都算糖；脂肪未分析，以 0 計',
    'sugarcane_juice': PLANT_NO_CHOLESTEROL,
    'apple_juice': PLANT_NO_CHOLESTEROL,
    'yakult': '乳製品，膳食纖維 0',
    'cocoa_milk': 'L1000101 各項都有資料',
    'oat_milk': PLANT_NO_CHOLESTEROL,
    'sports_drink': '糖未分析：碳水 7.0 g 都算糖；脂肪未分析，以 0 計',
    'diet_soda': '碳水 0.3 g，糖以 0 計；蛋白質、脂肪未分析，以 0 計',
    'water': '水沒有熱量和營養素',
    # Desserts and toppings (sugar syrup comes from the method sweet_soup)
    'jelly_topping': 'Q2100102 各項都有資料',
    'pudding': '乳、蛋、糖漿，膳食纖維以 0 計',
    'douhua': PLANT_NO_CHOLESTEROL,
    'taro_balls': 'R4100101 各項都有資料（未烹調）',
    'cream': '乳製品，膳食纖維 0',
    # ---- meats (2026-11-26, docs/nutrition_model_v0.md section 9) ----
    'chicken_thigh': '÷ 0.75；I0404501 各項都有資料',
    'chicken_wing': '÷ 0.75；碳水 0 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'chicken_skin': '÷ 0.75；I0421001 各項都有資料',
    'chicken_gizzard': '÷ 0.75；' + '碳水 ≤ 0.5 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'chicken_heart': '÷ 0.75；' + '碳水 ≤ 0.5 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'chicken_liver': '÷ 0.75；' + '糖沒有資料；' + ANIMAL_NO_FIBER,
    'chicken_feet': '不除產率；' + ANIMAL_NO_FIBER,
    'turkey': '÷ 0.75；碳水 0 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'pork_shoulder': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'pork_jowl': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'pork_lean': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'pork_tenderloin': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'pork_ribs': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'pork_knuckle': '碳水 0 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'pork_intestine': '÷ 0.75；' + '糖沒有資料；' + ANIMAL_NO_FIBER,
    'pork_stomach': '糖沒有資料；' + ANIMAL_NO_FIBER,
    'pig_blood': ANIMAL_NO_FIBER,
    'pork_kidney': '÷ 0.75；' + '糖沒有資料；' + ANIMAL_NO_FIBER,
    'pork_heart': '÷ 0.75；' + '糖沒有資料；' + ANIMAL_NO_FIBER,
    'pork_ear': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'pork_tongue': '÷ 0.75；' + '碳水 ≤ 0.5 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'beef_brisket': '兩筆平均 ÷ 0.70；糖沒有資料；' + ANIMAL_NO_FIBER,
    'beef_steak': '三筆平均 ÷ 0.70；腓力缺的糖以 0 計（碳水 0.1 g）；' + ANIMAL_NO_FIBER,
    'beef_short_rib': '÷ 0.70；' + ANIMAL_NO_FIBER,
    'beef_tripe': '÷ 0.75；' + '碳水 ≤ 0.5 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'lamb': '兩筆平均 ÷ 0.75；綿羊缺的糖以 0 計（碳水 0 g）；' + ANIMAL_NO_FIBER,
    'goose': '÷ 0.75；' + '糖沒有資料；' + ANIMAL_NO_FIBER,
    'duck_wing': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'duck_gizzard': '÷ 0.75；' + '碳水 ≤ 0.5 g，糖以 0 計；' + ANIMAL_NO_FIBER,
    'duck_intestine': '÷ 0.75；' + ANIMAL_NO_FIBER,
    'jerky': '三筆平均；膳食纖維沒有資料（含糖、果醬醃料）',
    'chinese_bacon': ANIMAL_NO_FIBER,
    'luncheon_meat': 'USDA 7908 各項都有資料',
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
    # Dessert syrup (糖水) poured on 豆花, 仙草, 刨冰 and sweet soups, per 100 g of the bowl.
    'sweet_soup': ('甜湯／糖水', (D(0), D(0)), (D('8.3'), D('12.3')),
                   'TFDA Q2104101 豆花（花生，含糖水）12.8 − Q2104201 傳統豆花（未加糖）0.5 ＝ 12.3；O0702801 冬瓜茶 8.3、'
                   'O0200201 仙草蜜碳水 8.8 g / 100 g'),
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
    'sweet_soup': ((D(0), D(0)), '甜湯不加鹽'),
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

# Meat on the bone photographed as one piece (shape 'whole': a drumstick, chicken wings, spare ribs, pork knuckle) is
# weighed from its outline bone and all. Edible grams per mL = BONE_IN_DENSITY (a piece of meat with its bone; an
# assumption, meat is about 1.05 g/mL) x (1 - the bone share of the cooked piece). TFDA's 廢棄率 is the bone share of
# the raw piece, r; the meat loses water in cooking (yield y, as in the ingredient row) and the bone does not, so the
# cooked share is r / (r + y (1 - r)).
BONE_IN_DENSITY = D('1.05')
BONE_IN_RAW = {  # key: (TFDA 廢棄率 %, cooking yield of the ingredient row; 1 when the row is cooked or not divided)
    'chicken': (D('37.9'), D('0.75')),        # I0404302 drumstick
    'chicken_wing': (D('42.4'), D('0.75')),   # I04062
    'chicken_feet': (D('58.2'), D('1')),      # I0420801, not divided by a yield
    'duck': (D('18.9'), D('0.75')),           # I0504101 duck leg
    'duck_wing': (D('57.2'), D('0.75')),      # I0520601
    'pork_ribs': (D('40.9'), D('0.75')),      # I0304301 spare ribs
    'pork_knuckle': (D('37.0'), D('1')),      # R5101101, already stewed
}
BONE_IN_REFUSE = {key: (100 * r / (r + y * (100 - r))).quantize(D('0.1')) for key, (r, y) in BONE_IN_RAW.items()}

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
    'turkey_rice': ([('cooked_rice', 85), ('turkey', 15)], 'none'),
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
    """Edible grams per mL of a whole item's outline (WHOLE_REFUSE, BONE_IN_REFUSE), or the usual density."""
    if key in WHOLE_REFUSE:
        return (WHOLE_DENSITY * (1 - WHOLE_REFUSE[key] / 100)).quantize(D('0.01'))
    if key in BONE_IN_REFUSE:
        return (BONE_IN_DENSITY * (1 - BONE_IN_REFUSE[key] / 100)).quantize(D('0.01'))
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
