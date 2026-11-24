"""Look of the web page, following the proposal deck: off-white page, white cards, dark green actions, pale green
nutrition panel, numbered steps and the KEY RULE banner. Pure HTML/CSS strings; no Streamlit import here.

Texts come from messages.py in the page language (i18n.t). Wording that CSS draws into Streamlit's upload boxes
reads custom properties (--fc-t-...) that lang_css() sets for the page language.
"""
from html import escape

from src.portion.nutrition_model import NUTRIENTS
from .i18n import t

CSS = """
<style>
:root { --fc-bg:#f7f6f2; --fc-card:#ffffff; --fc-line:#e5e1d8; --fc-ink:#1f2a24; --fc-muted:#6f746c;
        --fc-green:#3d6b4f; --fc-green-soft:#e9efe4; --fc-step:#f2efe9; }
.stApp { background: var(--fc-bg); }
.block-container { padding-top: 3.6rem; max-width: 1280px; }
.st-key-fc_plate, .st-key-fc_recognition, .st-key-fc_start, .st-key-fc_upload, .st-key-fc_samples {
  background: var(--fc-card); border-radius: 16px !important; border: 1px solid var(--fc-line) !important;
  padding: 1rem 1.1rem !important; box-shadow: 0 1px 2px rgba(31,42,36,.04); }
.st-key-fc_plate [data-testid="stImage"] img { max-height: 62vh; object-fit: contain; }
/* Before a photo is chosen: one centred column, the upload box first and the samples apart below it. */
.st-key-fc_upload, .st-key-fc_samples, .fc-or { max-width: 780px; width: 100%; margin-left: auto !important;
  margin-right: auto !important; }
.st-key-fc_upload, .st-key-fc_samples { padding: 1.3rem 1.5rem 1.2rem !important; }
.fc-title2 { font-size:1.45rem; font-weight:700; color:var(--fc-ink); line-height:1.3; margin:.2rem 0 .15rem; }
.fc-title3 { font-size:1.08rem; font-weight:650; color:var(--fc-ink); line-height:1.3; margin:.15rem 0 .1rem; }
.fc-lead { color:var(--fc-muted); font-size:.9rem; line-height:1.5; margin-bottom:.35rem; }
/* Streamlit's upload box, enlarged into one big drop area with Chinese text (the whole area opens the picker). */
.st-key-fc_upload [data-testid="stFileUploaderDropzone"] { min-height:210px; border:2px dashed #b9cdb4;
  border-radius:14px; background:#f6f8f3; flex-direction:column !important; align-items:center; justify-content:center;
  gap:.55rem; padding:1.4rem 1rem; text-align:center; transition:background .15s ease, border-color .15s ease; }
.st-key-fc_upload [data-testid="stFileUploaderDropzone"]:hover { background:#eef3ea; border-color:var(--fc-green); }
.st-key-fc_upload [data-testid="stFileUploaderDropzone"]::before { content:''; order:0; width:54px; height:54px;
  border-radius:50%; background:#e2ebdc url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%233d6b4f' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M4 8h3l1.5-2h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z'/%3E%3Ccircle cx='12' cy='13' r='3.5'/%3E%3C/svg%3E") center / 28px no-repeat; }
.st-key-fc_upload [data-testid="stFileUploaderDropzoneInstructions"] { order:1; width:100%; margin:0 !important;
  justify-content:center; text-align:center; }
.st-key-fc_upload [data-testid="stFileUploaderDropzoneInstructions"] > div { align-items:center; }
.st-key-fc_upload [data-testid="stFileUploaderDropzoneInstructions"] span { display:none; }
.st-key-fc_upload [data-testid="stFileUploaderDropzoneInstructions"] > div::before { content:var(--fc-t-drop-title);
  color:var(--fc-ink); font-weight:650; font-size:1.05rem; }
.st-key-fc_upload [data-testid="stFileUploaderDropzone"] > span { order:2; }
.st-key-fc_upload [data-testid="stFileUploaderDropzone"]::after { content:var(--fc-t-drop-note);
  order:3; color:var(--fc-muted); font-size:.78rem; }
.st-key-fc_upload [data-testid="stFileUploaderDropzone"] button { background:var(--fc-green); border-color:var(--fc-green);
  color:#fff; border-radius:10px; padding:.45rem 1.3rem; }
.st-key-fc_upload [data-testid="stFileUploaderDropzone"] button:hover { background:#335b43; border-color:#335b43; color:#fff; }
.st-key-fc_upload [data-testid="stFileUploaderDropzone"] button p { display:none; }
.st-key-fc_upload [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"]::after {
  content:var(--fc-t-choose); font-weight:600; }
.fc-tips { display:grid; grid-template-columns:repeat(3, minmax(0,1fr)); gap:.9rem; margin:.9rem 0 .45rem; }
.fc-tip { display:flex; gap:.6rem; align-items:flex-start; color:var(--fc-muted); font-size:.8rem; line-height:1.45; }
.fc-tip svg { flex:0 0 34px; }
.fc-tip b { display:block; color:var(--fc-ink); font-size:.86rem; font-weight:650; }
.fc-or { display:flex; align-items:center; gap:.8rem; color:var(--fc-muted); font-size:.82rem; margin-top:.5rem;
  margin-bottom:.5rem; }
.fc-or::before, .fc-or::after { content:''; flex:1; height:1px; background:var(--fc-line); }
.st-key-fc_samples [data-testid="stImage"] img { height:150px; object-fit:cover; border-radius:10px; }
.fc-sample-name { font-weight:650; color:var(--fc-ink); font-size:.9rem; margin:.05rem 0 -.25rem; }
.fc-divider { height:1px; background:var(--fc-line); margin:.75rem 0 .6rem; }
/* Box pickers keep the photo's size; on a narrow screen they scroll inside the card instead of spilling out. */
.st-key-fc_plate [data-testid="stVegaLiteChart"] { overflow-x:auto; }
.st-key-fc_plate [data-testid="stFullScreenFrame"]:has([data-testid="stVegaLiteChart"]) [data-testid="stElementToolbar"] {
  display:none; }  /* "show data / download PNG / copy spec" mean nothing on a box picker */
/* The optional side photo: a slim upload box - icon, two lines of text and the button in one row. */
.st-key-fc_side_drop [data-testid="stFileUploaderDropzone"] { min-height:74px; border:1.5px dashed #c9d6c3;
  border-radius:12px; background:#fafbf8; flex-direction:row !important; align-items:center; gap:.75rem;
  padding:.6rem .8rem; transition:background .15s ease, border-color .15s ease; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzone"]:hover { background:#f1f5ed; border-color:var(--fc-green); }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzone"]::before { content:''; order:0; flex:0 0 42px; height:42px;
  border-radius:10px; background:#e9efe4 url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32' fill='none' stroke='%233d6b4f' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M3 26.5h26'/%3E%3Cpath d='M15.5 20.5h13c0 3-2.9 5.5-6.5 5.5s-6.5-2.5-6.5-5.5z'/%3E%3Crect x='2.5' y='8.5' width='9' height='6.5' rx='1.3'/%3E%3Ccircle cx='7' cy='11.75' r='1.6'/%3E%3Cpath d='M12.5 13.5l4.5 4' stroke-dasharray='1.6 1.8'/%3E%3C/svg%3E") center / 30px no-repeat; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzoneInstructions"] { order:1; flex:1 1 auto; margin:0 !important;
  text-align:left; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzoneInstructions"] > div { align-items:flex-start; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzoneInstructions"] span { display:none; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzoneInstructions"] > div::before { content:var(--fc-t-side-title);
  color:var(--fc-ink); font-weight:650; font-size:.93rem; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzoneInstructions"] > div::after {
  content:var(--fc-t-side-note); color:var(--fc-muted); font-size:.76rem; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzone"] > span { order:2; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzone"] button { border-radius:10px; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzone"] button p { display:none; }
.st-key-fc_side_drop [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"]::after {
  content:var(--fc-t-choose); font-weight:600; }
/* A chosen side photo: small thumbnail beside its name (after the plate image rule, so the thumbnail wins). */
.st-key-fc_side_staged { background:#fafbf8; border:1px solid var(--fc-line); border-radius:12px;
  padding:.55rem .65rem !important; }
.st-key-fc_side_staged [data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; gap:.75rem !important; }
.st-key-fc_side_staged [data-testid="stColumn"]:first-child { flex:0 0 112px !important; width:112px !important;
  min-width:112px !important; }
.st-key-fc_side_staged [data-testid="stColumn"]:last-child { flex:1 1 auto !important; width:auto !important;
  min-width:0 !important; }
.st-key-fc_side_staged [data-testid="stImage"] img { height:84px; object-fit:cover; border-radius:8px; }
/* Language menu: pinned to the top right of the window, in Streamlit's header bar, so it stays in view on every
   step and while scrolling. A globe drawn in front of the chosen language marks what it is. */
.st-key-fc_lang { position:fixed; top:.6rem; right:3.4rem; width:11.5rem !important; z-index:999991; }
.st-key-fc_lang [data-testid="stSelectbox"] [role="group"] { padding-left:1.8rem; background-repeat:no-repeat;
  background-position:.65rem center;
  background-size:16px; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%233d6b4f' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='12' cy='12' r='9'/%3E%3Cpath d='M3 12h18'/%3E%3Cpath d='M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18'/%3E%3C/svg%3E"); }
.fc-brand { display:flex; justify-content:space-between; align-items:center; color:var(--fc-muted);
            font-size:.78rem; letter-spacing:.14em; text-transform:uppercase; font-weight:600; }
.fc-badge { background:#1f2a24; color:#fff; border-radius:6px; padding:2px 8px; font-size:.7rem; letter-spacing:.08em; }
.fc-title { font-size:2.5rem; font-weight:700; color:var(--fc-ink); margin:.2rem 0 0; line-height:1.15; }
.fc-tagline { color:var(--fc-muted); font-size:1.05rem; margin:.25rem 0 1rem; }
.fc-steps { display:grid; grid-template-columns:repeat(5, minmax(0,1fr)); gap:.6rem; margin:.4rem 0 .8rem; }
.fc-step { background:var(--fc-step); border:1px solid var(--fc-line); border-radius:12px; padding:.7rem .8rem; }
.fc-step.done { background:var(--fc-green-soft); border-color:#b9cdb4; }
.fc-step .n { font-family: Georgia, 'Times New Roman', serif; color:var(--fc-muted); font-size:1.25rem; }
.fc-step .t { font-weight:700; letter-spacing:.06em; font-size:.85rem; color:var(--fc-ink); margin-top:.15rem; }
.fc-step .d { color:var(--fc-ink); font-size:.86rem; margin-top:.2rem; }
.fc-step .s { color:var(--fc-muted); font-size:.74rem; margin-top:.25rem; }
.fc-rule { display:flex; gap:1.2rem; align-items:center; background:#262626; color:#fff; border-radius:10px;
           padding:.7rem 1.1rem; margin:.2rem 0 1.2rem; }
.fc-rule b { letter-spacing:.08em; font-size:.8rem; white-space:nowrap; }
.fc-rule span { font-size:.95rem; }
.fc-label { color:var(--fc-muted); font-size:.72rem; letter-spacing:.16em; text-transform:uppercase; font-weight:700; }
.fc-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:.3rem; }
.fc-chip { background:#ece9e2; color:#4b524a; border-radius:6px; padding:2px 8px; font-size:.7rem;
           letter-spacing:.1em; font-weight:700; text-transform:uppercase; }
.fc-dish { font-size:1.65rem; font-weight:650; color:var(--fc-ink); line-height:1.25; margin:.2rem 0 .1rem; }
.fc-sub { color:var(--fc-muted); font-size:.9rem; margin-bottom:.6rem; }
.fc-row { display:flex; gap:.7rem; align-items:center; }
.fc-num { flex:0 0 34px; height:34px; border-radius:50%; border:1px solid #cfd8c9; background:var(--fc-green-soft);
          display:flex; align-items:center; justify-content:center; font-size:.8rem; color:#3b4a3f; }
.fc-name { font-weight:650; color:var(--fc-ink); font-size:1.02rem; }
.fc-meta { color:var(--fc-muted); font-size:.78rem; line-height:1.35; }
.fc-hidden { color:#9a6b2f; font-size:.78rem; }
.fc-panel { background:var(--fc-green-soft); border:1px solid #d5e0cf; border-radius:16px; padding:1.1rem 1.2rem; }
.fc-kcal { font-size:3.3rem; font-weight:600; color:var(--fc-ink); line-height:1; }
.fc-kcal small { font-size:.85rem; color:var(--fc-muted); font-weight:500; margin-left:.3rem; }
.fc-bar { display:flex; height:8px; border-radius:6px; overflow:hidden; margin:.8rem 0 .6rem; background:#d8e0d2; }
.fc-macro { display:flex; justify-content:space-between; padding:.45rem 0; border-bottom:1px solid #d5e0cf; font-size:.92rem; }
.fc-dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:.5rem; }
.fc-note { background:#f6f8f3; border:1px solid #d5e0cf; border-radius:10px; padding:.6rem .75rem; font-size:.8rem;
           color:#3b4a3f; margin:.8rem 0; }
.fc-nut-head { margin:1rem 0 .15rem; color:var(--fc-muted); font-size:.72rem; letter-spacing:.12em; font-weight:700; }
.fc-nut { display:flex; justify-content:space-between; align-items:flex-start; gap:.6rem; padding:.35rem 0;
          border-bottom:1px solid #e1e8dc; font-size:.88rem; }
.fc-nut b { white-space:nowrap; }
.fc-table { width:100%; border-collapse:collapse; font-size:.8rem; }
.fc-table th { color:var(--fc-muted); font-weight:600; text-align:right; padding:.3rem .2rem; border-bottom:1px solid #d5e0cf; }
.fc-table th:first-child, .fc-table td:first-child { text-align:left; }
.fc-table td { text-align:right; padding:.35rem .2rem; border-bottom:1px solid #e1e8dc; }
.fc-table tr.total td { font-weight:700; border-bottom:none; }
.fc-empty { color:var(--fc-muted); font-size:.9rem; padding:1.4rem 0; }
.st-key-fc_mobile_summary { display:none !important; }
.fc-mobile-summary { background:var(--fc-green-soft); border:1px solid #d5e0cf; border-radius:14px;
                     padding:.75rem .9rem; margin:0 0 .8rem; }
.fc-ms-kcal { font-size:2.2rem; font-weight:650; color:var(--fc-ink); line-height:1.05; margin-top:.15rem; }
.fc-ms-kcal small { font-size:.8rem; color:var(--fc-muted); font-weight:500; margin-left:.3rem; }
.fc-ms-row { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:.3rem; margin:.55rem 0 .35rem; }
.fc-ms-row div { background:#f6f8f3; border-radius:8px; padding:.3rem .35rem; font-size:.7rem; color:var(--fc-muted); }
.fc-ms-row b { display:block; color:var(--fc-ink); font-size:.92rem; white-space:nowrap; }
@media (max-width: 760px) { .fc-steps { grid-template-columns:repeat(2, minmax(0,1fr)); } .fc-title { font-size:1.9rem; } }
/* Phones: the answer first, compact chrome, thumbnails side by side, no iOS zoom on number boxes. */
@media (max-width: 640px) {
  .block-container { padding: 2.8rem .85rem 2rem !important; }
  .st-key-fc_lang { top:.5rem; right:3rem; width:9.8rem !important; }
  .fc-brand { font-size:.6rem; letter-spacing:.08em; }
  .fc-badge { display:none; }
  .fc-title { font-size:1.55rem; }
  .fc-tagline { font-size:.88rem; margin:.2rem 0 .5rem; }
  .fc-steps { grid-template-columns:repeat(5, minmax(0,1fr)); gap:.25rem; margin:.1rem 0 .5rem; }
  .fc-step { padding:.3rem .1rem; border-radius:8px; text-align:center; }
  .fc-step .n { font-size:.8rem; }
  .fc-step .t { font-size:.54rem; letter-spacing:0; margin-top:0; }
  .fc-step .d, .fc-step .s { display:none; }
  .fc-rule { flex-direction:column; align-items:flex-start; gap:.2rem; padding:.5rem .75rem; margin:.1rem 0 .7rem; }
  .fc-rule b { font-size:.66rem; }
  .fc-rule span { font-size:.78rem; line-height:1.35; }
  .st-key-fc_mobile_summary { display:block !important; }
  [data-testid="stHorizontalBlock"] { gap:.8rem !important; }
  .st-key-fc_plate, .st-key-fc_recognition, .st-key-fc_start, .st-key-fc_samples { padding:.8rem .85rem !important; }
  .st-key-fc_upload { padding:1rem .95rem .9rem !important; }
  .st-key-fc_plate [data-testid="stImage"] img { max-height:48vh; }
  .fc-title2 { font-size:1.2rem; }
  .st-key-fc_upload [data-testid="stFileUploaderDropzone"] { min-height:168px; padding:1.1rem .8rem; }
  .st-key-fc_upload [data-testid="stFileUploaderDropzoneInstructions"] > div::before { content:var(--fc-t-drop-title-phone); }
  .st-key-fc_upload [data-testid="stFileUploaderDropzone"]::after { content:var(--fc-t-drop-note-phone); }
  .st-key-fc_upload [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"]::after {
    content:var(--fc-t-choose-phone); }
  .fc-tips { grid-template-columns:1fr; gap:.55rem; margin:.75rem 0 .5rem; }
  .st-key-fc_plate_actions [data-testid="stHorizontalBlock"] { flex-wrap:wrap !important; gap:.5rem !important; }
  .st-key-fc_plate_actions [data-testid="stColumn"] { flex:1 1 calc(50% - .5rem) !important;
    width:calc(50% - .5rem) !important; min-width:calc(50% - .5rem) !important; }
  .st-key-fc_side_drop [data-testid="stFileUploaderDropzone"]::before { display:none; }
  .st-key-fc_samples [data-testid="stHorizontalBlock"] { flex-wrap:nowrap !important; gap:.5rem !important; }
  .st-key-fc_samples [data-testid="stColumn"] { min-width:0 !important; width:auto !important; flex:1 1 0 !important; }
  .st-key-fc_samples [data-testid="stImage"] img { height:104px; }
  .fc-dish { font-size:1.3rem; }
  .fc-row { margin-bottom:.5rem; }
  .fc-kcal { font-size:2.6rem; }
  .fc-panel { padding:.9rem .95rem; }
  input, textarea { font-size:16px !important; }
}
</style>
"""

