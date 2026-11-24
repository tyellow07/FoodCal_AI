"""Streamlit page: a real photo recognised by Google Gemini with rough portions, or the synthetic demo.

Layout follows the proposal deck: workflow steps and the KEY RULE banner on top. Before a photo is chosen the page
shows one centred upload box with photo tips, and the sample photos apart below it. Once a photo is chosen it has two
columns: the photo on the left and the start card on the right, where an optional side photo can join the top one.
After recognition the left column holds the photo with numbered tags, the tools as buttons right under it (box the
card, add a missed dish; the photo itself turns into the box picker), the side photo, then the numbered dishes with
editable grams; the right column holds the nutrition panel (big kcal, macro bar, visible-vs-recipe split). Nothing a
user needs sits in a collapsed section; Gemini's raw answer shows only with ?dev=1 in the address. User images and
results stay in session memory; photos go to Gemini only after the user agrees, with the student card painted over.

Every text is in the visitor's language (i18n.py, messages.py), picked at the top right; ?lang=ja in the address or
the browser's language sets the first one. Session state keeps codes, not worded messages, so a switch rewords
everything already on screen and keeps the photo, grams and consent.
"""
import sys
from pathlib import Path

# Streamlit executes this file as an entry script, from the documented project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from src.ui import theme
from src.ui.adapter import decode_upload, draw_items, draw_predictions, fixture_demo
from src.ui.explain import dish_name, food_name, reason_text, summarize
from src.ui.i18n import DEFAULT, FALLBACK, LANGUAGES, NOTES_LANGUAGE, from_locale, join, known, language, set_language, t
from src.ui.portions import note_text, nutrition_of, portion_rows, read_food_reference_csv
from src.portion.card_locator import SIDE_SQUASHES, load_card_reference, locate_card
from src.portion.multi_view import side_view_geometry
from src.portion.nutrition_model import (COOKING_METHODS, INGREDIENTS, add_nutrients, empty_nutrients, macros_for_grams,
                                         nutrients_for_grams)
from src.portion.rough_volume import FOOD_TEMPLATES, cm_per_px
from src.detection.claude_vision import RecognitionError
from src.detection.gemini_vision import gemini_clients, mask_card, recognize_foods_gemini, recognize_side_view

ROOT = Path(__file__).resolve().parents[2]
MODE_UPLOAD, MODE_DEMO = 'upload', 'demo'  # worded by mode.upload / mode.demo
PHOTO_ERRORS = ('invalid_upload_size', 'unsupported_image_signature', 'image_too_large', 'invalid_image_content')
SESSION_KEYS = ('result', 'image', 'input_key', 'card_box', 'card_source', 'card_auto', 'manual_items', 'photo',
                'preview', 'side', 'side_photo', 'plate_mode')
PER_PHOTO_KEYS = ('result', 'image', 'preview', 'card_box', 'card_source', 'card_auto', 'manual_items', 'side',
                  'side_photo', 'plate_mode')
SAMPLE_NAMES = ('campus_buffet', 'white_rice')  # sample photo stems with a name in messages.py (sample.<stem>)
FOOD_REFERENCE_PATH = ROOT / 'data/food_reference/food_reference.csv'
CARD_REFERENCE_DIR = ROOT / 'data/card_reference'
SAMPLES_DIR = ROOT / 'data/samples'  # optional local sample photos (Git-ignored)


def clear_session():
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)


def input_tag():
    return str(st.session_state.get('input_key', ('none',))[0])[:12]


def photo_error(error):
    """Code for a photo that cannot be used, from decode_upload's error; error_text words it."""
    code = str(error)
    return code if code in PHOTO_ERRORS else 'photo_default'


def error_text(code):
    """An error kept in session state as a code, worded in the page language when it is shown.

    Codes: photo errors, Gemini's RecognitionError codes, side_angle and side_bad_box; an unknown code (an error
    message from elsewhere) shows as it is.
    """
    return t('err.' + code) if known('err.' + code) else code


def page_language():
    """This run's language: the picker's value, else ?lang= in the address, else the browser's language.

    A browser language the page does not have gets English; no browser language at all (tests) gets Chinese.
    """
    if st.session_state.get('lang') not in LANGUAGES:
        wanted = st.query_params.get('lang')
        locale = st.context.locale
        st.session_state['lang'] = (wanted if wanted in LANGUAGES else
                                    from_locale(locale) or (FALLBACK if locale else DEFAULT))
    return st.session_state['lang']


