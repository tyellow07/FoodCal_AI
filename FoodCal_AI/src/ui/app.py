"""Streamlit local demo. Default synthetic demo; user images/results are session-memory only."""
import sys
from pathlib import Path

# Streamlit executes this file as an entry script, from the documented project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from src.ui.adapter import fixture_demo, decode_upload, analyze_upload, draw_predictions
from src.ui.explain import reason_text, summarize, supported_foods_markdown
from src.ui.portions import note_text, portion_rows, range_text, read_food_reference_csv
from src.portion.card_locator import load_card_reference, locate_card
from src.portion.rough_volume import FOOD_TEMPLATES, cm_per_px
from src.ui.explain import food_name
from src.detection import FoodDetector
from src.detection.claude_vision import RecognitionError, make_client, recognize_foods
from src.detection.model import DEFAULT_CLASSES_PATH, DEFAULT_MODEL_PATH

MODE_DEMO = '合成展示（不需模型）'
MODE_UPLOAD = '上傳照片辨識'
ENGINE_CLAUDE = 'Claude Opus 5.5（雲端，認得大部分食物）'
ENGINE_YOLO = 'YOLO（本機，只認得 15 種菜）'
CONFIDENCE_TEXT = {'high': '高', 'medium': '中', 'low': '低'}
CLAUDE_ERRORS = {
    'claude_credentials_missing': '這台電腦還沒有 Claude API 的登入資訊：在終端機執行 `ant auth login`，'
                                  '或在 `.streamlit/secrets.toml` 寫一行 `ANTHROPIC_API_KEY = "..."`。',
    'claude_authentication_failed': 'Claude API 的登入資訊無效或過期，請重新執行 `ant auth login`。',
    'claude_permission_denied': '這個 API 帳戶沒有使用 claude-opus-5-5 的權限。',
    'claude_credit_or_billing': 'Claude API 帳戶額度不足，請到 Claude Console 加值。',
    'claude_rate_limited': 'Claude API 暫時太忙（流量限制），請稍等再試。',
    'claude_network_error': '連不上 Claude API，請檢查網路。',
    'claude_service_error': 'Claude API 暫時出錯，請稍後再試。',
    'claude_refused': 'Claude 拒絕處理這張照片，請換一張。',
    'claude_answer_truncated': 'Claude 的回答被截斷，請再試一次。',
    'claude_answer_unreadable': 'Claude 的回答格式不對，請再試一次。',
    'claude_bad_request': '送給 Claude 的請求不被接受，請換一張照片再試。',
}
SESSION_KEYS = ('result', 'image', 'input_key', 'detector', 'card_box', 'card_source', 'card_auto', 'manual_items')
PER_PHOTO_KEYS = ('result', 'image', 'card_box', 'card_source', 'card_auto', 'manual_items')
FOOD_REFERENCE_PATH = Path(__file__).resolve().parents[2] / 'data/food_reference/food_reference.csv'
CARD_REFERENCE_DIR = Path(__file__).resolve().parents[2] / 'data/card_reference'


def weights_available():
    """Cheap file check only; the model is never loaded here."""
    return Path(DEFAULT_MODEL_PATH).is_file() and Path(DEFAULT_CLASSES_PATH).is_file()


def clear_session():
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)


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


def auto_find_card(image):
    """Look for the card once per photo, before the result image is drawn; a hand-drawn box always wins."""
    if image is None or 'card_auto' in st.session_state:
        return
    reference = card_reference()
    found = None
    if reference is not None:
        with st.spinner('找學生證中…'):
            found = locate_card(image, reference)
    st.session_state['card_auto'] = found or False
    if found and st.session_state.get('card_box') is None:
        st.session_state['card_box'], st.session_state['card_source'] = found['quad'], 'auto'


def card_reference():
    """Logo template for automatic card finding, or None when it has not been set up on this computer."""
    try:
        return load_card_reference(CARD_REFERENCE_DIR)
    except (ValueError, OSError):
        st.warning('學生證 logo 設定檔讀不到，請重新執行 `python -m tools.setup_card_reference`。')
        return None


def detections_of(result):
    return list((result.get('detection_snapshot') or {}).get('detections', []))