STEPS = (('01', 'INPUT'), ('02', 'DISH'), ('03', 'VISION'), ('04', 'ESTIMATION'), ('05', 'OUTPUT'))  # step.N.d/.s

MACRO_COLORS = {'protein': '#3d6b4f', 'carb': '#d9a35b', 'fat': '#8fa7c4'}
PILL_COLORS = (('#dcebd3', '#5f8f52'), ('#fbe7c2', '#c58b2b'), ('#d6e6f5', '#4f7fb0'),
               ('#f6d9d6', '#b5615a'), ('#e6def3', '#7a64a8'), ('#d9eeeb', '#3f8f86'))
CSS_TEXTS = {'--fc-t-drop-title': 'css.drop_title', '--fc-t-drop-note': 'css.drop_note', '--fc-t-choose': 'css.choose',
             '--fc-t-side-title': 'css.side_title', '--fc-t-side-note': 'css.side_note',
             '--fc-t-drop-title-phone': 'css.drop_title_phone', '--fc-t-drop-note-phone': 'css.drop_note_phone',
             '--fc-t-choose-phone': 'css.choose_phone'}


def _css_string(text):
    return "'" + text.replace('\\', '\\\\').replace("'", "\\'").replace('\n', ' ') + "'"


def lang_css():
    """The wording CSS draws into the upload boxes, in the page language, as the custom properties CSS reads."""
    rules = ' '.join(f'{name}:{_css_string(t(key))};' for name, key in CSS_TEXTS.items())
    return f'<style>:root {{ {rules} }}</style>'