def remember_language():
    """Put the chosen language in the address, so a reload or a shared link keeps it."""
    st.query_params['lang'] = st.session_state['lang']


def language_picker():
    """The language menu; CSS pins it to the top right corner of the window."""
    with st.container(key='fc_lang'):
        st.selectbox(t('lang.label'), list(LANGUAGES), format_func=LANGUAGES.get, key='lang',
                     on_change=remember_language, label_visibility='collapsed')


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
        st.warning(t('card.reference_unreadable'))
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
    """Name shown under a sample photo, in the page language: campus_buffet_01.jpg -> 校園自助餐."""
    stem = path.stem.rstrip('0123456789').rstrip('_-')
    return t('sample.' + stem) if stem in SAMPLE_NAMES else stem.replace('_', ' ') or path.stem


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
        staged['error'] = photo_error(error)
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
    """{'image': decoded photo} or {'error': code}; decoded once per photo, then reused."""
    preview = st.session_state.get('preview')
    if preview is None:
        try:
            preview = {'image': decode_upload(raw)}
        except (ValueError, OSError) as error:  # show why instead of crashing the page
            preview = {'error': photo_error(error)}
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
        st.error(t('reference.bad_format', error=str(error)))
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
        with st.spinner(t('spin.card')):
            card = locate_card(image, reference)
    outgoing = mask_card(image, card['quad']) if card else image
    try:
        with st.spinner(t('spin.gemini')):
            found = recognize_foods_gemini(outgoing, clients=gemini_clients(key),
                                           notes_language=NOTES_LANGUAGE[language()])
    except RecognitionError as error:
        st.session_state['gemini_error'] = str(error)
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
        st.file_uploader(t('upload.label'), type=['png', 'jpg', 'jpeg', 'webp'],
                         key=f"upload_{st.session_state.get('upload_generation', 0)}", label_visibility='collapsed')
        st.markdown(theme.photo_tips(), unsafe_allow_html=True)
        if not key:
            st.warning(t('key.missing'))
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
                if st.button(t('sample.use'), key=f'sample_{index}', width='stretch'):
                    # The file name, not its label, so the label follows a later language switch.
                    st.session_state['photo'] = {'raw': path.read_bytes(), 'name': path.name, 'source': 'sample'}
                    st.rerun()


def recognition_caption(result):
    """Where the card came from and which model answered, under the recognised photo."""
    auto, source = st.session_state.get('card_auto'), st.session_state.get('card_source')
    card_text = t('caption.card_auto' if source == 'auto' else 'caption.card_manual' if source == 'manual' else
                  'caption.card_none')
    score = t('caption.score', score=f"{auto['score']:.2f}") if auto and source == 'auto' else ''
    masked = t('caption.masked') if result.get('card_masked') else ''
    usage = result.get('usage') or {}
    return card_text + score + masked + t('caption.model', model=result.get('model'), seconds=result.get('latency_s'),
                                          tokens_in=usage.get('input_tokens'), tokens_out=usage.get('output_tokens'))


def set_plate_mode(mode):
    st.session_state['plate_mode'] = mode
    st.rerun()


def photo_name(photo):
    """Name under the chosen photo: a sample's label in the page language, else the uploaded file's name."""
    if photo.get('source') == 'sample' and photo.get('name'):
        return sample_label(Path(photo['name']))
    return photo.get('name') or t('photo.fallback_name')


def plate_card(result, raw, items, rows, key):
    """The chosen photo. Before recognition a preview; after it the photo with numbered tags, the tool buttons and
    the side photo - or, while the card or a missed dish is being boxed, the photo as a box picker."""
    image = st.session_state.get('image')
    photo = st.session_state.get('photo') or {}
    mode = st.session_state.get('plate_mode') if result and image is not None else None
    chip = ((t('plate_mode.' + mode) if mode in ('card', 'add') else t('chip.recognised')) if result else
            t('chip.sample') if photo.get('source') == 'sample' else t('chip.chosen'))
    st.markdown(theme.card_head(t('head.plate'), chip), unsafe_allow_html=True)
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
        st.error(error_text(preview['error']))
    else:
        st.image(preview['image'], width='stretch')
    st.caption(t('photo.waiting', name=photo_name(photo)))