def show_portions(base_items):
    """Rough volume x density per recognised food, or grams the user types in."""
    image = st.session_state.get('image')
    input_tag = str(st.session_state.get('input_key', ('none',))[0])[:12]
    st.subheader('份量與熱量（粗估）')
    st.caption('用學生證當比例尺、依食物形狀估體積，再乘上密度得到重量。沒有用秤重資料驗證，'
               '只是大概的數字；知道實際重量的話，直接在右邊填入。')
    if image is not None:
        reference = card_reference()
        auto = st.session_state.get('card_auto')
        with st.expander('① 學生證（用來換算大小）', expanded=st.session_state.get('card_source') != 'auto'):
            if st.session_state.get('card_source') == 'auto':
                st.success(f"自動找到學生證（logo 配對分數 {auto['score']:.2f}），就是結果圖上的綠框。"
                           '框得不對的話，在下面照片上拖曳重框。')
            elif reference is None:
                st.caption('還沒設定學生證 logo（`python -m tools.setup_card_reference`），所以要手動框。')
            elif auto is False:
                st.info('沒有自動找到學生證：要背面朝上、左下角的悠遊卡 logo 清楚入鏡。請在下面照片上拖曳框出學生證。')
            st.caption('手動框：在照片上按住滑鼠拖曳，框住整張學生證；學生證要平放、邊緣大致和照片邊緣平行。')
            picked = box_picker(image, key='card_picker_' + input_tag, name='card')
            if picked is not None and picked != st.session_state.get('card_box'):
                try:
                    cm_per_px(picked)
                except ValueError as error:
                    st.warning(note_text(str(error)))
                else:
                    st.session_state['card_box'], st.session_state['card_source'] = picked, 'manual'
            if st.session_state.get('card_source') == 'manual':
                st.success('已框出學生證（結果圖上的綠框）。要重框的話，直接再拖一次。')
        manual = st.session_state.setdefault('manual_items', [])
        with st.expander('② 沒辨識到或認錯？手動加一道菜', expanded=not base_items and not manual):
            food = st.selectbox('哪一道菜', list(FOOD_TEMPLATES), format_func=food_name, key='manual_food_' + input_tag)
            st.caption('在照片上拖曳，框住這道菜（碗裝的菜框整個碗口）。')
            box = box_picker(image, key='food_picker_' + input_tag, name='food')
            if st.button('加入這道菜', disabled=box is None, key='manual_add_' + input_tag):
                manual.append({'detection_id': f'M{len(manual) + 1}', 'class_name': food,
                               'bounding_box': dict(zip(('x_min', 'y_min', 'x_max', 'y_max'), box))})
                st.rerun()
            if manual and st.button('清除手動加入的菜', key='manual_clear_' + input_tag):
                st.session_state['manual_items'] = []
                st.rerun()
    try:
        reference = read_food_reference_csv(FOOD_REFERENCE_PATH)
    except ValueError as error:
        st.error('密度與熱量表格式不對：' + str(error))
        reference = {}
    if not reference:
        st.info('還沒有密度與熱量表（`data/food_reference/food_reference.csv`），所以只能估體積；'
                '格式見 `docs/examples/food_reference_template.csv`。')
    detections = list(base_items)
    skipped = {d['detection_id'] for d in detections if st.session_state.get(f"skip_{input_tag}_{d['detection_id']}")}
    for detection in detections:
        if detection['detection_id'] in skipped:
            left, right = st.columns([3, 2], gap='large')
            left.markdown(f"~~#{detection['detection_id']} {food_name(detection['class_name'])}~~（已排除，不算進合計）")
            right.checkbox('辨識錯了，不要算', key=f"skip_{input_tag}_{detection['detection_id']}")
    items = [d for d in detections if d['detection_id'] not in skipped] + st.session_state.get('manual_items', [])
    if not items:
        st.caption('還沒有任何一道菜：用上面的「手動加一道菜」加入。')
        return
    typed = {item['detection_id']: st.session_state.get(f"grams_{input_tag}_{item['detection_id']}") for item in items}
    rows, total, missing = portion_rows(items, st.session_state.get('card_box'), reference, typed)
    manual_ids = {item['detection_id'] for item in st.session_state.get('manual_items', [])}
    for row in rows:
        left, right = st.columns([3, 2], gap='large')
        with left:
            st.markdown(f"**#{row['id']} {row['name']}**" + ('（手動加入）' if row['id'] in manual_ids else ''))
            lines = [f"- 體積：{range_text(row['volume_ml'], 'mL')}"]
            source = '（你填的）' if row['grams_source'] == 'user' else ''
            lines.append(f"- 重量：{range_text(row['grams'], 'g')}{source}")
            lines.append(f"- 熱量：{range_text(row['kcal'], 'kcal')}")
            lines += ['- ' + note_text(code) for code in row['notes']]
            st.markdown('\n'.join(lines))
        with right:
            st.number_input('自己填重量（克）', min_value=0.0, max_value=5000.0, value=None, step=10.0,
                            key=f"grams_{input_tag}_{row['id']}", placeholder='不填就用粗估')
            if row['id'] not in manual_ids:
                st.checkbox('辨識錯了，不要算', key=f"skip_{input_tag}_{row['id']}")
    if total is not None:
        st.markdown(f"### 合計：{range_text(total, 'kcal')}")
        st.caption('粗估值，僅供參考，不是醫療或營養建議。')
    elif rows:
        st.caption('還不能算合計：' + '、'.join(missing) + ' 沒有熱量數字。')


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