def header():
    return ('<div class="fc-brand"><span>National Tsing Hua University · I2P(I) · Team 1</span>'
            '<span class="fc-badge">FoodCal AI</span></div>'
            f'<div class="fc-title">{escape(t("header.title"))}</div>'
            f'<div class="fc-tagline">{escape(t("header.tagline"))}</div>')


def steps(done):
    """The five workflow cards; the first `done` are marked finished."""
    cards = ''.join(f'<div class="fc-step{" done" if i < done else ""}"><div class="n">{number}</div>'
                    f'<div class="t">{title}</div><div class="d">{escape(t(f"step.{i + 1}.d"))}</div>'
                    f'<div class="s">{escape(t(f"step.{i + 1}.s"))}</div></div>'
                    for i, (number, title) in enumerate(STEPS))
    return f'<div class="fc-steps">{cards}</div>'


def key_rule():
    return f'<div class="fc-rule"><b>KEY RULE</b><span>{escape(t("rule.text"))}</span></div>'


ICON_ATTRS = ('width="34" height="34" viewBox="0 0 34 34" fill="none" stroke="#3d6b4f" stroke-width="1.6" '
              'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"')
PHOTO_TIPS = (  # (icon, message key of its title and detail): how to take a photo the page can measure
    (f'<svg {ICON_ATTRS}><circle cx="13" cy="17" r="10"/><circle cx="13" cy="17" r="6" stroke-opacity=".4"/>'
     f'<rect x="25.5" y="11" width="6.5" height="12" rx="1.4"/></svg>',
     'tip.card'),
    (f'<svg {ICON_ATTRS}><rect x="10" y="2.5" width="14" height="9" rx="2"/><circle cx="17" cy="7" r="2"/>'
     f'<path d="M17 14.5v6" stroke-dasharray="2 2"/><path d="M14.5 18.5l2.5 2.5 2.5-2.5"/>'
     f'<ellipse cx="17" cy="27.5" rx="11" ry="3.8"/></svg>',
     'tip.top'),
    (f'<svg {ICON_ATTRS}><rect x="9" y="15" width="16" height="13" rx="2.5"/>'
     f'<path d="M12.5 15v-3.5a4.5 4.5 0 0 1 9 0V15"/><circle cx="17" cy="21.5" r="1.5"/></svg>',
     'tip.private'),
)


