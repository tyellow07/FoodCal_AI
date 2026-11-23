"""Streamlit page: a real photo recognised by Google Gemini with rough portions, or the synthetic demo.

Layout follows the proposal deck: workflow steps and the KEY RULE banner on top, then three cards - your plate
(photo with numbered tags, upload, samples, start), food recognition (numbered dishes with editable grams) and the
nutrition panel (big kcal, macro bar, visible-vs-recipe split). User images and results stay in session memory;
photos go to Gemini only after the user agrees, with the student card painted over.
"""
import sys
from pathlib import Path

# Streamlit executes this file as an entry script, from the documented project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from src.ui import theme
from src.ui.adapter import decode_upload, draw_items, draw_predictions, fixture_demo
from src.ui.explain import food_name, reason_text, summarize
from src.ui.portions import note_text, nutrition_of, portion_rows, read_food_reference_csv
from src.portion.card_locator import load_card_reference, locate_card
from src.portion.nutrition_model import (COOKING_METHODS, INGREDIENTS, add_nutrients, empty_nutrients, macros_for_grams,
                                         nutrients_for_grams)
from src.portion.rough_volume import FOOD_TEMPLATES, cm_per_px
from src.detection.claude_vision import RecognitionError
from src.detection.gemini_vision import gemini_clients, mask_card, recognize_foods_gemini

ROOT = Path(__file__).resolve().parents[2]
MODE_UPLOAD = '上傳照片辨識'
MODE_DEMO = '合成展示（不需模型）'
CONFIDENCE_TEXT = {'high': '把握高', 'medium': '把握中', 'low': '把握低'}
GEMINI_ERRORS = {
    'gemini_key_invalid': 'Gemini API 金鑰無效或沒有權限：到 Google AI Studio 確認金鑰，再更新 `.streamlit/secrets.toml`。',
    'gemini_model_unavailable': '這組金鑰目前用不了 Gemini Flash 模型，請到 Google AI Studio 確認。',
    'gemini_quota_exceeded': '已超過 Gemini 免費額度（每分鐘或每天的次數上限），請稍後再試。',
    'gemini_service_error': 'Gemini 服務暫時出錯，請稍後再試。',
    'gemini_too_slow': 'Gemini 免費版現在太忙，所有模型都沒有在時間內回應，請過幾分鐘再試，或先用手動加菜。',
    'gemini_network_error': '連不上 Gemini，請檢查網路。',
    'gemini_blocked': 'Gemini 拒絕處理這張照片，請換一張。',
    'gemini_answer_truncated': 'Gemini 的回答被截斷，請再試一次。',
    'gemini_answer_unreadable': 'Gemini 的回答格式不對，請再試一次。',
    'gemini_bad_request': '送給 Gemini 的請求不被接受，請換一張照片再試。',
}
SESSION_KEYS = ('result', 'image', 'input_key', 'card_box', 'card_source', 'card_auto', 'manual_items', 'sample')
PER_PHOTO_KEYS = ('result', 'image', 'card_box', 'card_source', 'card_auto', 'manual_items')
FOOD_REFERENCE_PATH = ROOT / 'data/food_reference/food_reference.csv'
CARD_REFERENCE_DIR = ROOT / 'data/card_reference'
SAMPLES_DIR = ROOT / 'data/samples'  # optional local sample photos (Git-ignored)


def clear_session():
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)


def input_tag():
    return str(st.session_state.get('input_key', ('none',))[0])[:12]