def show_upload_result(result):
    """Real photo: detections first, then the rough portion section; the strict result moves to an expander."""
    summary = summarize(result)
    image = st.session_state.get('image')
    auto_find_card(image)
    st.subheader('辨識結果')
    left, right = st.columns([3, 2], gap='large')
    if image is not None:
        left.image(draw_predictions(image, result, st.session_state.get('card_box'),
                                    st.session_state.get('manual_items')),
                   caption='橘框：模型辨識　藍框：手動加入　綠框：學生證', width='stretch')
    if summary['foods']:
        right.dataframe(summary['foods'], hide_index=True, width='stretch')
        right.caption('模型只認得 15 種菜；分數越高越有把握。')
    else:
        right.warning(summary['headline'])
        right.markdown('\n'.join('- ' + text for text in summary['reasons']))
    right.caption('白飯、麵這類單一食材不在模型的 15 種菜裡；認錯或漏掉的菜，可以在下面排除或「手動加一道菜」。')
    show_portions(detections_of(result))
    with st.expander('嚴格模式結果（要有秤重校準才會給熱量）'):
        st.markdown(f"**{summary['headline']}**　熱量：{summary['energy_text']}")
        st.markdown('\n'.join('- ' + text for text in summary['reasons']))
    show_developer_details(result)


def claude_client():
    """Client from .streamlit/secrets.toml when it holds a key, else ANTHROPIC_API_KEY or an `ant auth login` profile."""
    try:
        key = st.secrets.get('ANTHROPIC_API_KEY')
    except Exception:  # no secrets file: fall back to the environment and the CLI profile
        key = None
    return make_client(api_key=key)


def show_claude_result(result):
    """Claude's foods with boxes, then the same rough portion section as the YOLO path."""
    image = st.session_state.get('image')
    auto_find_card(image)
    items = result['items']
    st.subheader('辨識結果（Claude Opus 5.5）')
    left, right = st.columns([3, 2], gap='large')
    if image is not None:
        canvas = {'detection_snapshot': {'image': {'width': image.width, 'height': image.height}, 'detections': []}}
        left.image(draw_predictions(image, canvas, st.session_state.get('card_box'),
                                    items + st.session_state.get('manual_items', [])),
                   caption='藍框：Claude 辨識（C）與手動加入（M）　綠框：學生證', width='stretch')
    if items:
        right.dataframe([{'編號': i['detection_id'], '食物': i['display_name'],
                          '看得到的食材': '、'.join(i['visible_ingredients']) or '—',
                          '把握': CONFIDENCE_TEXT.get(i['confidence'], i['confidence'])} for i in items],
                        hide_index=True, width='stretch')
    else:
        right.warning('Claude 沒有在照片裡找到食物。')
    if result.get('notes'):
        right.info(result['notes'])
    usage = result.get('usage') or {}
    right.caption(f"模型 {result.get('model')} · {result.get('latency_s')} 秒 · "
                  f"輸入 {usage.get('input_tokens')} / 輸出 {usage.get('output_tokens')} tokens。"
                  '框的位置是大概的；認錯的菜可以在下面排除，漏掉的可以手動加入。')
    show_portions(items)


def show_result(result):
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


