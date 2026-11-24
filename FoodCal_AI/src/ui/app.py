"""Streamlit page: a real photo recognised by Google Gemini with rough portions, or the synthetic demo.

Layout follows the proposal deck: workflow steps and the KEY RULE banner on top. Before a photo is chosen the page
shows one centred upload box with photo tips, and the sample photos apart below it. Once a photo is chosen it has two
columns: the photo on the left and the start card on the right, where an optional side photo can join the top one.
After recognition the left column holds the photo with numbered tags, the tools as buttons right under it (box the
card, add a missed dish; the photo itself turns into the box picker), the side photo, then the numbered dishes with
editable grams; the right column holds the nutrition panel (big kcal, macro bar, visible-vs-recipe split). Nothing a
user needs sits in a collapsed section; Gemini's raw answer shows only with ?dev=1 in the address. User images and
results stay in session memory; photos go to Gemini only after the user agrees, with the student card painted over.
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
from src.portion.card_locator import SIDE_SQUASHES, load_card_reference, locate_card
from src.portion.multi_view import side_view_geometry
from src.portion.nutrition_model import (COOKING_METHODS, INGREDIENTS, add_nutrients, empty_nutrients, macros_for_grams,
                                         nutrients_for_grams)
from src.portion.rough_volume import FOOD_TEMPLATES, cm_per_px
from src.detection.claude_vision import RecognitionError
from src.detection.gemini_vision import gemini_clients, mask_card, recognize_foods_gemini, recognize_side_view

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
PHOTO_ERRORS = {
    'invalid_upload_size': '照片超過 20 MB，請縮小後再上傳。',
    'unsupported_image_signature': '只接受 JPG、PNG、WebP。iPhone 的 HEIC 請先轉成 JPG（用手機瀏覽器上傳時通常會自動轉）。',
    'image_too_large': '照片超過 1 億像素，請縮小後再上傳。',
    'invalid_image_content': '圖片檔損壞或讀不出來，請換一張。',
}
PHOTO_ERROR_DEFAULT = '圖片無法讀取，請換一張 JPG／PNG／WebP 照片。'
SESSION_KEYS = ('result', 'image', 'input_key', 'card_box', 'card_source', 'card_auto', 'manual_items', 'photo',
                'preview', 'side', 'side_photo', 'plate_mode')
PER_PHOTO_KEYS = ('result', 'image', 'preview', 'card_box', 'card_source', 'card_auto', 'manual_items', 'side',
                  'side_photo', 'plate_mode')
SAMPLE_LABELS = {'campus_buffet': '校園自助餐', 'white_rice': '白飯'}
KEY_MISSING = ('還沒有設定 Gemini API 金鑰：在 `FoodCal_AI/.streamlit/secrets.toml` 加一行 '
               '`GEMINI_API_KEY = "你的金鑰"`，再重新整理這個頁面。')
CONSENT_LABEL = '我了解並同意把照片傳給 Google Gemini 辨識'
PRIVACY_NOTE = ('只送縮小過、沒有拍攝資訊（含 GPS）的照片，學生證會先自動塗掉。依 Google 免費版條款，'
                '照片可能被用來改進產品並由人員審閱，請不要拍到人臉。')
SIDE_HELP = ('怎麼拍：學生證不要動（背面朝上、長邊朝向你，放在菜的旁邊），蹲低從桌面上方 15–35 度拍，'
             '整盤菜和學生證都要入鏡；碗裝的菜低一點（15–25 度）更準。')
PLATE_MODES = {'card': '框學生證', 'add': '加一道菜'}  # the photo turned into a box picker, and its chip
SIDE_ANGLE_ERROR = ('側面照的角度不對：請蹲低，從桌面上方約 15–35 度拍；學生證要平放、背面朝上、長邊朝向你。')
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
    # Fits the photo card in the left column (about 590 px inside on a wide screen); a portrait photo stays on screen.
    scale = min(540 / width, 600 / height)
    shown_w, shown_h = round(width * scale), round(height * scale)
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


def sample_label(path):
    """Name shown under a sample photo: campus_buffet_01.jpg -> 校園自助餐."""
    stem = path.stem.rstrip('0123456789').rstrip('_-')
    return SAMPLE_LABELS.get(stem) or stem.replace('_', ' ') or path.stem


def change_photo():
    """Back to the upload step: forget this photo and its results; a fresh upload box replaces the old one."""
    clear_session()
    st.session_state.pop('gemini_error', None)
    st.session_state['upload_generation'] = st.session_state.get('upload_generation', 0) + 1
    st.rerun()


def side_upload_key():
    """Key of the side photo's upload box: new for each top photo, and new again after a side photo is removed."""
    return f"side_upload_{input_tag()}_{st.session_state.get('side_generation', 0)}"