def upload_intro():
    """Heading of the upload card shown before a photo is chosen."""
    return (card_head(t('head.plate')) +
            f'<div class="fc-title2">{escape(t("upload.title"))}</div>'
            f'<div class="fc-lead">{escape(t("upload.lead"))}</div>')


def photo_tips():
    """Three short tips under the upload box: card beside the plate, shot from above, photo not kept."""
    tips = ''.join(f'<div class="fc-tip">{icon}<div><b>{escape(t(key + ".title"))}</b>{escape(t(key + ".detail"))}'
                   f'</div></div>' for icon, key in PHOTO_TIPS)
    return f'<div class="fc-tips">{tips}</div>'


def or_divider():
    return f'<div class="fc-or">{escape(t("or"))}</div>'


def samples_intro():
    """Heading of the sample photos, a card of their own below the upload box."""
    return (card_head(t('head.samples')) +
            f'<div class="fc-title3">{escape(t("samples.title"))}</div>'
            f'<div class="fc-lead">{escape(t("samples.lead"))}</div>')


def start_intro():
    """Heading of the start card next to the chosen photo."""
    return (card_head(t('head.next')) +
            f'<div class="fc-title2">{escape(t("start.title"))}</div>'
            f'<div class="fc-lead">{escape(t("start.lead"))}</div>')


