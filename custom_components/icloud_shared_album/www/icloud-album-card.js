// iCloud Shared Album card — ships with the icloud_shared_album integration.
// type: custom:icloud-album-card
//   entity: sensor.<album>_photos   (required)
//   mode: slideshow | grid | latest  (default slideshow)
//   interval: seconds per slide      (slideshow, default 10)
//   order: shuffle | added | taken   (slideshow, default shuffle; grid/latest use added unless taken)
//   columns: 3, max_photos: 0 (grid; 0 = all), aspect_ratio: "4/3" (slideshow/latest)
//   fit: smart | cover | contain  (smart: on a wide frame, portraits pair up side by side or sit whole on a blurred copy)
//   caption: true (date on slideshow/latest), title: optional heading, tap: viewer | none | more-info
const VERSION = '0.3.1';
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const svg = d => `<svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const I = { x: svg('<path d="M6 6l12 12M18 6L6 18"/>'), left: svg('<path d="M15 5l-7 7 7 7"/>'), right: svg('<path d="M9 5l7 7-7 7"/>') };
const fmtDate = ms => { const d = new Date(ms); return `${DAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`; };
const ago = ms => {
  const n = new Date(), d = new Date(ms), days = Math.floor((n - d) / 864e5), yrs = n.getFullYear() - d.getFullYear();
  if (d.getMonth() === n.getMonth() && d.getDate() === n.getDate() && yrs > 0) return `On this day · ${yrs} year${yrs > 1 ? 's' : ''} ago`;
  if (days < 1) return 'Today'; if (days < 2) return 'Yesterday'; if (days < 30) return `${days} days ago`;
  if (days < 365) { const m = Math.round(days / 30); return m < 2 ? 'Last month' : `${m} months ago`; }
  const y = Math.floor(days / 365); return `${y} year${y > 1 ? 's' : ''} ago`;
};

const CSS = `
:host{display:block}
ha-card{overflow:hidden;height:100%;display:flex;flex-direction:column}
.head{padding:14px 16px 8px;font-size:var(--ha-card-header-font-size,20px);font-weight:500;line-height:1.2}
.frame{position:relative;width:100%;flex:1;min-height:0;background:var(--secondary-background-color,#eee);cursor:pointer;overflow:hidden}
.frame.fixed{flex:none}
.layer{position:absolute;inset:0;opacity:0;transition:opacity 1s ease}
.layer.on{opacity:1}
.layer img{position:absolute;inset:0;width:100%;height:100%;object-fit:var(--fit,cover);object-position:50% 35%}
.layer .pair{position:absolute;inset:0;display:grid;grid-template-columns:1fr 1fr;gap:3px;background:var(--card-background-color,#fff)}
.layer .pair img{position:static;object-fit:cover;object-position:50% 25%}
.layer img.bg{object-fit:cover;filter:blur(28px) brightness(.8);transform:scale(1.15)}
.layer img.fg{object-fit:contain;object-position:50% 50%}
.cap{position:absolute;left:0;right:0;bottom:0;padding:28px 16px 12px;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.55));display:flex;justify-content:space-between;align-items:flex-end;gap:12px;pointer-events:none;font-size:14px}
.cap b{font-weight:700;letter-spacing:.06em;text-transform:uppercase;font-size:12px;opacity:.9}
.grid{display:grid;grid-template-columns:repeat(var(--cols,3),1fr);gap:4px;padding:4px;overflow-y:auto;flex:1;min-height:0}
.tile{position:relative;aspect-ratio:1;border:0;padding:0;margin:0;background:var(--secondary-background-color,#eee);border-radius:6px;overflow:hidden;cursor:pointer}
.tile img{width:100%;height:100%;object-fit:cover;display:block}
.msg{padding:24px 16px;color:var(--secondary-text-color);text-align:center}
.viewer{position:fixed;inset:0;z-index:9999;background:#0c0c0c;display:none;touch-action:none}
.viewer.open{display:block}
.viewer img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}
.vbtn{position:absolute;width:64px;height:64px;border-radius:50%;border:0;display:grid;place-items:center;background:rgba(255,255,255,.14);color:#fff;cursor:pointer}
.vx{top:16px;right:16px}.vprev{left:16px;top:50%;margin-top:-32px}.vnext{right:16px;top:50%;margin-top:-32px}
.vcap{position:absolute;left:0;right:0;bottom:0;padding:48px 24px 20px;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.6));font-size:22px}
.vcap small{display:block;font-size:13px;letter-spacing:.08em;text-transform:uppercase;opacity:.75;margin-top:4px}
`;

class ICloudAlbumCard extends HTMLElement {
  static getStubConfig(hass) {
    const e = Object.keys(hass.states).find(id => id.startsWith('sensor.') && hass.states[id].attributes.newest_id !== undefined && hass.states[id].attributes.album !== undefined);
    return { entity: e || '', mode: 'slideshow', interval: 10 };
  }
  static getConfigForm() {
    return {
      schema: [
        { name: 'entity', required: true, selector: { entity: { domain: 'sensor', integration: 'icloud_shared_album' } } },
        { name: 'title', selector: { text: {} } },
        { name: 'mode', selector: { select: { mode: 'dropdown', options: [{ value: 'slideshow', label: 'Slideshow' }, { value: 'grid', label: 'Grid' }, { value: 'latest', label: 'Latest photo' }] } } },
        { type: 'grid', name: '', schema: [
          { name: 'interval', selector: { number: { min: 3, max: 3600, unit_of_measurement: 's', mode: 'box' } } },
          { name: 'order', selector: { select: { mode: 'dropdown', options: [{ value: 'shuffle', label: 'Shuffle' }, { value: 'added', label: 'Recently added' }, { value: 'taken', label: 'Date taken' }] } } },
          { name: 'columns', selector: { number: { min: 1, max: 8, mode: 'box' } } },
          { name: 'max_photos', selector: { number: { min: 0, max: 2000, mode: 'box' } } },
          { name: 'aspect_ratio', selector: { text: {} } },
          { name: 'fit', selector: { select: { mode: 'dropdown', options: [{ value: 'smart', label: 'Smart (pair portraits)' }, { value: 'cover', label: 'Fill (crop)' }, { value: 'contain', label: 'Fit (whole photo)' }] } } }
        ] },
        { name: 'caption', selector: { boolean: {} } },
        { name: 'tap', selector: { select: { mode: 'dropdown', options: [{ value: 'viewer', label: 'Open full screen' }, { value: 'more-info', label: 'More info' }, { value: 'none', label: 'Nothing' }] } } }
      ],
      computeLabel: s => ({ entity: 'Album', title: 'Title', mode: 'Mode', interval: 'Seconds per photo', order: 'Order', columns: 'Grid columns', max_photos: 'Max photos in grid (0 = all)', aspect_ratio: 'Aspect ratio (e.g. 4/3)', fit: 'Photo fit', caption: 'Show date', tap: 'Tap action' }[s.name])
    };
  }
  setConfig(config) {
    if (!config || !config.entity) throw new Error('icloud-album-card: set entity to an iCloud Shared Album photos sensor');
    this._config = { mode: 'slideshow', interval: 10, order: 'shuffle', columns: 3, max_photos: 0, aspect_ratio: '4/3', fit: 'smart', caption: true, tap: 'viewer', ...config };
    this._built = false;
    if (this._hass) this._build();
  }
  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    const s = hass.states[this._config.entity];
    const sig = s ? `${s.state}|${s.attributes.newest_id}` : '-';
    if (sig !== this._sig) { this._sig = sig; this._fetch(); }
  }
  getCardSize() { return this._config && this._config.mode === 'grid' ? 6 : 5; }
  getGridOptions() { return { columns: 12, rows: this._config && this._config.mode === 'grid' ? 6 : 5, min_rows: 2 }; }
  connectedCallback() { if (this._photos) this._schedule(); }
  disconnectedCallback() { clearTimeout(this._timer); }

  _build() {
    const c = this._config;
    if (!this.shadowRoot) this.attachShadow({ mode: 'open' });
    const body = c.mode === 'grid'
      ? `<div class="grid" style="--cols:${Number(c.columns) || 3}"><div class="msg">Loading photos…</div></div>`
      : `<div class="frame${c.aspect_ratio ? ' fixed' : ''}" style="--fit:${esc(c.fit === 'contain' ? 'contain' : 'cover')};${c.aspect_ratio ? `aspect-ratio:${esc(c.aspect_ratio)}` : ''}"><div class="layer"></div><div class="layer"></div><div class="cap"></div></div>`;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><ha-card>${c.title ? `<div class="head">${esc(c.title)}</div>` : ''}${body}</ha-card>
      <div class="viewer"><img alt=""><button class="vbtn vx" data-v="close">${I.x}</button><button class="vbtn vprev" data-v="prev">${I.left}</button><button class="vbtn vnext" data-v="next">${I.right}</button><div class="vcap"></div></div>`;
    const root = this.shadowRoot;
    root.addEventListener('click', e => this._click(e));
    const v = root.querySelector('.viewer');
    v.addEventListener('pointerdown', e => { this._x0 = e.clientX; });
    v.addEventListener('pointerup', e => { if (this._x0 == null) return; const dx = e.clientX - this._x0; this._x0 = null; if (Math.abs(dx) > 60) { this._swiped = true; this._step(dx < 0 ? 1 : -1); } });
    this._keys = e => { if (!this._vOpen) return; if (e.key === 'Escape') this._close(); if (e.key === 'ArrowRight') this._step(1); if (e.key === 'ArrowLeft') this._step(-1); };
    window.addEventListener('keydown', this._keys);
    this._built = true; this._idx = 0;
    if (this._photos) this._render();
  }
  async _fetch() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    try {
      const r = await this._hass.callWS({ type: 'call_service', domain: 'icloud_shared_album', service: 'get_photos', service_data: { entity_id: this._config.entity }, return_response: true });
      const album = Object.values((r.response || {}).albums || {})[0];
      this._photos = album ? album.photos || [] : [];
      this._order();
      this._render();
    } catch (err) {
      const box = this.shadowRoot && (this.shadowRoot.querySelector('.grid') || this.shadowRoot.querySelector('.cap'));
      if (box) box.innerHTML = `<div class="msg">Couldn’t load the album: ${esc(err.message || err)}</div>`;
    } finally { this._loading = false; }
  }
  _order() {
    const c = this._config, list = (this._photos || []).slice();
    const by = c.order === 'taken' ? p => p.taken || p.added || 0 : p => p.added || p.taken || 0;
    list.sort((a, b) => by(b) - by(a));
    if (c.mode === 'slideshow' && c.order === 'shuffle') for (let i = list.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [list[i], list[j]] = [list[j], list[i]]; }
    this._list = list;
    if (c.mode === 'grid' && Number(c.max_photos) > 0) this._list = list.slice(0, Number(c.max_photos));
    this._slides = c.mode === 'grid' ? [] : this._buildSlides(list);
    this._idx = 0;
  }
  _wide() {
    const f = this.shadowRoot && this.shadowRoot.querySelector('.frame');
    if (f && f.clientWidth && f.clientHeight) return f.clientWidth / f.clientHeight > 1.2;
    const [a, b] = String(this._config.aspect_ratio || '4/3').split(/[/:x]/).map(Number);
    return !(a && b) || a / b > 1.2;
  }
  _buildSlides(list) {
    const smart = this._config.fit === 'smart' && this._wide();
    const portrait = p => p.w && p.h && p.h > p.w * 1.05, used = new Set(), slides = [];
    list.forEach((p, i) => {
      if (used.has(i)) return; used.add(i);
      if (!smart || !portrait(p)) return slides.push({ kind: 'wide', photos: [p] });
      if (this._config.mode !== 'latest') {
        const j = list.findIndex((q, k) => k > i && !used.has(k) && portrait(q));
        if (j > 0) { used.add(j); return slides.push({ kind: 'pair', photos: [p, list[j]] }); }
      }
      slides.push({ kind: 'solo', photos: [p] });
    });
    return slides;
  }
  _render() {
    if (!this._built || !this._list) return;
    const c = this._config, root = this.shadowRoot;
    if (!this._list.length) { const t = root.querySelector('.grid') || root.querySelector('.cap'); if (t) t.innerHTML = '<div class="msg">No photos in this album yet</div>'; return; }
    if (c.mode === 'grid') {
      root.querySelector('.grid').innerHTML = this._list.map((p, i) => `<button class="tile" data-i="${i}"><img src="${esc(p.thumb)}" loading="lazy" decoding="async" alt=""></button>`).join('');
      return;
    }
    this._slides = this._buildSlides(this._list);
    this._settle(this._idx).then(() => { this._show(this._idx); this._settle(this._idx + 1); });
    this._schedule();
  }
  // Apple's width/height ignore the rotation flag; measure the photo as the browser draws it and regroup if needed.
  _probe(p) {
    if (p._probed) return Promise.resolve(false);
    return new Promise(res => {
      const im = new Image(), done = ch => { p._probed = true; res(ch); };
      im.onload = () => { const was = p.h > p.w * 1.05; if (im.naturalWidth && im.naturalHeight) { p.w = im.naturalWidth; p.h = im.naturalHeight; } done((p.h > p.w * 1.05) !== was); };
      im.onerror = () => done(false);
      im.src = p.full;
    });
  }
  async _settle(i) {
    const sl = this._slides && this._slides[i]; if (!sl || this._config.fit !== 'smart') return;
    const changed = (await Promise.all(sl.photos.map(p => this._probe(p)))).some(Boolean);
    if (changed) this._slides = [...this._slides.slice(0, i), ...this._buildSlides(this._slides.slice(i).flatMap(x => x.photos))];
  }
  _show(i) {
    const sl = this._slides[i]; if (!sl) return;
    const p = sl.photos[0];
    const layers = [...this.shadowRoot.querySelectorAll('.frame .layer')], cur = layers.find(x => x.classList.contains('on')), next = layers.find(x => x !== cur) || layers[0];
    next.innerHTML = sl.kind === 'pair' ? `<div class="pair">${sl.photos.map(q => `<img src="${esc(q.full)}" alt="">`).join('')}</div>`
      : sl.kind === 'solo' ? `<img class="bg" src="${esc(p.thumb || p.full)}" alt=""><img class="fg" src="${esc(p.full)}" alt="">`
      : `<img src="${esc(p.full)}" alt="">`;
    const imgs = [...next.querySelectorAll('img')]; let left = imgs.length;
    const reveal = () => { next.classList.add('on'); if (cur && cur !== next) cur.classList.remove('on'); };
    imgs.forEach(im => { const done = () => { if (--left === 0) reveal(); }; if (im.complete && im.naturalWidth) done(); else { im.onload = done; im.onerror = done; } });
    const cap = this.shadowRoot.querySelector('.cap'), when = p.taken || p.added;
    cap.innerHTML = this._config.caption && when ? `<span>${esc(MONTHS[new Date(when).getMonth()] + ' ' + new Date(when).getFullYear())}</span><b>${esc(ago(when))}</b>` : '';
  }
  _schedule() {
    clearTimeout(this._timer);
    const c = this._config;
    if (c.mode !== 'slideshow' || !this._slides || this._slides.length < 2) return;
    this._timer = setTimeout(async () => { this._idx = (this._idx + 1) % this._slides.length; await this._settle(this._idx); this._show(this._idx); this._settle(this._idx + 1); this._schedule(); }, Math.max(3, Number(c.interval) || 10) * 1000);
  }
  _click(e) {
    const t = e.target, c = this._config;
    const v = t.closest('[data-v]');
    if (v) { e.stopPropagation(); if (v.dataset.v === 'close') this._close(); else this._step(v.dataset.v === 'next' ? 1 : -1); return; }
    if (t.closest('.viewer')) { if (this._swiped) { this._swiped = false; return; } return; }
    const tile = t.closest('[data-i]'), frame = t.closest('.frame');
    if (!tile && !frame) return;
    if (c.tap === 'none') return;
    if (c.tap === 'more-info') { this.dispatchEvent(new CustomEvent('hass-more-info', { bubbles: true, composed: true, detail: { entityId: c.entity } })); return; }
    this._open(tile ? +tile.dataset.i : Math.max(0, this._list.indexOf((this._slides[this._idx] || { photos: [] }).photos[0])));
  }
  _open(i) { this._vi = i; this._vOpen = true; clearTimeout(this._timer); this.shadowRoot.querySelector('.viewer').classList.add('open'); this._vshow(); }
  _close() { this._vOpen = false; this.shadowRoot.querySelector('.viewer').classList.remove('open'); this._schedule(); }
  _step(d) { if (!this._vOpen) return; const n = this._list.length; this._vi = (this._vi + d + n) % n; this._vshow(); }
  _vshow() {
    const p = this._list[this._vi]; if (!p) return;
    this.shadowRoot.querySelector('.viewer img').src = p.full;
    const when = p.taken || p.added;
    this.shadowRoot.querySelector('.vcap').innerHTML = `${when ? esc(fmtDate(when)) : 'Photo'}<small>${this._vi + 1} of ${this._list.length}</small>`;
  }
}
if (!customElements.get('icloud-album-card')) {
  customElements.define('icloud-album-card', ICloudAlbumCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: 'icloud-album-card', name: 'iCloud Shared Album', description: 'Slideshow, grid or latest photo from an iCloud Shared Album', preview: false, documentationURL: 'https://github.com/DeltaEchoFour/ha-icloud-shared-album' });
  console.info(`%c ICLOUD-ALBUM-CARD %c ${VERSION} `, 'background:#111;color:#fff', '');
}