def box_picker(image, key, name):
    """Drag a box on the photo; returns [x0, y0, x1, y1] in original image pixels, or None."""
    import base64
    from io import BytesIO
    import altair as alt
    import pandas as pd
    width, height = image.size
    preview = image.copy()
    preview.thumbnail((900, 900))
    buffer = BytesIO()
    preview.save(buffer, format='JPEG', quality=80)
    url = 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
    shown_w = 640
    shown_h = round(shown_w * height / width)
    x = alt.X('x:Q', scale=alt.Scale(domain=[0, width], nice=False, zero=False), axis=None)
    y = alt.Y('y:Q', scale=alt.Scale(domain=[0, height], nice=False, zero=False, reverse=True), axis=None)
    brush = alt.selection_interval(name=name, encodings=['x', 'y'])
    # The photo is stretched over the data extent [0, width] x [0, height], so the brush and the photo share
    # one coordinate system whatever size the plot ends up with.
    photo = alt.Chart(pd.DataFrame({'x': [0], 'x2': [width], 'y': [0], 'y2': [height], 'url': [url]})).mark_image(
        aspect=False).encode(x=x, x2='x2:Q', y=y, y2='y2:Q', url='url:N')
    corners = alt.Chart(pd.DataFrame({'x': [0, width], 'y': [0, height]})).mark_point(opacity=0).encode(
        x=x, y=y).add_params(brush)
    chart = (photo + corners).properties(width=shown_w, height=shown_h).configure_view(strokeWidth=0)
    # width='content' keeps the chart at the photo's size; a stretched chart would no longer match the image mark.
    event = st.altair_chart(chart, on_select='rerun', selection_mode=name, key=key, theme=None, width='content')
    selection = (event or {}).get('selection', {}).get(name) or {}
    if 'x' in selection and 'y' in selection:
        xs, ys = selection['x'], selection['y']
        return [max(0, round(min(xs))), max(0, round(min(ys))), min(width, round(max(xs))), min(height, round(max(ys)))]
    return None


def card_reference():
    """Logo template for automatic card finding, or None when it has not been set up on this computer."""
    try:
        return load_card_reference(CARD_REFERENCE_DIR)
    except (ValueError, OSError):
        st.warning('學生證 logo 設定檔讀不到，請重新執行 `python -m tools.setup_card_reference`。')
        return None


def gemini_key():
    """Gemini API key from .streamlit/secrets.toml (git-ignored), else the GEMINI_API_KEY environment variable."""
    import os
    try:
        key = st.secrets.get('GEMINI_API_KEY')
    except Exception:  # no secrets file yet
        key = None
    return key or os.environ.get('GEMINI_API_KEY') or None