def result_photo(result, image, items):
    """The recognised photo with numbered tags; the tools are buttons right under it, not a section further down."""
    tagged = [{**item, 'display_name': dish_name(item)} for item in items]  # tags in the page language
    st.image(draw_items(image, tagged, st.session_state.get('card_box'), st.session_state.get('show_boxes', True)),
             width='stretch')
    st.caption(recognition_caption(result))
    no_card = st.session_state.get('card_box') is None
    if no_card:
        st.warning(t('warn.no_card'))
    with st.container(key='fc_plate_actions'):
        boxes, card, add, change = st.columns([1.2, 1, 1, 1], vertical_alignment='center')
        boxes.toggle(t('toggle.boxes'), key='show_boxes')
        if card.button(t('btn.card_box') if no_card else t('btn.card_rebox'), type='primary' if no_card else 'secondary',
                       key='card_mode', width='stretch', help=t('btn.card_help')):
            set_plate_mode('card')
        if add.button(t('btn.add_dish'), key='add_mode', width='stretch', help=t('btn.add_help')):
            set_plate_mode('add')
        if change.button(t('btn.change_photo'), key='change_photo_done', width='stretch'):
            change_photo()


def card_picker(image):
    """The photo as a box picker for the student card; a usable box is used at once and the photo comes back."""
    st.markdown(theme.pick_intro(t('btn.card_box'), t('pick.card_lead')), unsafe_allow_html=True)
    picked = box_picker(image, key='card_picker_' + input_tag(), name='card')
    if picked is not None and picked != st.session_state.get('card_box'):
        try:
            cm_per_px(picked)
        except ValueError as error:
            st.warning(note_text(str(error)))
        else:
            st.session_state['card_box'], st.session_state['card_source'] = picked, 'manual'
            set_plate_mode(None)
    if st.button(t('btn.cancel'), key='card_cancel'):
        set_plate_mode(None)


