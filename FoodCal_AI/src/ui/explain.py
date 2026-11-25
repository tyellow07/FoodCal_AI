"""Plain-language summary of a diagnostic result for the demo UI, in the page language (Chinese by default).

Presentation only: it reads an existing result, never changes it, and never creates numbers the
pipeline did not compute. A calorie range is shown only when photo_energy_estimate is computed.
The Chinese texts below are the source; messages.py holds the other languages.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .i18n import DEFAULT, language, t

# Detection taxonomy (15 Roboflow Taiwanese-food classes), annotation spec v2 ingredient classes and shop drinks
# (the food keys Gemini may name and the hand-added dish menu offers).
FOOD_NAMES = {
    'Over-easy egg': '荷包蛋', 'bawan': '肉圓', 'beef_noodles': '牛肉麵', 'braised_napa_cabbage': '白菜滷',
    'braised_pork_over_rice': '滷肉飯', 'chicken_mushroom_soup': '香菇雞湯',
    'chinese_pickled_cucumber': '涼拌小黃瓜', 'cold_noodle': '涼麵', 'deep-fried_chicken_cutlets': '炸雞排',
    'egg_pancake_roll': '蛋餅', 'fried-spanish_mackerel_thick_soup': '土魠魚羹',
    'fried_instant_noodles': '炒泡麵', 'fried_rice_noodles': '炒米粉', 'loofah': '絲瓜',
    'turkey_rice': '火雞肉飯',
    'visible_fried_egg': '煎蛋', 'cooked_rice_region': '熟米飯', 'cooked_noodle_region': '熟麵條',
    # Drinks (shop cups), for Gemini's food_key and the hand-added dish menu.
    'bubble_milk_tea': '珍珠奶茶', 'milk_tea_drink': '奶茶', 'fresh_milk_tea_drink': '鮮奶茶',
    'tea_drink': '茶飲（紅茶、綠茶、烏龍）', 'soy_milk_drink': '豆漿', 'latte': '拿鐵', 'americano': '美式咖啡',
}

REASONS = {
    # Visual evidence boundary
    'ingredient_level_semantics_unverified': '目前的模型只辨識「整道菜」，還不能確認盤中有哪些食材，所以無法估重量。',
    'classification_is_not_ingredient_detection': '只有整張照片的菜名分類，沒有標出食材位置，無法估重量。',
    'recipe_prior_not_visual_evidence': '這項食材是從食譜推測的，照片上看不到，不能算進熱量。',
    'evidence_type_not_admissible': '這筆資料不是照片上的食材證據，不能用來估重量。',
    'visual_evidence_uncertain': '這個食材在照片上看不清楚，先不估重量。',
    'visual_evidence_unavailable': '這個區塊沒有可用的食材證據。',
    'ingredient_candidate_missing': '這個區塊還沒有標明是什麼食材。',
    'evidence_id_missing': '這筆標註缺少編號，無法追溯來源。',
    'image_region_missing': '這筆標註缺少照片上的位置框。',
    'duplicate_evidence_identity': '同一個標註編號出現兩次，無法判斷用哪一筆。',
    'no_detections_does_not_establish_ingredient_absence': '模型沒有找到食物；這不代表照片裡沒有食物。',
    # Portion records
    'photo_mass_unknown': '還沒有這個食材的份量（重量）估計。',
    'duplicate_estimates': '同一個食材有兩筆以上份量估計，系統不會自行擇一。',
    'invalid_estimate': '份量估計的格式不正確。',
    'evidence_hash_mismatch': '份量估計對應的標註已被修改，需要重新估計。',
    'ingredient_candidate_mismatch': '份量估計和標註的食材名稱不一致。',
    'estimate_rejected_by_review': '份量估計被審查者退回。',
    'calibration_unavailable': '找不到份量估計使用的校準紀錄。',
    'duplicate_calibration_id': '校準紀錄編號重複。',
    'calibration_invalid': '校準紀錄格式不正確。',
    'calibration_hash_mismatch': '校準紀錄內容和估計時引用的版本不同。',
    'calibration_method_mismatch': '份量估計和校準紀錄用的方法不同。',
    'coverage_differs_from_calibration': '份量區間的涵蓋率和校準紀錄不一致。',
    'calibration_revoked': '這份校準紀錄已被撤銷。',
    'independent_review_not_requested': '份量估計還沒送給第二個人複核。',
    'independent_review_pending': '份量估計正在等待複核。',
    'calibration_not_approved': '份量校準還沒經過核可。',
    'calibration_sample_too_small': '份量校準的秤重樣本少於 30 筆。',
    'calibration_source_groups_insufficient': '份量校準的資料來源（餐廳／日期）少於 2 組。',
    'calibration_coverage_below_nominal': '份量校準的實測涵蓋率低於宣告值。',
    'outside_calibration_ingredient_scope': '這個食材不在校準過的食材範圍內。',
    'outside_calibration_mass_range': '這份重量超出校準過的重量範圍。',
    # Nutrition basis and totals
    'portion_not_ready': '份量估計還沒通過複核或校準條件。',
    'nutrition_basis_unavailable': '還沒有這個食材的營養資料（TFDA 條目）審查紀錄。',
    'duplicate_nutrition_basis': '同一個食材有兩筆以上營養資料。',
    'nutrition_basis_invalid': '營養資料紀錄格式不正確。',
    'basis_ingredient_mismatch': '營養資料和照片食材名稱不一致。',
    'nutrition_basis_unresolved': '營養資料的來源或身分查核還沒通過。',
    'no_visual_items': '照片上沒有可計算的食材。',
    'visual_items_unavailable': '至少有一樣看得到的食物還不能計算，所以不給整餐總熱量。',
    'consumption_basis_missing': '還沒有確認「整份都吃完」，所以不給整餐熱量。',
    'mixed_energy_units': '營養資料的熱量單位不一致（kcal／kJ）。',
    'overlapping_same_ingredient_regions': '同一種食材的框重疊，可能重複計算。',
    # UI / pipeline issues
    'explicit_start_required': '請先選照片，再按「開始辨識」。',
    'local_model_or_taxonomy_missing': '這台電腦沒有模型權重或類別檔，無法執行真實辨識。',
    'model_taxonomy_mismatch': '模型權重和類別檔不一致。',
    'invalid_upload_or_configuration': '圖片無法讀取，或設定不正確。',
    'detection_or_retrieval_failed': '辨識過程失敗，沒有產生任何替代結果。',
    'invalid_or_unavailable_demo_fixture': '展示用資料無法載入。',
}

NEXT_STEPS = {
    'ingredient_level_semantics_unverified': '要算熱量，需要在照片上標出食材區塊（例如白飯、麵、煎蛋），並用秤重資料校準份量。',
    'local_model_or_taxonomy_missing': '請先使用「合成展示」，或向組員取得模型權重與類別檔。',
    'invalid_upload_or_configuration': '請換一張 PNG／JPEG／WEBP 圖片（10 MB 以內）。',
    'explicit_start_required': '選好照片後按「開始辨識」。',
    'no_detections_does_not_establish_ingredient_absence': '請把整個餐盤拍進畫面、光線充足再試一次；模型目前只認得 15 種台灣小吃。',
    'photo_mass_unknown': '份量需要「秤重校準過的估算方法」或實際秤重紀錄。',
}


DETECTION_CLASSES = ('Over-easy egg', 'bawan', 'beef_noodles', 'braised_napa_cabbage', 'braised_pork_over_rice',
                     'chicken_mushroom_soup', 'chinese_pickled_cucumber', 'cold_noodle', 'deep-fried_chicken_cutlets',
                     'egg_pancake_roll', 'fried-spanish_mackerel_thick_soup', 'fried_instant_noodles',
                     'fried_rice_noodles', 'loofah', 'turkey_rice')


def supported_foods_markdown():
    """What the current models and annotation spec can and cannot cover, in plain language."""
    dishes = '、'.join(FOOD_NAMES[c] for c in DETECTION_CLASSES)
    return ('**辨識模型（整道菜，15 類台灣小吃）**：' + dishes + '。\n\n'
            '校園餐廳常見的漢堡、吐司、丼飯、便當大多不在這 15 類裡，辨識不到是正常的。\n\n'
            '**可以估算份量的食材（需人工標註＋學生證比例尺＋秤重校準）**：熟米飯、熟麵條、煎蛋。\n\n'
            '模型只辨識整道菜，不會自己找出食材，所以上傳照片目前不會直接算出熱量。')


def food_name(name):
    """A fixed food in the page language: 荷包蛋（Over-easy egg） in Chinese, 目玉焼き（荷包蛋） in Japanese.

    Other languages keep the Chinese name after their own, so it can be matched with a Chinese menu board.
    """
    if not isinstance(name, str):
        return t('food.unknown')
    if name not in FOOD_NAMES:
        return name
    if language() == DEFAULT:
        return t('food.pattern', name=FOOD_NAMES[name], original=name)
    return t('food.pattern', name=t('food.' + name), original=FOOD_NAMES[name])


def dish_name(item):
    """Name of a recognised or hand-added dish in the page language.

    Gemini names each dish in every page language ('names'); results without that name fall back to the fixed food
    table, then to the English name, then to the name Gemini gave in Chinese (display_name).
    """
    code = language()
    names = item.get('names') or {}
    if names.get(code):
        return names[code]
    if code != DEFAULT:
        if item.get('class_name') in FOOD_NAMES:
            return food_name(item['class_name'])
        if names.get('en'):
            return names['en']
    return item.get('display_name') or food_name(item.get('class_name'))


def reason_text(code):
    if not isinstance(code, str):
        return t('reason.none')
    if code.startswith('gate_'):
        return t('reason.gate', code=code)
    if code in REASONS:
        return t('reason.' + code, REASONS[code])
    return t('reason.unknown', code=code)


def _kcal_range(energy):
    low, high = (Decimal(energy[k]) for k in ('low', 'high'))
    # Round outward so displayed integers never narrow the computed interval.
    return (low.to_integral_value(rounding=ROUND_FLOOR), high.to_integral_value(rounding=ROUND_CEILING))


def _unique(items):
    return list(dict.fromkeys(i for i in items if i))


def summarize(result):
    """Return {'headline', 'level', 'synthetic', 'foods', 'energy_text', 'reasons', 'next_steps', 'notes'}."""
    synthetic = result.get('mode') == 'fixture_offline'
    issues = [i.get('code') for i in result.get('issues', []) if isinstance(i, dict)]
    snapshot = result.get('detection_snapshot') or {}
    foods = [{t('sum.col_id'): d.get('detection_id'), t('sum.col_result'): food_name(d.get('class_name')),
              t('sum.col_score'): f"{d['confidence']:.2f}" if isinstance(d.get('confidence'), (int, float)) else '—'}
             for d in snapshot.get('detections', [])]
    energy = result.get('photo_energy_estimate') or {}
    codes, notes = [], []
    if issues:
        codes += issues
    for item in energy.get('items', []):
        codes.append(item.get('reason'))
        codes += item.get('blocking_reasons') or []
    codes += energy.get('reasons') or []
    if snapshot.get('status') == 'no_detections':
        # With nothing detected, the follow-on totals reasons only repeat the same fact; keep the real one.
        codes = [c for c in codes if c not in ('no_visual_items', 'visual_items_unavailable', 'consumption_basis_missing')]
        codes.append('no_detections_does_not_establish_ingredient_absence')
    codes = _unique(codes)

    if energy.get('status') == 'computed' and energy.get('unit') == 'kcal' and energy.get('energy'):
        low, high = _kcal_range(energy['energy'])
        energy_text = t('sum.energy_kcal', low=low, high=high)
        level, headline = 'success', t('sum.computed')
        if energy.get('completeness') == 'incomplete_hidden_ingredients_unquantified':
            notes.append(t('sum.hidden_note'))
        if energy.get('coverage_lower_bound') is None:
            notes.append(t('sum.narrow_note'))
    elif energy.get('status') == 'computed':
        energy_text = t('sum.energy_other', low=energy['energy']['low'], high=energy['energy']['high'],
                        unit=energy.get('unit'))
        level, headline = 'success', t('sum.computed')
    else:
        energy_text = t('sum.no_energy')
        if issues:
            level, headline = 'error', t('sum.failed')
        elif foods:
            level, headline = 'warning', t('sum.partial', count=len(foods))
        else:
            level, headline = 'warning', t('sum.no_food')
    if synthetic:
        notes.insert(0, t('sum.synthetic_note'))
    if foods:
        notes.append(t('sum.score_note'))
    return {'headline': headline, 'level': level, 'synthetic': synthetic, 'foods': foods,
            'energy_text': energy_text, 'reasons': [reason_text(c) for c in codes],
            'next_steps': _unique(t('next.' + c, NEXT_STEPS[c]) for c in codes if c in NEXT_STEPS), 'notes': notes}