def pick_intro(title, lead):
    """Heading over the photo while it is a box picker (boxing the card or a missed dish)."""
    return f'<div class="fc-title3">{escape(title)}</div><div class="fc-lead">{escape(lead)}</div>'


def side_offer():
    """One line on why a side photo helps, above its upload box under the recognised photo."""
    return f'<div class="fc-lead">{escape(t("side.offer"))}</div>'


def side_staged(name, note):
    """Name and state of a chosen side photo, beside its thumbnail."""
    return (f'<div class="fc-name">{escape(t("side.name"))}</div>'
            f'<div class="fc-meta">{escape(name)} · {escape(note)}</div>')


def card_head(label, chip=None):
    chip_html = f'<span class="fc-chip">{escape(chip)}</span>' if chip else ''
    return f'<div class="fc-head"><span class="fc-label">{escape(label)}</span>{chip_html}</div>'


def item_header(index, name, meta, hidden=None):
    hidden_html = f'<div class="fc-hidden">{escape(hidden)}</div>' if hidden else ''
    return (f'<div class="fc-row"><div class="fc-num">{index:02d}</div><div><div class="fc-name">{escape(name)}</div>'
            f'<div class="fc-meta">{escape(meta)}</div>{hidden_html}</div></div>')


NUTRIENT_ORDER = ('sat_fat_g', 'sugar_g', 'fiber_g', 'sodium_mg', 'cholesterol_mg', 'potassium_mg', 'calcium_mg',
                  'iron_mg')
