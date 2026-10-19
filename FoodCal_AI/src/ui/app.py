"""Streamlit local demo. Default fixture; user images/results are session-memory only."""
import sys
from pathlib import Path

# Streamlit executes this file as an entry script, from the documented project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from src.ui.adapter import fixture_demo, decode_upload, analyze_upload, draw_predictions
from src.detection import FoodDetector


def main():
    st.set_page_config(page_title='FoodCal AI Demo', page_icon='🍽️', layout='wide')
    st.title('FoodCal AI · Diagnostic Demo')
    st.caption('本機展示 · 候選與證據診斷 · 沒有正式熱量估算')
    mode = st.radio('模式', ['Fixture Demo', '真實 YOLO'], horizontal=True)
    if st.session_state.get('mode') != mode:
        for key in ('result', 'image', 'input_key', 'detector'):
            st.session_state.pop(key, None)
        st.session_state['mode'] = mode
    if st.button('清除本次圖片與結果'):
        for key in ('result', 'image', 'input_key', 'detector'):
            st.session_state.pop(key, None)
        st.session_state['upload_generation'] = st.session_state.get('upload_generation', 0) + 1
        st.rerun()
    st.info('熱量：unavailable　｜　照片食用重量：null　｜　營養資格：false')
    if mode == 'Fixture Demo':
        st.warning('SYNTHETIC / TEST_ONLY：畫布、辨識與食譜關聯都是合成協定案例，不是真實圖片分析或食譜推薦。')
        if st.button('顯示 Fixture 診斷'):
            st.session_state['result'], st.session_state['image'] = fixture_demo()
    else:
        st.warning('選圖不會啟動模型。按下「開始 YOLO 推論」才執行一次；照片與結果預設不落盤。')
        uploaded = st.file_uploader('選擇本機圖片（PNG / JPEG / WEBP，最多 10 MB）',
                                   type=['png', 'jpg', 'jpeg', 'webp'], key=f"upload_{st.session_state.get('upload_generation', 0)}")
        synthetic = st.checkbox('明示啟用 synthetic/test_only 食譜先驗（不是推薦）', value=False)
        raw = uploaded.getvalue() if uploaded else None
        if raw:
            from hashlib import sha256
            input_key = (sha256(raw).hexdigest(), synthetic)
            if st.session_state.get('input_key') != input_key:
                st.session_state.pop('result', None);st.session_state.pop('image', None)
                st.session_state['input_key'] = input_key
            try:
                st.image(decode_upload(raw), caption='選定圖片預覽；選圖不會自動推論', width='stretch')
            except (ValueError, OSError):
                st.error('圖片無法安全解碼，請重新選擇。')
        else:
            st.session_state.pop('result', None);st.session_state.pop('image', None)
        if st.button('開始 YOLO 推論', disabled=not raw):
            def detector():
                if 'detector' not in st.session_state:
                    st.session_state['detector'] = FoodDetector()
                return st.session_state['detector']
            with st.spinner('執行本機 Detection → Retrieval…'):
                st.session_state['result'], st.session_state['image'] = analyze_upload(
                    raw, enabled=True, fixture_recipes=synthetic, detector_factory=detector)
    result = st.session_state.get('result')
    if not result:
        st.caption('選擇模式並按下對應按鈕，結果才會出現在這裡。')
        return
    st.subheader('診斷結果')
    st.write('流程狀態：', result['status'])
    for issue in result.get('issues', []):
        st.error(issue['code'] + '：' + issue['message'])
    image = st.session_state.get('image')
    if image is not None and result.get('detection_snapshot'):
        st.image(draw_predictions(image, result), caption='Pred 框／類別／confidence；Fixture 模式是合成框。未顯示 GT。', width='stretch')
        st.dataframe(result['detection_snapshot']['detections'], hide_index=True)
    st.subheader('食譜候選與來源')
    for obj in result.get('objects', []):
        st.write(f"物件 #{obj['detection_id']} · {obj['retrieval_status']} · {obj.get('reason') or ''}")
        for candidate in obj['recipe_candidates']:
            recipe = candidate['recipe']
            with st.expander(recipe['name'] + ' · SYNTHETIC / TEST_ONLY'):
                st.warning('這是合成食譜先驗，不代表照片實際食材。')
                st.json({'source': recipe['source'], 'retrieval_score': recipe['retrieval_score'],
                         'status': candidate['status'], 'reason': candidate.get('reason'),
                         'parsing': candidate['parsing_snapshot'], 'mapping': candidate['mapping_snapshot'],
                         'source_diagnostic': candidate.get('source_diagnostic')})
    st.subheader('Parsing / Mapping / Nutrition')
    st.json(result['stages'])
    st.json({'review': result['review'], 'trust': result['trust'],
             'calorie_estimate': result['calorie_estimate'], 'photo_portion_grams': result['photo_portion_grams']})
    with st.expander('完整診斷 JSON（記憶體，無自動下載或保存）'):
        st.json(result)


if __name__ == '__main__':
    main()