def stage_side_photo():
    """Keep a newly chosen side photo (decoded once, plus a thumbnail) in session state.

    Like the top photo, its upload box is not drawn once a side photo is chosen, and a widget that is not drawn
    loses its value.
    """
    uploaded = st.session_state.get(side_upload_key())
    if not uploaded:
        return
    staged = {'name': uploaded.name}
    try:
        staged['image'] = decode_upload(uploaded.getvalue())
    except (ValueError, OSError) as error:
        staged['error'] = PHOTO_ERRORS.get(str(error), PHOTO_ERROR_DEFAULT)
    else:
        staged['thumb'] = staged['image'].copy()
        staged['thumb'].thumbnail((480, 480))
    st.session_state['side_photo'] = staged
    st.session_state.pop('side', None)


def remove_side():
    """Forget the side photo and what it measured; a fresh upload box replaces it."""
    st.session_state.pop('side_photo', None)
    st.session_state.pop('side', None)
    st.session_state['side_generation'] = st.session_state.get('side_generation', 0) + 1
    st.rerun()


def preview_image(raw):
    """{'image': decoded photo} or {'error': message}; decoded once per photo, then reused."""
    preview = st.session_state.get('preview')
    if preview is None:
        try:
            preview = {'image': decode_upload(raw)}
        except (ValueError, OSError) as error:  # show why instead of crashing the page
            preview = {'error': PHOTO_ERRORS.get(str(error), PHOTO_ERROR_DEFAULT)}
        st.session_state['preview'] = preview
    return preview


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
    rows, total, missing = portion_rows(items, st.session_state.get('card_box'), reference, typed, side_args())
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
    preview = preview_image(raw)
    if 'image' not in preview:
        st.session_state['gemini_error'] = preview['error']
        return
    image = preview['image']
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
        if name != 'side_photo':  # a side photo chosen before the start is analysed right below
            st.session_state.pop(name, None)
    st.session_state['card_auto'] = card or False
    if card:
        st.session_state['card_box'], st.session_state['card_source'] = card['quad'], 'auto'
    st.session_state['result'] = {'engine': 'gemini', 'card_masked': bool(card), **found}
    st.session_state['image'] = image
    staged = st.session_state.get('side_photo') or {}
    if staged.get('image') is not None and found.get('items'):
        analyse_side_photo(staged['image'], found['items'], key)


def upload_stage(key):
    """No photo yet: one large upload box with the photo tips; the sample photos sit apart below it."""
    with st.container(key='fc_upload'):
        st.markdown(theme.upload_intro(), unsafe_allow_html=True)
        st.file_uploader('上傳餐點照片（手機可以直接拍照）', type=['png', 'jpg', 'jpeg', 'webp'],
                         key=f"upload_{st.session_state.get('upload_generation', 0)}", label_visibility='collapsed')
        st.markdown(theme.photo_tips(), unsafe_allow_html=True)
        if not key:
            st.warning(KEY_MISSING)
    samples = sample_photos()
    if not samples:
        return
    st.markdown(theme.or_divider(), unsafe_allow_html=True)
    with st.container(key='fc_samples'):
        st.markdown(theme.samples_intro(), unsafe_allow_html=True)
        columns = st.columns(len(samples), gap='medium')
        for index, (column, path) in enumerate(zip(columns, samples), 1):
            with column:
                st.image(str(path), width='stretch')
                st.markdown(f'<div class="fc-sample-name">{theme.escape(sample_label(path))}</div>',
                            unsafe_allow_html=True)
                if st.button('用這張照片', key=f'sample_{index}', width='stretch'):
                    st.session_state['photo'] = {'raw': path.read_bytes(), 'name': sample_label(path),
                                                 'source': 'sample'}
                    st.rerun()