RECIPE_PART = ('sat_fat_g', 'sugar_g', 'sodium_mg')  # nutrients whose recipe-estimated part has its own line


def nutrient_name(key):
    """A nutrient in the page language (the Chinese name stays in nutrition_model.NUTRIENTS)."""
    return t('nutrient.' + key, {k: name for k, name, _, _ in NUTRIENTS}.get(key))


def nutrient_list(nutrients):
    """Other nutrients (saturated fat, sugar, fibre, sodium, ...) with the recipe part on its own orange line."""
    if not nutrients:
        return ''
    info = {key: (name, unit) for key, name, unit, _ in NUTRIENTS}
    rows = []
    for key in NUTRIENT_ORDER:
        value = nutrients[key]
        name, unit = info[key]
        amount = value['visible'] + value['hidden']
        recipe = ''
        if key in RECIPE_PART and value['hidden_high'] > 0:
            recipe = t('recipe.' + key, hidden=value['hidden'], low=value['hidden_low'], high=value['hidden_high'])
            recipe = f'<div class="fc-hidden">{escape(recipe)}</div>'
        rows.append(f'<div class="fc-nut"><div>{escape(nutrient_name(key))}{recipe}</div>'
                    f'<b>{"≥ " if value["partial"] else ""}{amount} {unit}</b></div>')
    partial = any(nutrients[k]['partial'] for k in NUTRIENT_ORDER)
    note = (t('nut.partial_note') + t('sep.sentence') if partial else '') + t('nut.source_note')
    return (f'<div class="fc-nut-head">{escape(t("nut.other_head"))}</div>' + ''.join(rows) +
            f'<div class="fc-meta" style="margin-top:.4rem">{escape(note)}</div>')


