"""Nutrition model: visible ingredient energy plus recipe-based hidden oil and sugar, kept apart."""
from decimal import Decimal
import unittest

from src.portion.nutrition_model import (COOKING_METHODS, DEFAULT_COMPONENTS, INGREDIENTS, energy_for_grams,
                                         food_nutrition)
from src.portion.rough_volume import FOOD_TEMPLATES

D = Decimal


class NutritionModelTests(unittest.TestCase):
    def test_tables_are_consistent(self):
        self.assertEqual(set(DEFAULT_COMPONENTS), set(FOOD_TEMPLATES))
        for key, (components, method) in DEFAULT_COMPONENTS.items():
            self.assertIn(method, COOKING_METHODS, key)
            self.assertEqual(sum(share for _, share in components), 100, key)
            self.assertTrue(all(name in INGREDIENTS for name, _ in components), key)
        for key, (_, kcal, density, _, source) in INGREDIENTS.items():
            self.assertTrue(0 < kcal < 900 and 0 < density < 2 and source, key)

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
        partial = food_nutrition([('leafy_vegetable', 50), ('dragon_fruit', 50)], 'none')
        self.assertEqual((partial['known_share'], partial['visible_kcal_per_100g']), (D('0.50'), D('20.0')))
        self.assertIsNone(food_nutrition([('dragon_fruit', 100)], 'none'))
        self.assertIsNone(food_nutrition([], 'none'))

    def test_energy_for_grams(self):
        greens = food_nutrition([('leafy_vegetable', 100)], 'stir_fried')
        visible, hidden = energy_for_grams({'low': D(100), 'mid': D(150), 'high': D(200)}, greens)
        self.assertEqual(visible, {'low': D(20), 'mid': D(30), 'high': D(40)})
        self.assertEqual((hidden['low'], hidden['high']), (D(26), D(146)))


if __name__ == '__main__':
    unittest.main()
