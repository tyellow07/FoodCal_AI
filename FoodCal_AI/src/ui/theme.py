"""Look of the web page, following the proposal deck: off-white page, white cards, dark green actions, pale green
nutrition panel, numbered steps and the KEY RULE banner. Pure HTML/CSS strings; no Streamlit import here.
"""
from html import escape

CSS = """
<style>
:root { --fc-bg:#f7f6f2; --fc-card:#ffffff; --fc-line:#e5e1d8; --fc-ink:#1f2a24; --fc-muted:#6f746c;
        --fc-green:#3d6b4f; --fc-green-soft:#e9efe4; --fc-step:#f2efe9; }
.stApp { background: var(--fc-bg); }
.block-container { padding-top: 3.6rem; max-width: 1280px; }
.st-key-fc_plate, .st-key-fc_recognition { background: var(--fc-card); border-radius: 16px !important;
  border: 1px solid var(--fc-line) !important; padding: 1rem 1.1rem !important; box-shadow: 0 1px 2px rgba(31,42,36,.04); }
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
.fc-table { width:100%; border-collapse:collapse; font-size:.8rem; }
.fc-table th { color:var(--fc-muted); font-weight:600; text-align:right; padding:.3rem .2rem; border-bottom:1px solid #d5e0cf; }
.fc-table th:first-child, .fc-table td:first-child { text-align:left; }
.fc-table td { text-align:right; padding:.35rem .2rem; border-bottom:1px solid #e1e8dc; }
.fc-table tr.total td { font-weight:700; border-bottom:none; }
.fc-empty { color:var(--fc-muted); font-size:.9rem; padding:1.4rem 0; }
@media (max-width: 760px) { .fc-steps { grid-template-columns:repeat(2, minmax(0,1fr)); } .fc-title { font-size:1.9rem; } }
</style>
"""

STEPS = (('01', 'INPUT', '餐點照片', '照片由使用者提供'),
         ('02', 'DISH', 'Gemini 辨識菜色', '菜色 ≠ 食材'),
         ('03', 'VISION', '食材範圍與學生證', '只用看得到的'),
         ('04', 'ESTIMATION', '份量與食譜', '體積 × 密度；食譜補看不到的油糖'),
         ('05', 'OUTPUT', '熱量與營養素', '熱量、蛋白質、醣類、脂肪'))

MACRO_COLORS = {'protein': '#3d6b4f', 'carb': '#d9a35b', 'fat': '#8fa7c4'}
PILL_COLORS = (('#dcebd3', '#5f8f52'), ('#fbe7c2', '#c58b2b'), ('#d6e6f5', '#4f7fb0'),
               ('#f6d9d6', '#b5615a'), ('#e6def3', '#7a64a8'), ('#d9eeeb', '#3f8f86'))


def header():
    return ('<div class="fc-brand"><span>National Tsing Hua University · I2P(I) · Team 1</span>'
            '<span class="fc-badge">FoodCal AI</span></div>'
            '<div class="fc-title">FoodCal AI · 餐點熱量估算</div>'
            '<div class="fc-tagline">Understand the meal, not just the label — 拍一張照片，看懂這餐的熱量。</div>')


def steps(done):
    """The five workflow cards; the first `done` are marked finished."""
    cards = ''.join(f'<div class="fc-step{" done" if i < done else ""}"><div class="n">{n}</div><div class="t">{t}</div>'
                    f'<div class="d">{escape(d)}</div><div class="s">{escape(s)}</div></div>'
                    for i, (n, t, d, s) in enumerate(STEPS))
    return f'<div class="fc-steps">{cards}</div>'


def key_rule():
    return ('<div class="fc-rule"><b>KEY RULE</b><span>Recipe candidates may explain hidden calories, but are not visual '
            'proof. 食譜只能解釋看不到的熱量，不能當作照片證據。</span></div>')


def card_head(label, chip=None):
    chip_html = f'<span class="fc-chip">{escape(chip)}</span>' if chip else ''
    return f'<div class="fc-head"><span class="fc-label">{escape(label)}</span>{chip_html}</div>'


def item_header(index, name, meta, hidden=None):
    hidden_html = f'<div class="fc-hidden">{escape(hidden)}</div>' if hidden else ''
    return (f'<div class="fc-row"><div class="fc-num">{index:02d}</div><div><div class="fc-name">{escape(name)}</div>'
            f'<div class="fc-meta">{escape(meta)}</div>{hidden_html}</div></div>')


def nutrition_panel(total, macros, weight_text, rows, empty_reason=None):
    """Big kcal number, macro bar and list, visible-vs-recipe note and per-dish table."""
    if total is None:
        return (f'<div class="fc-panel"><div class="fc-label">Nutrition · 營養估計</div>'
                f'<div class="fc-empty">{escape(empty_reason or "辨識後，熱量和營養素會顯示在這裡。")}</div></div>')
    energy = {'protein': macros['protein'] * 4, 'carb': macros['carb'] * 4, 'fat': macros['fat'] * 9}
    whole = sum(energy.values()) or 1
    bar = ''.join(f'<div style="width:{float(energy[k] / whole * 100):.1f}%;background:{MACRO_COLORS[k]}"></div>'
                  for k in ('protein', 'carb', 'fat'))
    names = {'protein': '蛋白質', 'carb': '醣類', 'fat': '脂肪'}
    macro_rows = ''.join(f'<div class="fc-macro"><span><span class="fc-dot" style="background:{MACRO_COLORS[k]}"></span>'
                         f'{names[k]}</span><b>{macros[k]} g</b></div>' for k in ('protein', 'carb', 'fat'))
    table_rows = ''.join(f'<tr><td>{escape(name)}</td><td>{visible}</td><td>{hidden}</td><td>{visible + hidden}</td></tr>'
                         for name, visible, hidden in rows)
    v, h = total['visible']['mid'], total['hidden']['mid']
    table_rows += f'<tr class="total"><td>合計</td><td>{v}</td><td>{h}</td><td>{v + h}</td></tr>'
    return (f'<div class="fc-panel"><div class="fc-label">Nutrition · 營養估計</div>'
            f'<div class="fc-kcal">{total["mid"]}<small>kcal · 粗估 {total["low"]}–{total["high"]}</small></div>'
            f'<div class="fc-bar">{bar}</div>{macro_rows}'
            f'<div class="fc-macro"><span>這餐總重量（估）</span><b>{escape(weight_text)}</b></div>'
            f'<div class="fc-note">看得到的食材 {v} kcal ＋ 依食譜估計的油、糖 {h} kcal。'
            f'後者是看不到的部分，只是推估，不是照片證據。</div>'
            f'<table class="fc-table"><tr><th>菜色（kcal）</th><th>照片*</th><th>食譜*</th><th>合計</th></tr>'
            f'{table_rows}</table>'
            f'<div class="fc-meta" style="margin-top:.6rem">*照片：看得到的食材 × 衛福部食品營養成分資料庫；'
            f'食譜：依烹調方式估計的油、糖。粗估值，僅供參考，不是醫療或營養建議。</div></div>')