def side_table(rows):
    """Per dish with a side photo: grams from the top photo alone vs with the side photo, and what was measured."""
    from decimal import Decimal
    body = []
    for row in rows:
        if row.get('volume_method') != 'side':
            continue
        density, single = (row.get('nutrition') or {}).get('density'), row.get('single_volume_ml')
        one = (f"{(single['low'] * density).quantize(Decimal('1'))}–{(single['high'] * density).quantize(Decimal('1'))} g"
               if single is not None and density is not None else '—')
        grams = row.get('grams')
        two = (t('meta.typed') if row.get('grams_source') == 'user' else
               f"{grams['low']}–{grams['high']} g" if grams else '—')
        h = row['height_cm']
        measured = t('table.bowl_depth') if row.get('shape') == 'bowl' else t('table.height')
        body.append(f'<tr><td>{escape(row["name"])}</td><td>{one}</td><td>{escape(two)}</td>'
                    f'<td>{escape(measured)} {h["low"]}–{h["high"]}</td></tr>')
    if not body:
        return f'<div class="fc-meta">{escape(t("side.none_measurable"))}</div>'
    head = ''.join(f'<th>{escape(t(key))}</th>' for key in ('table.dish', 'table.top_only', 'table.with_side',
                                                            'table.measured'))
    return (f'<table class="fc-table"><tr>{head}</tr>' + ''.join(body) +
            f'</table><div class="fc-meta" style="margin-top:.5rem">{escape(t("side.table_note"))}</div>')