def sample_photos():
    if not SAMPLES_DIR.is_dir():
        return []
    return sorted(p for p in SAMPLES_DIR.iterdir() if p.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp'))[:4]


def current_items(result):
    """(base items left after exclusions, excluded items, items used for portions incl. hand-added ones)."""
    tag = input_tag()
    base = list(result.get('items', []))
    skipped = [d for d in base if st.session_state.get(f"skip_{tag}_{d['detection_id']}")]
    kept = [d for d in base if d not in skipped]
    return kept, skipped, kept + st.session_state.get('manual_items', [])


def compute(result):
    """Portion rows, total, macros and other nutrients for the current photo, from session widget values.

    macros carries the other nutrients under 'nutrients'. A dish without a composition (only a per-food kcal from
    the reference table) has no nutrient data, so every nutrient total is then marked partial.
    """
    try:
        reference = read_food_reference_csv(FOOD_REFERENCE_PATH)
    except ValueError as error:
        st.error('密度與熱量表格式不對：' + str(error))
        reference = {}
    _, _, items = current_items(result)
    tag = input_tag()
    typed = {item['detection_id']: st.session_state.get(f"grams_{tag}_{item['detection_id']}") for item in items}
    rows, total, missing = portion_rows(items, st.session_state.get('card_box'), reference, typed)
    macros = {'protein': 0, 'carb': 0, 'fat': 0}
    nutrients = empty_nutrients()
    for row in rows:
        if row['nutrition'] is not None and row['grams'] is not None:
            for name, value in macros_for_grams(row['grams'], row['nutrition']).items():
                macros[name] += value
            nutrients = add_nutrients(nutrients, nutrients_for_grams(row['grams'], row['nutrition']))
        elif row['grams'] is not None:
            nutrients = {key: {**value, 'partial': True} for key, value in nutrients.items()}
    macros['nutrients'] = nutrients
    return items, rows, total, missing, macros, reference


def start_recognition(raw, key):
    image = decode_upload(raw)
    reference, card = card_reference(), None
    if reference is not None:
        with st.spinner('找學生證中…'):
            card = locate_card(image, reference)
    outgoing = mask_card(image, card['quad']) if card else image
    try:
        with st.spinner('Gemini 辨識中…（通常 5–30 秒，免費版忙的時候會更久）'):
            found = recognize_foods_gemini(outgoing, clients=gemini_clients(key))
    except RecognitionError as error:
        st.session_state['gemini_error'] = GEMINI_ERRORS.get(str(error), str(error))
        return
    for name in PER_PHOTO_KEYS:
        st.session_state.pop(name, None)
    st.session_state['card_auto'] = card or False
    if card:
        st.session_state['card_box'], st.session_state['card_source'] = card['quad'], 'auto'
    st.session_state['result'] = {'engine': 'gemini', 'card_masked': bool(card), **found}
    st.session_state['image'] = image


def plate_card(result, raw, items, key):
    image = st.session_state.get('image')
    chip = '辨識完成' if result else '已選照片' if raw else '尚未上傳'
    st.markdown(theme.card_head('Your plate · 你的餐盤', chip), unsafe_allow_html=True)
    if result and image is not None:
        show = st.checkbox('顯示框', value=True, key='show_boxes')
        st.image(draw_items(image, items, st.session_state.get('card_box'), show), width='stretch')
    elif raw:
        try:
            st.image(decode_upload(raw), caption='照片預覽（還沒開始辨識）', width='stretch')
        except (ValueError, OSError):
            st.error('圖片無法讀取，請換一張 PNG／JPEG／WEBP 照片。')
    else:
        st.markdown('<div class="fc-empty">上傳一張餐點照片。把學生證<b>背面朝上</b>平放在餐盤旁，'
                    '程式會自動找到它來換算大小。</div>', unsafe_allow_html=True)
    st.file_uploader('＋ 上傳照片（手機可以直接拍照）', type=['png', 'jpg', 'jpeg', 'webp'],
                     key=f"upload_{st.session_state.get('upload_generation', 0)}")
    st.caption('JPG、PNG、WebP，最多 10 MB；照片和結果只留在這次的記憶體。')
    samples = sample_photos()
    if samples:
        st.markdown('<span class="fc-label">Or try a sample · 或試試範例</span>', unsafe_allow_html=True)
        with st.container(key='fc_samples'):  # CSS keeps these side by side on phones
            columns = st.columns(len(samples))
        for index, (column, path) in enumerate(zip(columns, samples), 1):
            with column:
                st.image(str(path), width='stretch')
                if st.button(f'範例 {index}', key=f'sample_{index}', width='stretch'):
                    st.session_state['sample'] = str(path)
                    st.session_state['upload_generation'] = st.session_state.get('upload_generation', 0) + 1
                    st.rerun()
    if not key:
        st.warning('還沒有設定 Gemini API 金鑰：在 `FoodCal_AI/.streamlit/secrets.toml` 加一行 '
                   '`GEMINI_API_KEY = "你的金鑰"`，再重新整理這個頁面。')
    consent = st.checkbox('我了解並同意把這張照片傳給 Google Gemini 辨識', key='gemini_consent')
    st.caption('只送縮小過、沒有拍攝資訊（含 GPS）的照片，學生證會先自動塗掉。依 Google 免費版條款，'
               '照片可能被用來改進產品並由人員審閱，請不要拍到人臉。')
    if st.button('開始辨識 →', type='primary', disabled=not raw or not (consent and key), width='stretch'):
        st.session_state.pop('gemini_error', None)
        start_recognition(raw, key)
        st.rerun()
    if st.session_state.get('gemini_error'):
        st.error(st.session_state['gemini_error'])
    if result:
        auto = st.session_state.get('card_auto')
        card_text = ('自動找到學生證（綠框）' if st.session_state.get('card_source') == 'auto' else
                     '學生證：你手動框的（綠框）' if st.session_state.get('card_source') == 'manual' else
                     '沒有找到學生證，到下方「修正工具」框出來')
        masked = '，已在送出前塗掉' if result.get('card_masked') else ''
        score = f"（配對 {auto['score']:.2f}）" if auto and st.session_state.get('card_source') == 'auto' else ''
        usage = result.get('usage') or {}
        st.caption(f"{card_text}{score}{masked}。模型 {result.get('model')} · {result.get('latency_s')} 秒 · "
                   f"{usage.get('input_tokens')}/{usage.get('output_tokens')} tokens（免費額度）")
    if st.button('清除本次圖片與結果', width='stretch'):
        clear_session()
        st.session_state.pop('gemini_error', None)
        st.session_state['upload_generation'] = st.session_state.get('upload_generation', 0) + 1
        st.rerun()


def row_meta(row, item):
    bits = []
    if row['nutrition']:
        parts = '、'.join(f'{name} {share}%' for name, share in row['nutrition']['parts'])
        bits.append(f"{row['nutrition']['method']} · {parts}")
    if item.get('confidence'):
        bits.append(CONFIDENCE_TEXT.get(item['confidence'], item['confidence']))
    if row['grams'] is not None:
        source = '你填的' if row['grams_source'] == 'user' else f"約 {row['grams']['low']}–{row['grams']['high']} g"
        bits.append(f"{source} · 看得到的約 {row['kcal']['mid'] if row['kcal'] else '—'} kcal")
    bits += [note_text(code) for code in row['notes']]
    return ' · '.join(bits)


def recognition_card(result, rows, items):
    tag = input_tag()
    model = (result or {}).get('model')
    st.markdown(theme.card_head('Food recognition · 食物辨識', model.replace('gemini-', '') if model else None),
                unsafe_allow_html=True)
    if not result:
        st.markdown('<div class="fc-empty">按「開始辨識」後，Gemini 會列出照片裡的每道菜、看得到的食材和烹調方式。</div>',
                    unsafe_allow_html=True)
        return
    names = '、'.join(i.get('display_name') or food_name(i['class_name']) for i in items) or '沒有找到食物'
    st.markdown(f'<div class="fc-dish">{theme.escape(names)}</div>'
                f'<div class="fc-sub">{theme.escape(result.get("notes") or "Gemini 看照片辨識；框的位置是大概的。")}</div>',
                unsafe_allow_html=True)
    st.markdown(theme.card_head('食材與份量', 'Grams'), unsafe_allow_html=True)
    manual_ids = {i['detection_id'] for i in st.session_state.get('manual_items', [])}
    for index, (row, item) in enumerate(zip(rows, items), 1):
        left, right = st.columns([3.2, 1.3], vertical_alignment='center')
        hidden = None
        if row['hidden_kcal'] is not None and row['hidden_kcal']['high'] > 0:
            hidden = f"＋ 看不到的油、糖約 {row['hidden_kcal']['mid']} kcal（依食譜估計）"
        name = row['name'] + ('（手動加入）' if row['id'] in manual_ids else '')
        left.markdown(theme.item_header(index, name, row_meta(row, item), hidden), unsafe_allow_html=True)
        placeholder = f"約 {row['grams']['mid']}" if row['grams'] and row['grams_source'] == 'rough' else '克數'
        right.number_input('克', min_value=0.0, max_value=5000.0, value=None, step=10.0, placeholder=placeholder,
                           key=f"grams_{tag}_{row['id']}", label_visibility='collapsed')
        if row['id'] not in manual_ids:
            right.checkbox('認錯了', key=f"skip_{tag}_{row['id']}")
    _, skipped, _ = current_items(result)
    for item in skipped:
        label = item.get('display_name') or item['class_name']
        st.checkbox(f'已排除「{label}」（取消勾選就恢復）', key=f"skip_{tag}_{item['detection_id']}")
    st.caption('克數框不填就用粗估；知道實際重量就直接填。')


def nutrition_card(result, rows, total, missing, macros):
    if not result:
        st.markdown(theme.nutrition_panel(None, None, '', []), unsafe_allow_html=True)
        return
    if total is None:
        reason = ('還沒有學生證可以換算大小：到下方「修正工具」框出學生證，或直接填每道菜的克數。'
                  if st.session_state.get('card_box') is None else
                  '還不能算合計：' + '、'.join(missing) + ' 沒有熱量數字，請填克數或排除。' if missing else
                  '辨識後，熱量和營養素會顯示在這裡。')
        st.markdown(theme.nutrition_panel(None, None, '', [], reason), unsafe_allow_html=True)
        return
    weight = sum(r['grams']['mid'] for r in rows if r['grams'] is not None)
    per_dish = [(r['name'], r['kcal']['mid'], r['hidden_kcal']['mid'] if r['hidden_kcal'] else 0) for r in rows]
    nutrients = macros.get('nutrients')
    macros = {k: round(macros[k], 1) for k in ('protein', 'carb', 'fat')}
    st.markdown(theme.nutrition_panel(total, macros, f'約 {weight} g', per_dish, nutrients=nutrients),
                unsafe_allow_html=True)


def tools_section(result):
    """Card box and hand-added dishes, full width because the box pickers need room."""
    image = st.session_state.get('image')
    if not result or image is None:
        return
    tag = input_tag()
    with st.expander('修正工具：重框學生證、手動加一道菜', expanded=st.session_state.get('card_box') is None):
        card_tab, add_tab = st.tabs(['學生證（換算大小）', '手動加一道菜'])
        with card_tab:
            st.caption('在照片上按住滑鼠拖曳，框住整張學生證；學生證要平放、邊緣大致和照片邊緣平行。')
            picked = box_picker(image, key='card_picker_' + tag, name='card')
            if picked is not None and picked != st.session_state.get('card_box'):
                try:
                    cm_per_px(picked)
                except ValueError as error:
                    st.warning(note_text(str(error)))
                else:
                    st.session_state['card_box'], st.session_state['card_source'] = picked, 'manual'
                    st.rerun()
        with add_tab:
            manual = st.session_state.setdefault('manual_items', [])
            food = st.selectbox('哪一道菜', list(FOOD_TEMPLATES), format_func=food_name, key='manual_food_' + tag)
            st.caption('在照片上拖曳，框住這道菜（碗裝的菜框整個碗口）。')
            box = box_picker(image, key='food_picker_' + tag, name='food')
            if st.button('加入這道菜', disabled=box is None, key='manual_add_' + tag):
                manual.append({'detection_id': f'M{len(manual) + 1}', 'class_name': food,
                               'bounding_box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), box))})
                st.rerun()
            if manual and st.button('清除手動加入的菜', key='manual_clear_' + tag):
                st.session_state['manual_items'] = []
                st.rerun()
    with st.expander('開發者細節（Gemini 原始回答；只在記憶體）'):
        st.json({k: v for k, v in result.items() if k != 'items'}, expanded=False)
        st.json(result.get('items', []), expanded=False)
        st.caption('烹調方式代碼：' + '、'.join(f'{k}={v[0]}' for k, v in COOKING_METHODS.items()))
        st.caption('食材代碼：' + '、'.join(f'{k}={v[0]}' for k, v in INGREDIENTS.items()))


def show_developer_details(result):
    energy = result.get('photo_energy_estimate')
    with st.expander('開發者細節（流程狀態、食譜候選、完整 JSON；只在記憶體，不會下載或保存）'):
        st.write('流程狀態：', result['status'])
        for issue in result.get('issues', []):
            st.error(issue['code'] + '：' + issue['message'])
        if result.get('detection_snapshot'):
            st.dataframe(result['detection_snapshot']['detections'], hide_index=True)
        st.markdown('**食譜候選與來源**（合成食譜先驗，不代表照片實際食材）')
        for obj in result.get('objects', []):
            st.write(f"物件 #{obj['detection_id']} · {obj['retrieval_status']} · {obj.get('reason') or ''}")
            for candidate in obj['recipe_candidates']:
                recipe = candidate['recipe']
                st.write(recipe['name'] + ' · SYNTHETIC / TEST_ONLY')
                st.json({'source': recipe['source'], 'retrieval_score': recipe['retrieval_score'],
                         'status': candidate['status'], 'reason': candidate.get('reason'),
                         'parsing': candidate['parsing_snapshot'], 'mapping': candidate['mapping_snapshot'],
                         'source_diagnostic': candidate.get('source_diagnostic')}, expanded=False)
        st.json({'stages': result['stages'], 'review': result['review'], 'trust': result['trust'],
                 'calorie_estimate': result['calorie_estimate'], 'photo_portion_grams': result['photo_portion_grams']},
                expanded=False)
        if energy is not None:
            st.json({key: energy[key] for key in ('status', 'reasons', 'scope', 'completeness',
                                                   'nutrition_eligible', 'production_eligible')}, expanded=False)
        st.json(result, expanded=False)


def show_result(result):
    """Synthetic demo of the strict pipeline (no model, no network)."""
    summary = summarize(result)
    st.subheader('結果摘要')
    {'success': st.success, 'warning': st.warning, 'error': st.error}[summary['level']](summary['headline'])
    st.markdown(f"### 熱量：{summary['energy_text']}")
    image = st.session_state.get('image')
    has_image = image is not None and bool(result.get('detection_snapshot'))
    left, right = st.columns([3, 2], gap='large') if has_image else (None, st.container())
    if has_image:
        caption = ('辨識框（合成展示的畫布與框都是測試用）' if summary['synthetic'] else
                   '辨識框與模型分數' if summary['foods'] else '上傳的照片（沒有辨識到食物，所以沒有框）')
        left.image(draw_predictions(image, result), caption=caption, width='stretch')
    with right:
        if summary['foods']:
            st.dataframe(summary['foods'], hide_index=True, width='stretch')
        if summary['reasons']:
            st.markdown('**為什麼沒有算出熱量／需要注意的地方**')
            st.markdown('\n'.join('- ' + text for text in summary['reasons']))
        if summary['next_steps']:
            st.markdown('**下一步可以怎麼做**')
            st.markdown('\n'.join('- ' + text for text in summary['next_steps']))
    for note in summary['notes']:
        st.caption(note)
    energy = result.get('photo_energy_estimate')
    with st.expander('各項目的份量狀態'):
        if energy is None:
            st.caption('尚未產生照片份量評估。')
        else:
            st.caption('只計照片中看得到的食材；食譜候選不是視覺證據，不會加入熱量。')
            rows = [{'項目': index, '狀態': '已估算' if item['status'] == 'estimated' else '無法估算',
                     '原因': reason_text(item['reason']) if item['reason'] else '—'}
                    for index, item in enumerate(energy['portion_assessment']['items'], 1)]
            if rows:
                st.dataframe(rows, hide_index=True, width='stretch')
    show_developer_details(result)


def photo_bytes():
    """Raw bytes of the uploaded photo, else of the chosen sample; resets per-photo state when the photo changes."""
    uploaded = st.session_state.get(f"upload_{st.session_state.get('upload_generation', 0)}")
    raw = uploaded.getvalue() if uploaded else None
    if raw:
        st.session_state.pop('sample', None)
    elif st.session_state.get('sample') and Path(st.session_state['sample']).is_file():
        raw = Path(st.session_state['sample']).read_bytes()
    if raw:
        from hashlib import sha256
        input_key = (sha256(raw).hexdigest(), False)
        if st.session_state.get('input_key') != input_key:
            for name in PER_PHOTO_KEYS:
                st.session_state.pop(name, None)
            st.session_state.pop('gemini_error', None)
            st.session_state['input_key'] = input_key
    else:
        st.session_state.pop('result', None)
        st.session_state.pop('image', None)
    return raw


def main():
    st.set_page_config(page_title='FoodCal AI 餐點熱量估算', page_icon='🍽️', layout='wide')
    st.markdown(theme.CSS, unsafe_allow_html=True)
    st.markdown(theme.header(), unsafe_allow_html=True)
    mode = st.radio('模式', [MODE_UPLOAD, MODE_DEMO], horizontal=True, label_visibility='collapsed')
    if st.session_state.get('mode') != mode:
        clear_session()
        st.session_state['mode'] = mode
    steps_slot = st.empty()
    st.markdown(theme.key_rule(), unsafe_allow_html=True)
    if mode == MODE_DEMO:
        steps_slot.markdown(theme.steps(5 if st.session_state.get('result') else 0), unsafe_allow_html=True)
        st.info('合成展示：畫布、辨識框與食譜都是測試用案例，不是真實照片分析，也不是食譜推薦。')
        if st.button('顯示展示結果', type='primary'):
            st.session_state['result'], st.session_state['image'] = fixture_demo()
        if st.button('清除本次圖片與結果'):
            clear_session()
            st.rerun()
        result = st.session_state.get('result')
        if result:
            show_result(result)
        else:
            st.caption('按「顯示展示結果」看合成案例。')
        return
    key = gemini_key()
    raw = photo_bytes()
    result = st.session_state.get('result')
    items, rows, total, missing, macros = [], [], None, [], {}
    if result:
        items, rows, total, missing, macros, _ = compute(result)
    steps_slot.markdown(theme.steps(5 if total is not None else 4 if result else 1 if raw else 0),
                        unsafe_allow_html=True)
    if total is not None:
        with st.container(key='fc_mobile_summary'):  # shown on phones only (CSS)
            st.markdown(theme.mobile_summary(total, {k: round(macros[k], 1) for k in ('protein', 'carb', 'fat')},
                                             macros.get('nutrients')), unsafe_allow_html=True)
    left, middle, right = st.columns([1.15, 1.15, 0.95], gap='large')
    with left:
        with st.container(key='fc_plate'):
            plate_card(result, raw, items, key)
    with middle:
        with st.container(key='fc_recognition'):
            recognition_card(result, rows, items)
    with right:
        nutrition_card(result, rows, total, missing, macros)
    tools_section(result)
    if result:
        unknown = [i.get('display_name') or i['class_name'] for i in items if nutrition_of(i) is None]
        if unknown:
            st.caption('、'.join(unknown) + ' 不在食材表裡，只能估體積；可以填克數，或把它加進 '
                       '`src/portion/nutrition_model.py`。')


if __name__ == '__main__':
    main()
