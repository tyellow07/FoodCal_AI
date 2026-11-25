"""Nutrition model: visible ingredient energy plus recipe-based hidden oil and sugar, kept apart."""
from decimal import Decimal
import unittest

from src.portion.nutrition_model import (COOKING_METHODS, DEFAULT_COMPONENTS, INGREDIENTS, MACROS, MICRO_NOTES, MICROS,
                                         NUTRIENTS, SEASONING_SODIUM, WHOLE_REFUSE, add_nutrients, empty_nutrients,
                                         energy_for_grams, food_nutrition, macros_for_grams, nutrients_for_grams,
                                         whole_density)
from src.portion.rough_volume import FOOD_TEMPLATES

D = Decimal


class NutritionModelTests(unittest.TestCase):
    def test_tables_are_consistent(self):
        self.assertEqual(set(DEFAULT_COMPONENTS), set(FOOD_TEMPLATES))
        for key, (components, method) in DEFAULT_COMPONENTS.items():
            self.assertIn(method, COOKING_METHODS, key)
            self.assertEqual(sum(share for _, share in components), 100, key)
            self.assertTrue(all(name in INGREDIENTS for name, _ in components), key)
        for key, (_, kcal, density, _, source) in INGREDIENTS.items():  # tea and ice are 0 kcal
            self.assertTrue(0 <= kcal < 900 and 0 < density < 2 and source, key)
        self.assertTrue(set(WHOLE_REFUSE) <= set(INGREDIENTS))
        self.assertTrue(all(0 <= refuse < 100 for refuse in WHOLE_REFUSE.values()))

    def test_plain_rice_has_no_hidden_energy(self):
        rice = food_nutrition([('cooked_rice', 100)], 'none')
        self.assertEqual((rice['visible_kcal_per_100g'], rice['hidden_kcal_per_100g'], rice['density']),
                         (D('183.0'), (D('0.0'), D('0.0')), D('0.79')))

    def test_pan_fried_egg_range_covers_the_tfda_oiled_egg(self):
        egg = food_nutrition([('egg', 100)], 'pan_fried')
        low = egg['visible_kcal_per_100g'] + egg['hidden_kcal_per_100g'][0]
        high = egg['visible_kcal_per_100g'] + egg['hidden_kcal_per_100g'][1]
        self.assertTrue(low <= D(192) <= high)  # TFDA K0123201 荷包蛋（加油1小匙）192 kcal / 100 g

    def test_cooked_dish_values_are_not_counted_twice(self):
        egg = food_nutrition([('braised_egg', 100)], 'braised')
        self.assertEqual(egg['hidden_kcal_per_100g'], (D('0.0'), D('0.0')))
        rice_bowl = food_nutrition([('cooked_rice', 80), ('braised_pork_mince', 20)], 'braised')
        rice_only = food_nutrition([('cooked_rice', 100)], 'braised')
        self.assertEqual(rice_bowl['hidden_kcal_per_100g'][1], (rice_only['hidden_kcal_per_100g'][1] * D('0.8')).quantize(D('0.1')))
        self.assertEqual(rice_bowl['visible_kcal_per_100g'], D('225.8'))  # 0.8 x 183 + 0.2 x 397

    def test_stir_fried_greens_and_unknown_parts(self):
        greens = food_nutrition([('leafy_vegetable', 100)], 'stir_fried')
        self.assertEqual(greens['hidden_kcal_per_100g'], (D('26.5'), D('72.6')))  # 3-8 g oil, 0-0.5 g sugar
        partial = food_nutrition([('leafy_vegetable', 50), ('durian', 50)], 'none')
        self.assertEqual((partial['known_share'], partial['visible_kcal_per_100g']), (D('0.50'), D('20.0')))
        self.assertIsNone(food_nutrition([('durian', 100)], 'none'))
        self.assertIsNone(food_nutrition([], 'none'))

    def test_macros_match_energy_and_include_hidden_oil(self):
        self.assertEqual(set(MACROS), set(INGREDIENTS))
        for key, (p, f, c) in MACROS.items():  # Atwater 4/9/4 lands near the stated energy (TFDA uses finer factors)
            kcal, atwater = INGREDIENTS[key][1], p * 4 + f * 9 + c * 4
            if kcal < 10:  # tea, coffee, ice: a relative error means nothing near zero
                self.assertLess(abs(atwater - kcal), 3, key)
            else:
                self.assertLess(abs(atwater - kcal) / kcal, D('0.25'), key)
        greens = food_nutrition([('leafy_vegetable', 100)], 'stir_fried')
        macros = macros_for_grams({'low': D(100), 'mid': D(100), 'high': D(100)}, greens)
        self.assertEqual(macros, {'protein': D('2.0'), 'carb': D('3.6'), 'fat': D('5.8')})  # + 5.5 g oil, 0.25 g sugar

    def test_other_nutrient_tables_are_complete_and_sourced(self):
        self.assertEqual(set(MICROS), set(INGREDIENTS))
        self.assertEqual(set(MICRO_NOTES), set(INGREDIENTS))
        self.assertEqual(set(SEASONING_SODIUM), set(COOKING_METHODS))
        for key, values in MICROS.items():
            self.assertEqual(len(values), len(NUTRIENTS), key)
            self.assertTrue(all(v is None or v >= 0 for v in values), key)
            self.assertTrue(MICRO_NOTES[key], key)
            if None in values:  # every unknown value is explained
                self.assertIn('沒有資料', MICRO_NOTES[key], key)
        for method, ((low, high), note) in SEASONING_SODIUM.items():
            self.assertTrue(0 <= low <= high and note, method)

    def test_spot_values_follow_the_tfda_rows_and_conversions(self):
        names = [key for key, *_ in NUTRIENTS]
        def value(food, nutrient):
            return MICROS[food][names.index(nutrient)]
        self.assertEqual(value('cooked_rice', 'sodium_mg'), D('2'))           # A0550601 白飯
        self.assertEqual(value('instant_noodles', 'sodium_mg'), D('830.8'))   # R2100201 2077 mg x 0.4
        self.assertEqual(value('chicken', 'cholesterol_mg'), D('117.3'))      # I0404302 88 mg / 0.75
        self.assertEqual(value('egg', 'fiber_g'), D('0'))                     # animal food: structural zero
        self.assertIsNone(value('braised_pork_mince', 'fiber_g'))             # not analysed: unknown, not zero

    def test_unknown_values_make_the_food_partial(self):
        bowl = food_nutrition([('cooked_rice', 80), ('braised_pork_mince', 20)], 'none')
        self.assertEqual(bowl['nutrients_partial'], ['fiber_g', 'sugar_g'])
        self.assertEqual(bowl['nutrients_per_100g']['fiber_g'], D('0.480'))  # only the rice part: 0.8 x 0.6
        self.assertEqual(food_nutrition([('cooked_rice', 100)], 'none')['nutrients_partial'], [])

    def test_recipe_adds_sodium_saturated_fat_and_sugar(self):
        greens = food_nutrition([('leafy_vegetable', 100)], 'stir_fried')
        self.assertEqual(greens['hidden_sodium_mg_per_100g'], (D('196.5'), D('393')))  # 0.5-1 g salt x 393 mg/g
        self.assertEqual(greens['hidden_sat_fat_g_per_100g'], (D('0.483'), D('1.288')))  # 3-8 g soybean oil x 16.1%
        egg = food_nutrition([('braised_egg', 100)], 'braised')  # TFDA value already holds the braising
        self.assertEqual(egg['hidden_sodium_mg_per_100g'], (D(0), D(0)))
        plain = food_nutrition([('cooked_rice', 100)], 'none')
        self.assertEqual(plain['hidden_sodium_mg_per_100g'], (D(0), D(0)))

    def test_nutrients_for_grams_and_totals(self):
        greens = food_nutrition([('leafy_vegetable', 100)], 'stir_fried')
        part = nutrients_for_grams({'low': D(100), 'mid': D(100), 'high': D(100)}, greens)
        self.assertEqual(part['sodium_mg'], {'visible': D('72'), 'hidden': D('295'), 'hidden_low': D('197'),
                                             'hidden_high': D('393'), 'partial': False})
        self.assertEqual((part['iron_mg']['visible'], part['fiber_g']['visible']), (D('2.2'), D('1.9')))
        bowl = food_nutrition([('cooked_rice', 80), ('braised_pork_mince', 20)], 'none')
        total = add_nutrients(add_nutrients(empty_nutrients(), part),
                              nutrients_for_grams({'low': D(200), 'mid': D(200), 'high': D(200)}, bowl))
        self.assertEqual(total['sodium_mg']['visible'], D('72') + D('380'))  # 200 g x (0.8 x 2 + 0.2 x 941) / 100 = 379.6
        self.assertTrue(total['fiber_g']['partial'])
        self.assertFalse(total['sodium_mg']['partial'])

    def test_sweetness_ranges_cover_the_tfda_shop_drinks(self):
        # TFDA 現調飲料, large cup without ice: total sugar g / 100 g for 微糖, 半糖, 全糖
        measured = {'tea': (D('3.6'), D('4.9'), D('8.2')),        # O0701001-O0701201 烏龍茶
                    'milk_tea': (D('3.8'), D('4.7'), D('8.6')),   # O0701701-O0701901 烏龍奶茶
                    'bubble': (D('5.6'), D('6.4'), D('8.8'))}     # O0702001-O0702201 珍珠奶茶 (liquid part)
        methods = ('drink_light_sugar', 'drink_half_sugar', 'drink_full_sugar')
        for drink, totals in measured.items():
            nutrition = food_nutrition([('tea' if drink == 'tea' else 'milk_tea', 100)], 'none')
            base = nutrition['nutrients_per_100g']['sugar_g']  # the creamer's own sugar
            for method, total in zip(methods, totals):
                low, high = food_nutrition([('tea' if drink == 'tea' else 'milk_tea', 100)], method)['hidden_sugar_g_per_100g']
                self.assertTrue(base + low <= total <= base + high, (drink, method, base, low, high))
        cup = food_nutrition([('tea', 75), ('ice', 25)], 'drink_full_sugar')
        self.assertEqual(cup['hidden_sugar_g_per_100g'], (D('7.1'), D('8.2')))  # per 100 g of the cup, ice included
        self.assertLess(abs(700 * cup['hidden_sugar_g_per_100g'][0] / 100 - 50), 1)  # 台大：全糖超過 50 g / 700 mL
        unknown = food_nutrition([('tea', 100)], 'drink_sugar_unknown')['hidden_sugar_g_per_100g']
        self.assertEqual(unknown, (D(0), D('8.2')))
        self.assertEqual(food_nutrition([('tea', 100)], 'drink_no_sugar')['hidden_kcal_per_100g'], (D('0.0'), D('0.0')))

    def test_milk_tea_energy_covers_the_tfda_measurements(self):
        kcal = {'drink_light_sugar': 59, 'drink_half_sugar': 62, 'drink_full_sugar': 75}  # 烏龍奶茶（去冰）
        for method, measured in kcal.items():
            tea = food_nutrition([('milk_tea', 100)], method)
            low = tea['visible_kcal_per_100g'] + tea['hidden_kcal_per_100g'][0]
            high = tea['visible_kcal_per_100g'] + tea['hidden_kcal_per_100g'][1]
            self.assertTrue(low <= measured <= high, (method, low, high))

    def test_drink_defaults_and_ready_made_sugar(self):
        latte = food_nutrition(*DEFAULT_COMPONENTS['latte'])
        self.assertLess(abs(latte['visible_kcal_per_100g'] - 43) / 43, D('0.05'))  # TFDA O0700301 拿鐵咖啡（無糖）43
        cola = food_nutrition([('cola', 100)], 'drink_full_sugar')  # TFDA value already holds the sugar
        self.assertEqual((cola['hidden_sugar_g_per_100g'], cola['nutrients_per_100g']['sugar_g']),
                         ((D(0), D(0)), D('12.8')))
        iced = food_nutrition([('milk', 50), ('ice', 50)], 'none')
        self.assertEqual(iced['visible_kcal_per_100g'], D('31.5'))  # ice adds weight, not energy

    def test_dessert_syrup_and_converted_rows(self):
        plain = food_nutrition([('douhua', 100)], 'sweet_soup')  # TFDA Q2104201 豆花（未加糖）, sugar 0.5 g
        low, high = plain['hidden_sugar_g_per_100g']
        base = plain['nutrients_per_100g']['sugar_g']
        self.assertTrue(base + low <= D('12.8') <= base + high)  # TFDA Q2104101 豆花（花生，含糖水）12.8 g
        self.assertEqual(INGREDIENTS['red_beans'][1], D('128'))  # TFDA 紅豆（乾）328 x USDA 128/329
        self.assertEqual(INGREDIENTS['pasta'][1], D('158'))      # USDA 20121 cooked pasta
        self.assertEqual(INGREDIENTS['glass_noodles'][1], D('100'))  # TFDA 冬粉（乾）351 / 3.5

    def test_whole_fruit_counts_edible_grams_only(self):
        self.assertEqual(whole_density('banana'), D('0.61'))  # 0.95 x (1 - 35.3% peel, TFDA D08001 廢棄率)
        self.assertEqual(whole_density('toast'), INGREDIENTS['toast'][2])  # nothing to throw away
        cut = food_nutrition([('banana', 100)], 'none')
        whole = food_nutrition([('banana', 100)], 'none', whole=True)
        self.assertEqual((cut['density'], whole['density'], whole['whole']), (D('0.63'), D('0.61'), True))
        self.assertEqual(cut['visible_kcal_per_100g'], whole['visible_kcal_per_100g'])

    def test_energy_for_grams(self):
        greens = food_nutrition([('leafy_vegetable', 100)], 'stir_fried')
        visible, hidden = energy_for_grams({'low': D(100), 'mid': D(150), 'high': D(200)}, greens)
        self.assertEqual(visible, {'low': D(20), 'mid': D(30), 'high': D(40)})
        self.assertEqual((hidden['low'], hidden['high']), (D(26), D(146)))


if __name__ == '__main__':
    unittest.main()
