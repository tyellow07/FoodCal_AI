"""Streamlit local demo. Default synthetic demo; user images/results are session-memory only."""
import sys
from pathlib import Path

# Streamlit executes this file as an entry script, from the documented project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from src.ui.adapter import fixture_demo, decode_upload, analyze_upload, draw_predictions
from src.ui.explain import reason_text, summarize, supported_foods_markdown
from src.detection import FoodDetector
from src.detection.model import DEFAULT_CLASSES_PATH, DEFAULT_MODEL_PATH

MODE_DEMO = '合成展示（不需模型）'
MODE_UPLOAD = '上傳照片辨識（需要模型權重）'
SESSION_KEYS = ('result', 'image', 'input_key', 'detector')


def weights_available():
    """Cheap file check only; the model is never loaded here."""
    return Path(DEFAULT_MODEL_PATH).is_file() and Path(DEFAULT_CLASSES_PATH).is_file()


def clear_session():
    for key in SESSION_KEYS:
        st.session_state.pop(key, None)


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


def main():
    st.set_page_config(page_title='FoodCal AI 餐點熱量估算', page_icon='🍽️', layout='wide')
    st.title('FoodCal AI · 餐點熱量估算')
    st.caption('本機展示版 · 只有在證據足夠時才顯示熱量 · 估計值僅供參考，不是醫療或營養建議')
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
                st.session_state.pop('result', None)
                st.session_state.pop('image', None)
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
    show_result(result)


if __name__ == '__main__':
    main()