def recognition_caption(result):
    """Where the card came from and which model answered, under the recognised photo."""
    auto, source = st.session_state.get('card_auto'), st.session_state.get('card_source')
    card_text = ('自動找到學生證（綠框）' if source == 'auto' else '學生證：你手動框的（綠框）' if source == 'manual' else
                 '沒有找到學生證')
    score = f"（配對 {auto['score']:.2f}）" if auto and source == 'auto' else ''
    masked = '，已在送出前塗掉' if result.get('card_masked') else ''
    usage = result.get('usage') or {}
    return (f"{card_text}{score}{masked}。模型 {result.get('model')} · {result.get('latency_s')} 秒 · "
            f"{usage.get('input_tokens')}/{usage.get('output_tokens')} tokens（免費額度）")


def set_plate_mode(mode):
    st.session_state['plate_mode'] = mode
    st.rerun()


def plate_card(result, raw, items, rows, key):
    """The chosen photo. Before recognition a preview; after it the photo with numbered tags, the tool buttons and
    the side photo - or, while the card or a missed dish is being boxed, the photo as a box picker."""
    image = st.session_state.get('image')
    photo = st.session_state.get('photo') or {}
    mode = st.session_state.get('plate_mode') if result and image is not None else None
    chip = ((PLATE_MODES.get(mode) or '辨識完成') if result else
            '範例照片' if photo.get('source') == 'sample' else '已選照片')
    st.markdown(theme.card_head('Your plate · 你的餐盤', chip), unsafe_allow_html=True)
    if result and image is not None:
        if mode == 'card':
            card_picker(image)
        elif mode == 'add':
            add_dish_picker(image)
        else:
            result_photo(result, image, items)
            side_section(items, rows, key)
        return
    preview = preview_image(raw)
    if 'error' in preview:
        st.error(preview['error'])
    else:
        st.image(preview['image'], width='stretch')
    st.caption(f"{photo.get('name') or '照片'} · 俯視照 · 還沒開始辨識")


def result_photo(result, image, items):
    """The recognised photo with numbered tags; the tools are buttons right under it, not a section further down."""
    st.image(draw_items(image, items, st.session_state.get('card_box'), st.session_state.get('show_boxes', True)),
             width='stretch')
    st.caption(recognition_caption(result))
    no_card = st.session_state.get('card_box') is None
    if no_card:
        st.warning('沒有學生證就換算不了份量：按「框出學生證」在照片上框出來，或在下面直接填每道菜的克數。')
    with st.container(key='fc_plate_actions'):
        boxes, card, add, change = st.columns([1.2, 1, 1, 1], vertical_alignment='center')
        boxes.toggle('顯示辨識框', key='show_boxes')
        if card.button('框出學生證' if no_card else '重框學生證', type='primary' if no_card else 'secondary',
                       key='card_mode', width='stretch', help='在照片上拖曳框住學生證，用來換算份量'):
            set_plate_mode('card')
        if add.button('＋ 加一道菜', key='add_mode', width='stretch', help='Gemini 漏掉的菜，自己在照片上框出來'):
            set_plate_mode('add')
        if change.button('換一張照片', key='change_photo_done', width='stretch'):
            change_photo()


def card_picker(image):
    """The photo as a box picker for the student card; a usable box is used at once and the photo comes back."""
    st.markdown(theme.pick_intro('框出學生證', '在照片上按住滑鼠拖曳，框住整張學生證（平放、邊緣大致和照片邊緣平行），'
                                 '放開就會套用。'), unsafe_allow_html=True)
    picked = box_picker(image, key='card_picker_' + input_tag(), name='card')
    if picked is not None and picked != st.session_state.get('card_box'):
        try:
            cm_per_px(picked)
        except ValueError as error:
            st.warning(note_text(str(error)))
        else:
            st.session_state['card_box'], st.session_state['card_source'] = picked, 'manual'
            set_plate_mode(None)
    if st.button('取消', key='card_cancel'):
        set_plate_mode(None)