def mobile_summary(total, macros, nutrients=None):
    """Phone-only card at the top of the page: kcal, macros and sodium, so the answer is not three screens down.

    Hidden on wide screens by CSS (the full nutrition panel is already in view there).
    """
    if total is None:
        return ''
    cells = [(t('macro.protein'), f"{macros['protein']} g"), (t('macro.carb'), f"{macros['carb']} g"),
             (t('macro.fat'), f"{macros['fat']} g")]
    if nutrients:
        sodium = nutrients['sodium_mg']
        cells.append((nutrient_name('sodium_mg'),
                      f"{'≥ ' if sodium['partial'] else ''}{sodium['visible'] + sodium['hidden']} mg"))
    row = ''.join(f'<div>{escape(name)}<b>{escape(value)}</b></div>' for name, value in cells)
    rough = t('kcal.rough', low=total['low'], high=total['high'])
    return (f'<div class="fc-mobile-summary"><div class="fc-label">{escape(t("head.estimate"))}</div>'
            f'<div class="fc-ms-kcal">{total["mid"]}<small>{escape(rough)}</small></div>'
            f'<div class="fc-ms-row">{row}</div>'
            f'<div class="fc-meta">{escape(t("mobile.more"))}</div></div>')


def nutrition_panel(total, macros, weight_text, rows, empty_reason=None, nutrients=None):
    """Big kcal number, macro bar and list, other nutrients, visible-vs-recipe note and per-dish table."""
    if total is None:
        return (f'<div class="fc-panel"><div class="fc-label">{escape(t("head.nutrition"))}</div>'
                f'<div class="fc-empty">{escape(empty_reason or t("nut.reason_wait"))}</div></div>')
    energy = {'protein': macros['protein'] * 4, 'carb': macros['carb'] * 4, 'fat': macros['fat'] * 9}
    whole = sum(energy.values()) or 1
    bar = ''.join(f'<div style="width:{float(energy[k] / whole * 100):.1f}%;background:{MACRO_COLORS[k]}"></div>'
                  for k in ('protein', 'carb', 'fat'))
    macro_rows = ''.join(f'<div class="fc-macro"><span><span class="fc-dot" style="background:{MACRO_COLORS[k]}"></span>'
                         f'{escape(t("macro." + k))}</span><b>{macros[k]} g</b></div>' for k in ('protein', 'carb', 'fat'))
    table_rows = ''.join(f'<tr><td>{escape(name)}</td><td>{visible}</td><td>{hidden}</td><td>{visible + hidden}</td></tr>'
                         for name, visible, hidden in rows)
    v, h = total['visible']['mid'], total['hidden']['mid']
    table_rows += (f'<tr class="total"><td>{escape(t("nut.total"))}</td><td>{v}</td><td>{h}</td>'
                   f'<td>{v + h}</td></tr>')
    head = ''.join(f'<th>{escape(t(key))}</th>' for key in ('nut.col_dish', 'nut.col_photo', 'nut.col_recipe', 'nut.total'))
    rough = t('kcal.rough', low=total['low'], high=total['high'])
    return (f'<div class="fc-panel"><div class="fc-label">{escape(t("head.nutrition"))}</div>'
            f'<div class="fc-kcal">{total["mid"]}<small>{escape(rough)}</small></div>'
            f'<div class="fc-bar">{bar}</div>{macro_rows}'
            f'<div class="fc-macro"><span>{escape(t("nut.weight_label"))}</span><b>{escape(weight_text)}</b></div>'
            f'{nutrient_list(nutrients)}'
            f'<div class="fc-note">{escape(t("nut.split_note", visible=v, hidden=h))}</div>'
            f'<table class="fc-table"><tr>{head}</tr>{table_rows}</table>'
            f'<div class="fc-meta" style="margin-top:.6rem">{escape(t("nut.footer"))}</div></div>')
