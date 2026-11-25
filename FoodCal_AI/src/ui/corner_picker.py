"""Mark the student card by clicking its four corners on the photo (the card may lie at any angle).

A small Streamlit component (st.components.v2, inline HTML/CSS/JS, no build step): each click puts a corner where
the pointer is, in the photo's own pixels; a corner can be dragged to adjust it, and a fifth click starts over. The
four corners go back to Python only once all four are placed, so clicking does not rerun the page each time.
"""
import base64
from io import BytesIO

import streamlit as st

from src.portion.card_locator import order_card_corners

HTML = '<div class="fc-corners"></div>'

CSS = """
.fc-corners { font-family: inherit; }
.fc-corners .wrap { position: relative; width: 100%; line-height: 0; user-select: none; }
.fc-corners img { width: 100%; height: auto; display: block; border-radius: 8px; }
.fc-corners svg { position: absolute; inset: 0; width: 100%; height: 100%; touch-action: none; cursor: crosshair; }
.fc-corners .bar { display: flex; align-items: center; gap: 12px; margin-top: 8px; line-height: 1.4;
  color: var(--st-text-color); font-size: 0.95rem; }
.fc-corners button { font: inherit; padding: 4px 14px; border-radius: 8px; cursor: pointer;
  border: 1px solid var(--st-border-color, rgba(128, 128, 128, 0.4)); background: var(--st-background-color);
  color: var(--st-text-color); }
.fc-corners button:hover { border-color: var(--st-primary-color); color: var(--st-primary-color); }
"""

JS = """
const NS = 'http://www.w3.org/2000/svg';

export default function (component) {
  const { data, setStateValue, parentElement } = component;
  const root = parentElement.querySelector('.fc-corners');
  root.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'wrap';
  wrap.style.maxWidth = data.shown_w + 'px';
  const img = document.createElement('img');
  img.src = data.url;
  img.alt = '';
  img.draggable = false;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${data.width} ${data.height}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  wrap.append(img, svg);
  const bar = document.createElement('div');
  bar.className = 'bar';
  const status = document.createElement('span');
  const reset = document.createElement('button');
  reset.type = 'button';
  reset.textContent = data.labels.reset;
  bar.append(status, reset);
  root.append(wrap, bar);

  let points = (data.points || []).map((p) => [p[0], p[1]]);
  let drag = -1;

  const unit = () => data.width / (svg.getBoundingClientRect().width || data.shown_w);  // photo px per screen px
  const at = (ev) => {
    const box = svg.getBoundingClientRect();
    const x = (ev.clientX - box.left) / box.width * data.width;
    const y = (ev.clientY - box.top) / box.height * data.height;
    return [Math.min(Math.max(x, 0), data.width), Math.min(Math.max(y, 0), data.height)];
  };
  const shape = (tag, attrs) => {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.appendChild(el);
  };
  const around = () => {  // corners in order around their centre, so four of them never cross
    const cx = points.reduce((s, p) => s + p[0], 0) / points.length;
    const cy = points.reduce((s, p) => s + p[1], 0) / points.length;
    return [...points].sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
  };
  const draw = () => {
    const u = unit();
    svg.innerHTML = '';
    if (points.length >= 2) {
      const line = (points.length === 4 ? around() : points).map((p) => p.join(',')).join(' ');
      shape(points.length === 4 ? 'polygon' : 'polyline', { points: line, fill: points.length === 4 ? 'rgba(34,197,94,0.18)' : 'none',
        stroke: '#22c55e', 'stroke-width': 3 * u, 'stroke-linejoin': 'round' });
    }
    points.forEach((p) => {
      shape('circle', { cx: p[0], cy: p[1], r: 9 * u, fill: 'rgba(255,255,255,0.35)', stroke: '#16a34a', 'stroke-width': 3 * u });
      shape('circle', { cx: p[0], cy: p[1], r: 2 * u, fill: '#16a34a' });
    });
    status.textContent = data.labels.status.replace('{n}', points.length);
  };
  const send = () => setStateValue('corners', points.length === 4 ? points.map((p) => [Math.round(p[0]), Math.round(p[1])]) : null);

  svg.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    const p = at(ev);
    const reach = 16 * unit();
    drag = points.findIndex((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) <= reach);
    if (drag < 0) {
      if (points.length >= 4) points = [];
      points.push(p);
      drag = points.length - 1;
    }
    svg.setPointerCapture(ev.pointerId);
    draw();
  });
  svg.addEventListener('pointermove', (ev) => {
    if (drag < 0) return;
    points[drag] = at(ev);
    draw();
  });
  const release = () => {
    if (drag < 0) return;
    drag = -1;
    draw();
    if (points.length === 4) send();
  };
  svg.addEventListener('pointerup', release);
  svg.addEventListener('pointercancel', release);
  reset.addEventListener('click', () => {
    points = [];
    draw();
    send();
  });
  const watch = new ResizeObserver(draw);
  watch.observe(svg);
  draw();
  return () => watch.disconnect();
}
"""



def _mount(**kwargs):
    # Registered on every call: each Streamlit runtime (a server, or a test's AppTest) has its own registry, and
    # registering the same definition again is silent.
    return st.components.v2.component('fc_corner_picker', html=HTML, css=CSS, js=JS)(**kwargs)


def photo_url(image, size=1200):
    """The photo as a JPEG data URL at most size px on its longest side (the corners still come back in full size)."""
    preview = image.copy()
    preview.thumbnail((size, size))
    buffer = BytesIO()
    preview.convert('RGB').save(buffer, format='JPEG', quality=82)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def corners_in_card_order(points, side=False):
    """Clicked corners [[x, y]] * 4 in card order (TL, TR, BR, BL), or None when there are not four of them."""
    if not points or len(points) != 4:
        return None
    return order_card_corners(points, first='level' if side else 'long')


def corner_picker(image, key, labels, side=False):
    """The photo to click the card's four corners on; [[x, y]] * 4 in card order in original pixels, or None.

    labels: {'status': '... {n}/4 ...', 'reset': '...'} in the page language.
    """
    width, height = image.size
    shown_w = round(width * min(540 / width, 600 / height))  # same size as the box picker: a portrait photo fits
    state = st.session_state.get(key) or {}
    result = _mount(key=key, data={'url': photo_url(image), 'width': width, 'height': height, 'shown_w': shown_w,
                                   'points': state.get('corners') or [], 'labels': labels},
                    on_corners_change=lambda: None, width='stretch')
    return corners_in_card_order((result or {}).get('corners'), side)