def add_dish_picker(image):
    """The photo as a box picker for a dish Gemini missed."""
    tag = input_tag()
    st.markdown(theme.pick_intro(t('plate_mode.add'), t('pick.add_lead')), unsafe_allow_html=True)
    names = {food: food_name(food) for food in FOOD_TEMPLATES}  # worded now; one menu per language (see main)
    food = st.selectbox(t('pick.which_dish'), list(names), format_func=names.get,
                        key=f'manual_food_{tag}_{language()}')
    box = box_picker(image, key='food_picker_' + tag, name='food')
    add, cancel = st.columns(2)
    if add.button(t('btn.add_this'), type='primary', disabled=box is None, key='manual_add_' + tag, width='stretch'):
        manual = st.session_state.setdefault('manual_items', [])
        number = 1 + max((int(item['detection_id'][1:]) for item in manual), default=0)
        manual.append({'detection_id': f'M{number}', 'class_name': food,
                       'bounding_box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), box))})
        set_plate_mode(None)
    if cancel.button(t('btn.cancel'), key='add_cancel', width='stretch'):
        set_plate_mode(None)


def side_drop():
    """Compact upload box for the optional side photo, with how to take it."""
    with st.container(key='fc_side_drop'):
        st.file_uploader(t('side.upload_label'), type=['png', 'jpg', 'jpeg', 'webp'], key=side_upload_key(),
                         label_visibility='collapsed')
    st.caption(t('side.help'))


def side_staged(staged, note, remove_key, retake=False):
    """A chosen side photo: thumbnail, name and a remove button (worded "another one" when it cannot be used)."""
    with st.container(key='fc_side_staged'):
        thumb, info = st.columns([1, 2.4], vertical_alignment='center')
        if staged.get('thumb') is not None:
            thumb.image(staged['thumb'], width='stretch')
        info.markdown(theme.side_staged(staged['name'], t('side.unusable') if staged.get('error') else note),
                      unsafe_allow_html=True)
        if staged.get('error'):
            info.error(error_text(staged['error']))
        if info.button(t('btn.side_retake') if retake or staged.get('error') else t('btn.side_remove'), key=remove_key):
            remove_side()


def start_card(raw, key):
    """Next to the chosen photo: the optional side photo, consent, then start; or pick another photo."""
    st.markdown(theme.start_intro(), unsafe_allow_html=True)
    staged = st.session_state.get('side_photo')
    if staged:
        side_staged(staged, t('side.note_with_top'), 'side_remove_start')
    else:
        side_drop()
    consent = st.checkbox(t('consent.label'), key='gemini_consent')
    st.caption(t('consent.privacy'))
    readable = 'image' in preview_image(raw)
    if st.button(t('btn.start'), type='primary', disabled=not (readable and consent and key), width='stretch'):
        st.session_state.pop('gemini_error', None)
        start_recognition(raw, key)
        st.rerun()
    if staged and not staged.get('error'):
        st.caption(t('start.side_caption'))
    if st.session_state.get('gemini_error'):
        st.error(error_text(st.session_state['gemini_error']))
    if not key:
        st.warning(t('key.missing'))
    if st.button(t('btn.change_photo'), key='change_photo_start', width='stretch'):
        change_photo()


def row_meta(row, item):
    bits = []
    nutrition = row['nutrition']
    if nutrition:
        keys = nutrition.get('part_keys') or [None] * len(nutrition['parts'])
        parts = join(f"{t('ingredient.' + key, name) if key else name} {share}%"
                     for (name, share), key in zip(nutrition['parts'], keys))
        method = (t('method.' + nutrition['method_key'], nutrition['method']) if nutrition.get('method_key') else
                  nutrition['method'])
        bits.append(f'{method} · {parts}')
    if item.get('confidence'):
        code = 'conf.' + item['confidence']
        bits.append(t(code) if known(code) else item['confidence'])
    if row['grams'] is not None:
        source = (t('meta.typed') if row['grams_source'] == 'user' else
                  t('meta.grams_range', low=row['grams']['low'], high=row['grams']['high']))
        bits.append(f"{source} · {t('meta.visible_kcal', kcal=row['kcal']['mid'] if row['kcal'] else '—')}")
    if row.get('volume_method') == 'side' and row.get('height_cm'):
        h = row['height_cm']
        bits.append(t('meta.side_bowl' if row.get('shape') == 'bowl' else 'meta.side_height', low=h['low'], high=h['high']))
    bits += [note_text(code) for code in row['notes']]
    return ' · '.join(bits)


def recognition_card(result, rows, items):
    tag = input_tag()
    model = (result or {}).get('model')
    st.markdown(theme.card_head(t('head.recognition'), model.replace('gemini-', '') if model else None),
                unsafe_allow_html=True)
    names = join(dish_name(i) for i in items) or t('recog.none')
    st.markdown(f'<div class="fc-dish">{theme.escape(names)}</div>'
                f'<div class="fc-sub">{theme.escape(result.get("notes") or t("recog.sub_default"))}</div>',
                unsafe_allow_html=True)
    st.markdown(theme.card_head(t('head.grams'), 'Grams'), unsafe_allow_html=True)
    manual_ids = {i['detection_id'] for i in st.session_state.get('manual_items', [])}
    for index, (row, item) in enumerate(zip(rows, items), 1):
        left, right = st.columns([3.2, 1.3], vertical_alignment='center')
        hidden = None
        if row['hidden_kcal'] is not None and row['hidden_kcal']['high'] > 0:
            hidden = t('recog.hidden', kcal=row['hidden_kcal']['mid'])
        name = row['name'] + (t('recog.manual_tag') if row['id'] in manual_ids else '')
        left.markdown(theme.item_header(index, name, row_meta(row, item), hidden), unsafe_allow_html=True)
        placeholder = (t('recog.placeholder_about', grams=row['grams']['mid'])
                       if row['grams'] and row['grams_source'] == 'rough' else t('recog.placeholder_grams'))
        right.number_input(t('recog.grams_label'), min_value=0.0, max_value=5000.0, value=None, step=10.0,
                           placeholder=placeholder, key=f"grams_{tag}_{row['id']}", label_visibility='collapsed')
        if row['id'] in manual_ids:
            if right.button(t('btn.remove'), key=f"manual_remove_{tag}_{row['id']}", width='stretch'):
                st.session_state['manual_items'] = [i for i in st.session_state.get('manual_items', [])
                                                    if i['detection_id'] != row['id']]
                st.rerun()
        else:
            right.checkbox(t('recog.wrong'), key=f"skip_{tag}_{row['id']}")
    _, skipped, _ = current_items(result)
    for item in skipped:
        st.checkbox(t('recog.excluded', label=dish_name(item)), key=f"skip_{tag}_{item['detection_id']}")
    st.caption(t('recog.grams_caption'))


def nutrition_card(result, rows, total, missing, macros):
    if total is None:
        reason = (t('nut.reason_no_card') if st.session_state.get('card_box') is None else
                  t('nut.reason_missing', names=join(missing)) if missing else t('nut.reason_wait'))
        st.markdown(theme.nutrition_panel(None, None, '', [], reason), unsafe_allow_html=True)
        return
    weight = sum(r['grams']['mid'] for r in rows if r['grams'] is not None)
    per_dish = [(r['name'], r['kcal']['mid'], r['hidden_kcal']['mid'] if r['hidden_kcal'] else 0) for r in rows]
    nutrients = macros.get('nutrients')
    macros = {k: round(macros[k], 1) for k in ('protein', 'carb', 'fat')}
    st.markdown(theme.nutrition_panel(total, macros, t('nut.weight', grams=weight), per_dish, nutrients=nutrients),
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
        side['error'] = 'side_angle' if angle else 'side_bad_box'
        side['need_card'] = source == 'manual' or not angle  # a bad box can be redrawn; a bad angle needs a new photo
        st.session_state['side'] = side
        return
    dishes = [(i['detection_id'], dish_name(i), i.get('container') or 'plate') for i in items]
    try:
        with st.spinner(t('spin.side')):
            found = recognize_side_view(mask_card(image, _quad(card)), dishes, clients=gemini_clients(key),
                                        notes_language=NOTES_LANGUAGE[language()])
    except RecognitionError as error:
        side['error'] = str(error)
        st.session_state['side'] = side
        return
    st.session_state['side'] = {**side, 'geometry': geometry, **found}


def analyse_side_photo(image, items, key):
    """Find the card in the side photo, then measure; without a card the side photo waits for a hand-drawn box."""
    reference, card = card_reference(), None
    if reference is not None:
        with st.spinner(t('spin.side_card')):
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
    st.markdown('<div class="fc-divider"></div>' +
                theme.card_head(t('head.side'), t('chip.measured') if measured else t('chip.optional')),
                unsafe_allow_html=True)
    if measured:
        side_result(items, rows, side)
        if st.button(t('btn.side_remove'), key='side_clear_' + tag):
            remove_side()
        return
    if not staged:
        st.markdown(theme.side_offer(), unsafe_allow_html=True)
        side_drop()
        return
    note = t('side.note_pending' if not side else 'side.note_need_card' if side.get('need_card') else
             'side.note_unmeasured')
    side_staged(staged, note, 'side_remove_' + tag, retake=side.get('error') == 'side_angle')
    if staged.get('error'):
        return
    consent = st.session_state.get('gemini_consent')
    if not consent:  # normally given before recognition; asked here again only if it was lost
        consent = st.checkbox(t('consent.label'), key='gemini_consent')
    if side.get('error'):
        st.error(error_text(side['error']))
    if side.get('need_card') and side.get('image') is not None:
        st.info(t('side.info_need_card'))
        box = box_picker(side['image'], key='side_card_' + tag, name='card')
        if st.button(t('btn.side_card_go'), type='primary', disabled=box is None or not (consent and key),
                     key='side_card_go_' + tag):
            run_side_view(side['image'], box, 'manual', items, key)
            st.rerun()
    elif side.get('error') != 'side_angle':  # a wrong angle needs a new photo, not another try
        if st.button(t('btn.side_retry') if side.get('error') else t('btn.side_go'), type='primary',
                     disabled=not (consent and key), key='side_go_' + tag):
            analyse_side_photo(staged['image'], items, key)
            st.rerun()


def side_result(items, rows, side):
    """The side photo with the dishes Gemini found in it, and the grams with and without it."""
    numbered, unseen = [], []
    for index, item in enumerate(items, 1):
        name = dish_name(item)
        seen = side['boxes'].get(item['detection_id'])
        if seen:
            numbered.append({'detection_id': item['detection_id'], 'display_name': name, 'number': index,
                             'bounding_box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), seen['food']))})
        else:
            unseen.append(name)
    left, right = st.columns([1, 1.15], gap='medium')
    left.image(draw_items(side['image'], numbered, _quad(side['card'])), width='stretch')
    g = side['geometry']
    card = t('side.card_auto') if side.get('card_source') == 'auto' else t('side.card_manual')
    left.caption(t('side.caption', deg=f"{g['elevation_deg']:.0f}", card=card, model=side.get('model'),
                   seconds=side.get('latency_s')))
    right.markdown(theme.side_table(rows), unsafe_allow_html=True)
    if unseen:
        right.caption(t('side.unseen', names=join(unseen)))
    if side.get('notes'):
        right.caption(t('side.gemini_notes', notes=side['notes']))


def dev_mode():
    """True with ?dev=1 in the address: the team's view with Gemini's raw answer."""
    return st.query_params.get('dev') == '1'


def developer_details(result):
    """Gemini's raw answer and the code tables, drawn only in dev mode (they mean nothing to a diner)."""
    with st.container(border=True):
        st.markdown(t('dev.title'))
        st.json({k: v for k, v in result.items() if k != 'items'}, expanded=False)
        st.json(result.get('items', []), expanded=False)
        st.caption(t('dev.methods') + '、'.join(f'{k}={v[0]}' for k, v in COOKING_METHODS.items()))
        st.caption(t('dev.ingredients') + '、'.join(f'{k}={v[0]}' for k, v in INGREDIENTS.items()))


def show_developer_details(result):
    energy = result.get('photo_energy_estimate')
    with st.expander(t('dev.demo_expander')):
        st.write(t('dev.status'), result['status'])
        for issue in result.get('issues', []):
            st.error(issue['code'] + '：' + issue['message'])
        if result.get('detection_snapshot'):
            st.dataframe(result['detection_snapshot']['detections'], hide_index=True)
        st.markdown(t('dev.recipes'))
        for obj in result.get('objects', []):
            st.write(t('dev.object', id=obj['detection_id'], status=obj['retrieval_status'], reason=obj.get('reason') or ''))
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
    st.subheader(t('demo.summary'))
    {'success': st.success, 'warning': st.warning, 'error': st.error}[summary['level']](summary['headline'])
    st.markdown('### ' + t('demo.energy', text=summary['energy_text']))
    image = st.session_state.get('image')
    has_image = image is not None and bool(result.get('detection_snapshot'))
    left, right = st.columns([3, 2], gap='large') if has_image else (None, st.container())
    if has_image:
        caption = t('demo.caption_synthetic' if summary['synthetic'] else
                    'demo.caption_boxes' if summary['foods'] else 'demo.caption_empty')
        left.image(draw_predictions(image, result), caption=caption, width='stretch')
    with right:
        if summary['foods']:
            st.dataframe(summary['foods'], hide_index=True, width='stretch')
        if summary['reasons']:
            st.markdown(t('demo.why'))
            st.markdown('\n'.join('- ' + text for text in summary['reasons']))
        if summary['next_steps']:
            st.markdown(t('demo.next'))
            st.markdown('\n'.join('- ' + text for text in summary['next_steps']))
    for note in summary['notes']:
        st.caption(note)
    energy = result.get('photo_energy_estimate')
    with st.expander(t('demo.portions_expander')):
        if energy is None:
            st.caption(t('demo.no_assessment'))
        else:
            st.caption(t('demo.visible_only'))
            rows = [{t('demo.col_item'): index,
                     t('demo.col_status'): t('demo.estimated' if item['status'] == 'estimated' else 'demo.not_estimable'),
                     t('demo.col_reason'): reason_text(item['reason']) if item['reason'] else '—'}
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
    set_language(page_language())
    st.set_page_config(page_title=t('page.title'), page_icon='🍽️', layout='wide')
    st.markdown(theme.CSS + theme.lang_css(), unsafe_allow_html=True)
    language_picker()
    st.markdown(theme.header(), unsafe_allow_html=True)
    # The browser keeps a radio's choice by its wording, so each language has its own radio, opened on the mode in use.
    modes = {code: t('mode.' + code) for code in (MODE_UPLOAD, MODE_DEMO)}
    current = st.session_state.get('mode', MODE_UPLOAD)
    mode = st.radio(t('mode.label'), list(modes), index=list(modes).index(current) if current in modes else 0,
                    format_func=modes.get, key='mode_choice_' + language(), horizontal=True,
                    label_visibility='collapsed')
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
        st.info(t('demo.info'))
        if st.button(t('btn.demo_show'), type='primary'):
            st.session_state['result'], st.session_state['image'] = fixture_demo()
        if st.button(t('btn.demo_clear')):
            clear_session()
            st.rerun()
        result = st.session_state.get('result')
        if result:
            show_result(result)
        else:
            st.caption(t('demo.hint'))
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
        unknown = [dish_name(i) for i in items if nutrition_of(i) is None]
        if unknown:
            st.caption(t('unknown.caption', names=join(unknown)))
        if dev_mode():
            developer_details(result)


if __name__ == '__main__':
    main()
