"""Streamlit local demo. Default synthetic demo; user images/results are session-memory only."""
import sys
from pathlib import Path

# Streamlit executes this file as an entry script, from the documented project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from src.ui.adapter import fixture_demo, decode_upload, analyze_upload, draw_predictions
from src.ui.explain import reason_text, summarize, supported_foods_markdown
from src.ui.portions import note_text, portion_rows, range_text, read_food_reference_csv
from src.portion.rough_volume import FOOD_TEMPLATES, cm_per_px
from src.ui.explain import food_name
from src.detection import FoodDetector
from src.detection.model import DEFAULT_CLASSES_PATH, DEFAULT_MODEL_PATH

MODE_DEMO = '合成展示（不需模型）'
MODE_UPLOAD = '上傳照片辨識（需要模型權重）'
SESSION_KEYS = ('result', 'image', 'input_key', 'detector', 'card_box', 'manual_items')
FOOD_REFERENCE_PATH = Path(__file__).resolve().parents[2] / 'data/food_reference/food_reference.csv'


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


def detections_of(result):
    return list((result.get('detection_snapshot') or {}).get('detections', []))


def show_portions(result):
    """Rough volume x density per detected food, or grams the user types in."""
    image = st.session_state.get('image')
    input_tag = str(st.session_state.get('input_key', ('none',))[0])[:12]
    st.subheader('份量與熱量（粗估）')
    st.caption('用學生證當比例尺、依食物形狀估體積，再乘上密度得到重量。沒有用秤重資料驗證，'
               '只是大概的數字；知道實際重量的話，直接在右邊填入。')
    if image is not None:
        with st.expander('① 框出學生證（用來換算大小）', expanded=True):
            st.caption('在照片上按住滑鼠拖曳，框住整張學生證；學生證要平放、邊緣大致和照片邊緣平行。')
            picked = box_picker(image, key='card_picker_' + input_tag, name='card')
            if picked is not None and picked != st.session_state.get('card_box'):
                try:
                    cm_per_px(picked)
                except ValueError as error:
                    st.warning(note_text(str(error)))
                else:
                    st.session_state['card_box'] = picked
            if st.session_state.get('card_box') is not None:
                st.success('已框出學生證（結果圖上的綠框）。要重框的話，直接再拖一次。')
        manual = st.session_state.setdefault('manual_items', [])
        with st.expander('② 沒辨識到或認錯？手動加一道菜', expanded=not detections_of(result) and not manual):
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
    items = detections_of(result) + st.session_state.get('manual_items', [])
    if not items:
        st.caption('還沒有任何一道菜：用上面的「手動加一道菜」加入。')
        return
    typed = {item['detection_id']: st.session_state.get(f"grams_{input_tag}_{item['detection_id']}") for item in items}
    rows, total, missing = portion_rows(items, st.session_state.get('card_box'), reference, typed)
    for row in rows:
        left, right = st.columns([3, 2], gap='large')
        with left:
            st.markdown(f"**#{row['id']} {row['name']}**")
            lines = [f"- 體積：{range_text(row['volume_ml'], 'mL')}"]
            source = '（你填的）' if row['grams_source'] == 'user' else ''
            lines.append(f"- 重量：{range_text(row['grams'], 'g')}{source}")
            lines.append(f"- 熱量：{range_text(row['kcal'], 'kcal')}")
            lines += ['- ' + note_text(code) for code in row['notes']]
            st.markdown('\n'.join(lines))
        with right:
            st.number_input('自己填重量（克）', min_value=0.0, max_value=5000.0, value=None, step=10.0,
                            key=f"grams_{input_tag}_{row['id']}", placeholder='不填就用粗估')
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
        right.caption('白飯、麵這類單一食材不在模型的 15 種菜裡，可以用下面的「手動加一道菜」。')
    show_portions(result)
    with st.expander('嚴格模式結果（要有秤重校準才會給熱量）'):
        st.markdown(f"**{summary['headline']}**　熱量：{summary['energy_text']}")
        st.markdown('\n'.join('- ' + text for text in summary['reasons']))
    show_developer_details(result)


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
        st.markdown(supported_foods_markdown())
    if st.session_state.get('mode') != mode:
        clear_session()
        st.session_state['mode'] = mode
    if mode == MODE_DEMO:
        st.info('合成展示：畫布、辨識框與食譜都是測試用案例，不是真實照片分析，也不是食譜推薦。')
        if st.button('顯示展示結果', type='primary'):
            st.session_state['result'], st.session_state['image'] = fixture_demo()
    else:
        ready = weights_available()
        if ready:
            st.info('選好照片後按「開始辨識」才會執行模型；照片與結果只留在這次的記憶體，不會存檔。')
        else:
            st.warning('這台電腦沒有模型權重或類別檔，無法辨識真實照片。請向組員取得 '
                       '`runs/detect/taiwanese_food_baseline_10ep/weights/best.pt` 與 '
                       '`data/roboflow_taiwanese_food_detection/foodcal_data.yaml`，或先使用合成展示。')
        uploaded = st.file_uploader('選擇餐點照片（PNG / JPEG / WEBP，最多 10 MB）',
                                    type=['png', 'jpg', 'jpeg', 'webp'],
                                    key=f"upload_{st.session_state.get('upload_generation', 0)}")
        with st.expander('進階選項'):
            synthetic = st.checkbox('顯示測試用食譜候選（合成資料，不是推薦，也不會算進熱量）', value=False)
        raw = uploaded.getvalue() if uploaded else None
        if raw:
            from hashlib import sha256
            input_key = (sha256(raw).hexdigest(), synthetic)
            if st.session_state.get('input_key') != input_key:
                for key in ('result', 'image', 'card_box', 'manual_items'):
                    st.session_state.pop(key, None)
                st.session_state['input_key'] = input_key
            try:
                st.image(decode_upload(raw), caption='照片預覽（還沒開始辨識）', width=360)
            except (ValueError, OSError):
                st.error('圖片無法讀取，請換一張 PNG／JPEG／WEBP 照片。')
        else:
            st.session_state.pop('result', None)
            st.session_state.pop('image', None)
        if st.button('開始辨識', type='primary', disabled=not raw or not ready):
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
    if mode == MODE_UPLOAD:
        show_upload_result(result)
    else:
        show_result(result)


if __name__ == '__main__':
    main()