def main():
    st.set_page_config(page_title='FoodCal AI 餐點熱量估算', page_icon='🍽️', layout='wide')
    st.title('FoodCal AI · 餐點熱量估算')
    st.caption('本機展示版 · 份量是粗估（體積 × 密度），也可以自己填 · 估計值僅供參考，不是醫療或營養建議')
    mode = st.radio('模式', [MODE_DEMO, MODE_UPLOAD], horizontal=True)
    with st.expander('這個模型認得哪些食物？'):
        st.markdown('**Claude Opus 5.5**（雲端）：大部分常見食物都認得，包括白飯、燙青菜這類單一食材。'
                    '\n\n**YOLO**（本機，可離線）：只認得下面這些。')
        st.markdown(supported_foods_markdown())
    if st.session_state.get('mode') != mode:
        clear_session()
        st.session_state['mode'] = mode
    if mode == MODE_DEMO:
        st.info('合成展示：畫布、辨識框與食譜都是測試用案例，不是真實照片分析，也不是食譜推薦。')
        if st.button('顯示展示結果', type='primary'):
            st.session_state['result'], st.session_state['image'] = fixture_demo()
    else:
        engine = st.radio('辨識方式', [ENGINE_CLAUDE, ENGINE_YOLO], horizontal=True, key='engine')
        if st.session_state.get('engine_used') != engine:
            for key in PER_PHOTO_KEYS:
                if key != 'image':
                    st.session_state.pop(key, None)
            st.session_state['engine_used'] = engine
        if engine == ENGINE_CLAUDE:
            st.info('按「開始辨識」後，照片會傳給 Anthropic 的 Claude（claude-opus-5-5）辨識。'
                    '只送縮小過、沒有拍攝資訊（含 GPS）的照片；本機不存檔。')
            ready = st.checkbox('我同意把這張照片傳給 Anthropic 辨識', key='claude_consent')
        else:
            ready = weights_available()
            if ready:
                st.info('選好照片後按「開始辨識」才會執行模型；照片與結果只留在這次的記憶體，不會存檔。')
            else:
                st.warning('這台電腦沒有模型權重或類別檔，無法用 YOLO 辨識。請向組員取得 '
                           '`runs/detect/taiwanese_food_baseline_10ep/weights/best.pt` 與 '
                           '`data/roboflow_taiwanese_food_detection/foodcal_data.yaml`，或改用 Claude。')
        uploaded = st.file_uploader('選擇餐點照片（PNG / JPEG / WEBP，最多 10 MB）',
                                    type=['png', 'jpg', 'jpeg', 'webp'],
                                    key=f"upload_{st.session_state.get('upload_generation', 0)}")
        synthetic = False
        if engine == ENGINE_YOLO:
            with st.expander('進階選項'):
                synthetic = st.checkbox('顯示測試用食譜候選（合成資料，不是推薦，也不會算進熱量）', value=False)
        raw = uploaded.getvalue() if uploaded else None
        if raw:
            from hashlib import sha256
            input_key = (sha256(raw).hexdigest(), synthetic)
            if st.session_state.get('input_key') != input_key:
                for key in PER_PHOTO_KEYS:
                    st.session_state.pop(key, None)
                st.session_state['input_key'] = input_key
            try:
                st.image(decode_upload(raw), caption='照片預覽（還沒開始辨識）', width=360)
            except (ValueError, OSError):
                st.error('圖片無法讀取，請換一張 PNG／JPEG／WEBP 照片。')
        else:
            st.session_state.pop('result', None)
            st.session_state.pop('image', None)
        start = st.button('開始辨識', type='primary', disabled=not raw or not ready)
        if start and engine == ENGINE_CLAUDE:
            image = decode_upload(raw)
            try:
                with st.spinner('Claude 辨識中…（通常 10–30 秒）'):
                    found = recognize_foods(image, client=claude_client())
            except RecognitionError as error:
                st.error(CLAUDE_ERRORS.get(str(error), str(error)))
            else:
                for key in PER_PHOTO_KEYS:
                    st.session_state.pop(key, None)
                st.session_state['result'], st.session_state['image'] = {'engine': 'claude', **found}, image
        elif start:
            def detector():
                if 'detector' not in st.session_state:
                    st.session_state['detector'] = FoodDetector()
                return st.session_state['detector']
            with st.spinner('辨識中…'):
                st.session_state['result'], st.session_state['image'] = analyze_upload(
                    raw, enabled=True, fixture_recipes=synthetic, detector_factory=detector)
    if st.button('清除本次圖片與結果'):
        clear_session()
        st.session_state['upload_generation'] = st.session_state.get('upload_generation', 0) + 1
        st.rerun()
    result = st.session_state.get('result')
    if not result:
        st.caption('選擇模式並按下按鈕後，結果會出現在這裡。')
        return
    if mode == MODE_UPLOAD and result.get('engine') == 'claude':
        show_claude_result(result)
    elif mode == MODE_UPLOAD:
        show_upload_result(result)
    else:
        show_result(result)


if __name__ == '__main__':
    main()