def add_dish_picker(image):
    """The photo as a box picker for a dish Gemini missed."""
    tag = input_tag()
    st.markdown(theme.pick_intro('加一道菜', '選菜名，再在照片上拖曳框住這道菜（碗裝的菜框整個碗口）。'),
                unsafe_allow_html=True)
    food = st.selectbox('哪一道菜', list(FOOD_TEMPLATES), format_func=food_name, key='manual_food_' + tag)
    box = box_picker(image, key='food_picker_' + tag, name='food')
    add, cancel = st.columns(2)
    if add.button('加入這道菜', type='primary', disabled=box is None, key='manual_add_' + tag, width='stretch'):
        manual = st.session_state.setdefault('manual_items', [])
        number = 1 + max((int(item['detection_id'][1:]) for item in manual), default=0)
        manual.append({'detection_id': f'M{number}', 'class_name': food,
                       'bounding_box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), box))})
        set_plate_mode(None)
    if cancel.button('取消', key='add_cancel', width='stretch'):
        set_plate_mode(None)


def side_drop():
    """Compact upload box for the optional side photo, with how to take it."""
    with st.container(key='fc_side_drop'):
        st.file_uploader('側面照（選填）', type=['png', 'jpg', 'jpeg', 'webp'], key=side_upload_key(),
                         label_visibility='collapsed')
    st.caption(SIDE_HELP)


def side_staged(staged, note, remove_key, retake=False):
    """A chosen side photo: thumbnail, name and a remove button (worded "another one" when it cannot be used)."""
    with st.container(key='fc_side_staged'):
        thumb, info = st.columns([1, 2.4], vertical_alignment='center')
        if staged.get('thumb') is not None:
            thumb.image(staged['thumb'], width='stretch')
        info.markdown(theme.side_staged(staged['name'], '這張不能用' if staged.get('error') else note),
                      unsafe_allow_html=True)
        if staged.get('error'):
            info.error(staged['error'])
        if info.button('換一張側面照' if retake or staged.get('error') else '移除側面照', key=remove_key):
            remove_side()


def start_card(raw, key):
    """Next to the chosen photo: the optional side photo, consent, then start; or pick another photo."""
    st.markdown(theme.start_intro(), unsafe_allow_html=True)
    staged = st.session_state.get('side_photo')
    if staged:
        side_staged(staged, '會和俯視照一起分析', 'side_remove_start')
    else:
        side_drop()
    consent = st.checkbox(CONSENT_LABEL, key='gemini_consent')
    st.caption(PRIVACY_NOTE)
    readable = 'image' in preview_image(raw)
    if st.button('開始辨識 →', type='primary', disabled=not (readable and consent and key), width='stretch'):
        st.session_state.pop('gemini_error', None)
        start_recognition(raw, key)
        st.rerun()
    if staged and not staged.get('error'):
        st.caption('會先辨識俯視照，再看側面照量高度（多一次 Gemini，約多 5–30 秒）。')
    if st.session_state.get('gemini_error'):
        st.error(st.session_state['gemini_error'])
    if not key:
        st.warning(KEY_MISSING)
    if st.button('換一張照片', key='change_photo_start', width='stretch'):
        change_photo()


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
    if row.get('volume_method') == 'side' and row.get('height_cm'):
        h = row['height_cm']
        bits.append(f"側面照：{'碗深' if row.get('shape') == 'bowl' else '高度'}約 {h['low']}–{h['high']} cm")
    bits += [note_text(code) for code in row['notes']]
    return ' · '.join(bits)


def recognition_card(result, rows, items):
    tag = input_tag()
    model = (result or {}).get('model')
    st.markdown(theme.card_head('Food recognition · 食物辨識', model.replace('gemini-', '') if model else None),
                unsafe_allow_html=True)
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
        if row['id'] in manual_ids:
            if right.button('移除', key=f"manual_remove_{tag}_{row['id']}", width='stretch'):
                st.session_state['manual_items'] = [i for i in st.session_state.get('manual_items', [])
                                                    if i['detection_id'] != row['id']]
                st.rerun()
        else:
            right.checkbox('認錯了', key=f"skip_{tag}_{row['id']}")
    _, skipped, _ = current_items(result)
    for item in skipped:
        label = item.get('display_name') or item['class_name']
        st.checkbox(f'已排除「{label}」（取消勾選就恢復）', key=f"skip_{tag}_{item['detection_id']}")
    st.caption('克數框不填就用粗估；知道實際重量就直接填。')


def nutrition_card(result, rows, total, missing, macros):
    if total is None:
        reason = ('還沒有學生證可以換算大小：按照片下面的「框出學生證」，或直接填每道菜的克數。'
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


def side_args():
    """The side photo's geometry and boxes for portion_rows, or None before a side photo was analysed."""
    side = st.session_state.get('side') or {}
    if side.get('geometry') and side.get('boxes'):
        return {'geometry': side['geometry'], 'boxes': side['boxes']}
    return None


def _quad(card):
    if isinstance(card[0], (list, tuple)):
        return card
    x0, y0, x1, y1 = card
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def run_side_view(image, card, source, items, key):
    """Read the camera angle from the card, paint the card over, ask Gemini where each dish is; store the outcome."""
    side = {'image': image, 'card': card, 'card_source': source}
    try:
        geometry = side_view_geometry(card)
    except ValueError as error:
        angle = str(error).split(':')[0] == 'side_photo_angle_out_of_range'
        side['error'] = SIDE_ANGLE_ERROR if angle else '學生證的框不正確，請重框。'
        side['need_card'] = source == 'manual' or not angle  # a bad box can be redrawn; a bad angle needs a new photo
        st.session_state['side'] = side
        return
    dishes = [(i['detection_id'], i.get('display_name') or food_name(i['class_name']), i.get('container') or 'plate')
              for i in items]
    try:
        with st.spinner('Gemini 看側面照中…'):
            found = recognize_side_view(mask_card(image, _quad(card)), dishes, clients=gemini_clients(key))
    except RecognitionError as error:
        side['error'] = GEMINI_ERRORS.get(str(error), str(error))
        st.session_state['side'] = side
        return
    st.session_state['side'] = {**side, 'geometry': geometry, **found}


def analyse_side_photo(image, items, key):
    """Find the card in the side photo, then measure; without a card the side photo waits for a hand-drawn box."""
    reference, card = card_reference(), None
    if reference is not None:
        with st.spinner('找側面照裡的學生證中…'):
            card = locate_card(image, reference, SIDE_SQUASHES)
    if card:
        run_side_view(image, card['quad'], 'auto', items, key)
    else:
        st.session_state['side'] = {'image': image, 'card': None, 'need_card': True}


def side_section(items, rows, key):
    """Under the recognised photo, always in view: add a side photo, analyse it, or see what it measured.

    The side photo measures dish heights (multi_view.py) instead of assuming them; with no dish found there is
    nothing to measure.
    """
    if not items:
        return
    tag = input_tag()
    side = st.session_state.get('side') or {}
    staged = st.session_state.get('side_photo')
    measured = side.get('geometry') is not None and side.get('boxes') is not None
    st.markdown('<div class="fc-divider"></div>' + theme.card_head('Side view · 側面照', '已量高度' if measured else '選填'),
                unsafe_allow_html=True)
    if measured:
        side_result(items, rows, side)
        if st.button('移除側面照', key='side_clear_' + tag):
            remove_side()
        return
    if not staged:
        st.markdown(theme.side_offer(), unsafe_allow_html=True)
        side_drop()
        return
    note = ('還沒分析' if not side else '要先框出側面照裡的學生證' if side.get('need_card') else '沒有量到高度')
    side_staged(staged, note, 'side_remove_' + tag, retake=side.get('error') == SIDE_ANGLE_ERROR)
    if staged.get('error'):
        return
    consent = st.session_state.get('gemini_consent')
    if not consent:  # normally given before recognition; asked here again only if it was lost
        consent = st.checkbox(CONSENT_LABEL, key='gemini_consent')
    if side.get('error'):
        st.error(side['error'])
    if side.get('need_card') and side.get('image') is not None:
        st.info('沒有自動找到側面照裡的學生證：在側面照上拖曳框出學生證，再按「用這個框繼續」。')
        box = box_picker(side['image'], key='side_card_' + tag, name='card')
        if st.button('用這個框繼續 →', type='primary', disabled=box is None or not (consent and key),
                     key='side_card_go_' + tag):
            run_side_view(side['image'], box, 'manual', items, key)
            st.rerun()
    elif side.get('error') != SIDE_ANGLE_ERROR:  # a wrong angle needs a new photo, not another try
        if st.button('再分析一次 →' if side.get('error') else '分析側面照 →', type='primary',
                     disabled=not (consent and key), key='side_go_' + tag):
            analyse_side_photo(staged['image'], items, key)
            st.rerun()


def side_result(items, rows, side):
    """The side photo with the dishes Gemini found in it, and the grams with and without it."""
    numbered, unseen = [], []
    for index, item in enumerate(items, 1):
        name = item.get('display_name') or food_name(item['class_name'])
        seen = side['boxes'].get(item['detection_id'])
        if seen:
            numbered.append({'detection_id': item['detection_id'], 'display_name': name, 'number': index,
                             'bounding_box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), seen['food']))})
        else:
            unseen.append(name)
    left, right = st.columns([1, 1.15], gap='medium')
    left.image(draw_items(side['image'], numbered, _quad(side['card'])), width='stretch')
    g = side['geometry']
    left.caption(f"相機仰角約 {g['elevation_deg']:.0f}°（建議 15–35°）· 學生證"
                 f"{'自動找到（綠框）' if side.get('card_source') == 'auto' else '你框的（綠框）'}，已在送出前塗掉 · "
                 f"模型 {side.get('model')} · {side.get('latency_s')} 秒")
    right.markdown(theme.side_table(rows), unsafe_allow_html=True)
    if unseen:
        right.caption('側面照沒看到：' + '、'.join(unseen) + '（仍用單張照片估）')
    if side.get('notes'):
        right.caption('Gemini：' + side['notes'])


def dev_mode():
    """True with ?dev=1 in the address: the team's view with Gemini's raw answer."""
    return st.query_params.get('dev') == '1'


def developer_details(result):
    """Gemini's raw answer and the code tables, drawn only in dev mode (they mean nothing to a diner)."""
    with st.container(border=True):
        st.markdown('**開發者資訊**（網址有 `?dev=1` 才顯示；只在記憶體）')
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
    """Raw bytes of the chosen photo (uploaded or a sample), or None before one is chosen.

    The bytes live in session state because the upload box goes away once a photo is chosen (a widget that is not
    drawn loses its value). Per-photo state resets when the photo changes.
    """
    uploaded = st.session_state.get(f"upload_{st.session_state.get('upload_generation', 0)}")
    if uploaded:
        st.session_state['photo'] = {'raw': uploaded.getvalue(), 'name': uploaded.name, 'source': 'upload'}
    photo = st.session_state.get('photo')
    raw = photo['raw'] if photo else None
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
    if 'gemini_consent' in st.session_state:  # keep the answer while its checkbox is not drawn (after recognition)
        st.session_state['gemini_consent'] = st.session_state['gemini_consent']
    # On by default, and kept while the switch is hidden (the photo is a box picker then).
    st.session_state['show_boxes'] = st.session_state.get('show_boxes', True)
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
    if raw:
        stage_side_photo()
    result = st.session_state.get('result')
    items, rows, total, missing, macros = [], [], None, [], {}
    if result:
        items, rows, total, missing, macros, _ = compute(result)
    steps_slot.markdown(theme.steps(5 if total is not None else 4 if result else 1 if raw else 0),
                        unsafe_allow_html=True)
    if not raw:
        upload_stage(key)
        return
    if total is not None:
        with st.container(key='fc_mobile_summary'):  # shown on phones only (CSS)
            st.markdown(theme.mobile_summary(total, {k: round(macros[k], 1) for k in ('protein', 'carb', 'fat')},
                                             macros.get('nutrients')), unsafe_allow_html=True)
    left, right = st.columns([1.45, 1], gap='large')
    with left:
        with st.container(key='fc_plate'):
            plate_card(result, raw, items, rows, key)
        if result:
            with st.container(key='fc_recognition'):
                recognition_card(result, rows, items)
    with right:
        if result:
            nutrition_card(result, rows, total, missing, macros)
        else:
            with st.container(key='fc_start'):
                start_card(raw, key)
    if result:
        unknown = [i.get('display_name') or i['class_name'] for i in items if nutrition_of(i) is None]
        if unknown:
            st.caption('、'.join(unknown) + ' 不在食材表裡，只能估體積；可以填克數，或把它加進 '
                       '`src/portion/nutrition_model.py`。')
        if dev_mode():
            developer_details(result)


if __name__ == '__main__':
    main()
