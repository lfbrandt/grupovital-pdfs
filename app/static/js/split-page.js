// Página "Dividir PDF": seleção por miniaturas + ações (girar/recortar) + POST para /api/split
// >>> Atualização: aplicação de rotação/fit centralizada via utils.fitRotateMedia()
'use strict';

const PREFIX = 'split';
const ROOT_SELECTOR = '#preview-' + PREFIX;
// Não use ".page" (pode ser wrapper genérico). Incluí [data-page] por segurança.
const ITEM_SELECTOR = '.page-wrapper, .page-thumb, .thumb-card, [data-page]';
const CONTENT_SELECTOR = 'img,canvas,.thumb-canvas,.thumb-image';

let currentFile = null;

/* -------------------- utils globais -------------------- */
const U = (window.utils || {});
const fitRotateMedia = U.fitRotateMedia || function(){};
const normalizeAngle = U.normalizeAngle || (a=>{ a=Number(a)||0; a%=360; if(a<0)a+=360; return a; });

/* -------------------- persistência de seleção -------------------- */
/** Store em memória + sessionStorage, chaveado por arquivo/sessão. */
const SelectionStore = (() => {
  let key = 'gv_split_sel_default';
  let set = new Set();

  function load() {
    try {
      const raw = sessionStorage.getItem(key);
      set = raw ? new Set(JSON.parse(raw)) : new Set();
    } catch (_) { set = new Set(); }
  }
  function save() {
    try { sessionStorage.setItem(key, JSON.stringify(Array.from(set))); } catch (_) {}
  }
  function setKey(k) { if (k && k !== key) { key = k; load(); } }
  function add(id) { if (id != null) { set.add(String(id)); save(); } }
  function del(id) { if (id != null) { set.delete(String(id)); save(); } }
  function has(id) { return set.has(String(id)); }
  function clear() { set.clear(); save(); }
  function all() { return Array.from(set); }

  // bootstrap
  load();
  return { setKey, add, del, has, clear, all };
})();

function computePersistKey() {
  const r = root();
  const idx = r?.getAttribute('data-file-index') || r?.getAttribute('data-index') || '';
  const name = (document.querySelector('.file-name')?.textContent || '').trim();
  return `gv_split_sel_${idx || name || 'default'}`;
}

/* -------------------- util -------------------- */
function msg(text, tipo) {
  try { if (typeof window.mostrarMensagem === 'function') return window.mostrarMensagem(text, tipo); } catch(_){}
  (tipo === 'erro' ? console.error : console.log)(text);
}
function enableActions(enabled) {
  const btnSplit = document.getElementById(`btn-${PREFIX}`);
  const btnAll   = document.getElementById('btn-split-all');
  if (btnSplit) btnSplit.disabled = !enabled;
  if (btnAll)   btnAll.disabled   = !enabled;
}
function getCSRFToken() {
  const m = document.querySelector('meta[name="csrf-token"], meta[name="csp-nonce"]');
  if (m?.content) return m.content;
  const match = document.cookie.match(/(?:^|;)\s*csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

/* -------------------- helpers -------------------- */
const root   = () => document.querySelector(ROOT_SELECTOR);
const cards  = () => Array.from(root()?.querySelectorAll(ITEM_SELECTOR) || []);
const indexOf = (el) => cards().indexOf(el);
const hostOf = (el) => el?.closest?.(ITEM_SELECTOR) || el;
const getContent = (el) => hostOf(el)?.querySelector?.(CONTENT_SELECTOR);

function pageNumberFromEl(el) {
  const h = hostOf(el);
  const ds = h?.dataset || {};
  const v = ds.page ?? ds.pageId ?? h.getAttribute?.('data-page');
  const n = parseInt(v, 10);
  if (Number.isFinite(n) && n > 0) return n;
  const i = indexOf(h);
  return i >= 0 ? i + 1 : null;
}
function pageStableId(el) {
  const h = hostOf(el);
  return h?.getAttribute?.('data-page-id') || h?.dataset?.pageId || h?.getAttribute?.('data-page') || String(pageNumberFromEl(h));
}

/* -------------------- UI do marcador ✔ -------------------- */
function ensureSelectionUI(card) {
  if (!card) return;
  const selected = card.classList.contains('is-selected') ||
                   card.getAttribute('aria-selected') === 'true' ||
                   card.dataset?.selected === 'true';
  const existing = card.querySelector('.select-check');

  if (selected) {
    if (!existing) {
      const check = document.createElement('div');
      check.className = 'select-check';
      check.textContent = '✔';
      card.appendChild(check);
    } else {
      existing.style.removeProperty('opacity');
      existing.hidden = false;
    }
  } else {
    if (existing) existing.remove();
  }
}

/* -------------------- seleção -------------------- */
function setSelected(el, on) {
  const h = hostOf(el);
  if (!h) return;
  const id = pageStableId(h);
  const flag = !!on;

  h.classList.toggle('is-selected', flag);
  h.setAttribute('aria-selected', flag ? 'true' : 'false');
  h.dataset.selected = flag ? 'true' : 'false';

  ensureSelectionUI(h);

  const cb = h.querySelector('input[name="page-select"]');
  if (cb) cb.checked = flag;

  if (flag) SelectionStore.add(id); else SelectionStore.del(id);
}
function isSelected(el) {
  const h = hostOf(el);
  return h?.classList.contains('is-selected')
      || h?.getAttribute('aria-selected') === 'true'
      || h?.dataset?.selected === 'true'
      || !!h?.querySelector?.('input[name="page-select"]:checked');
}
function clearAllSelection() {
  const r = root(); if (!r) return;
  r.querySelectorAll(`${ITEM_SELECTOR}.is-selected, ${ITEM_SELECTOR}[aria-selected="true"]`)
    .forEach(el => {
      el.classList.remove('is-selected');
      el.setAttribute('aria-selected','false');
      if (el.dataset) el.dataset.selected = 'false';
      const cb = el.querySelector('input[name="page-select"]'); if (cb) cb.checked = false;
      el.querySelector('.select-check')?.remove();
    });
  SelectionStore.clear();
}
function reapplySelection() {
  const stored = SelectionStore.all();
  if (!stored.length) return;
  const wanted = new Set(stored.map(String));
  cards().forEach(el => {
    const id = String(pageStableId(el));
    const on = wanted.has(id);
    setSelected(el, on);
    ensureSelectionUI(el);
  });
}

/* -------------------- barras de controle (garantir X/↻) ------------- */
function ensureControls(card){
  if(!card || card.__controlsEnsured) return;

  const bars = card.querySelectorAll(':scope > .file-controls, :scope > .thumb-actions');
  if(bars.length>1){
    for(let i=1;i<bars.length;i++) bars[i].remove();
  }
  let bar = bars[0] || null;

  if(!bar){
    bar=document.createElement('div');
    bar.className='file-controls';
    bar.innerHTML =
      `<button class="remove-file" type="button" title="Remover página" data-no-drag="true" aria-label="Remover página">×</button>
       <button class="rotate-page"  type="button" title="Girar 90°"       data-no-drag="true" aria-label="Girar 90°">↻</button>`;
    card.appendChild(bar);
  }else{
    if(!bar.querySelector('.remove-file,[data-action="remove"],[data-action="delete"],[data-action="close"]')){
      const b=document.createElement('button');
      b.className='remove-file'; b.type='button'; b.title='Remover página';
      b.setAttribute('data-no-drag','true'); b.setAttribute('aria-label','Remover página'); b.textContent='×';
      bar.prepend(b);
    }
    if(!bar.querySelector('.rotate-page,[data-action="rot-right"],[data-action="rotate-right"]')){
      const b=document.createElement('button');
      b.className='rotate-page'; b.type='button'; b.title='Girar 90°';
      b.setAttribute('data-no-drag','true'); b.setAttribute('aria-label','Girar 90°'); b.textContent='↻';
      bar.append(b);
    }
  }

  card.__controlsEnsured = true;
}

/* -------------------- suporte a frame/media + rotação centralizada -------- */
function ensureFrame(card) {
  if (!card) return null;

  let frame = card.querySelector(':scope > .thumb-frame');
  if (!frame) {
    frame = document.createElement('div');
    frame.className = 'thumb-frame';
    Object.assign(frame.style, {
      position: 'absolute',
      inset: '0',
      inlineSize: '100%',
      blockSize: '100%',
      overflow: 'hidden'
    });
    const cs = getComputedStyle(card);
    if (cs.position === 'static') card.style.position = 'relative';
    card.appendChild(frame);
  }

  let media =
    frame.querySelector('.thumb-media') ||
    card.querySelector(':scope > img, :scope > canvas, .thumb-image, .thumb-canvas');

  if (media && media.parentElement !== frame) {
    media.parentElement?.removeChild(media);
    frame.appendChild(media);
  }
  if (media) {
    media.classList.add('thumb-media');
    Object.assign(media.style, {
      position:'absolute', left:'50%', top:'50%',
      transform:'translate(-50%, -50%)',
      transformOrigin:'50% 50%',
      maxWidth: 'none', height: 'auto',
      backfaceVisibility: 'hidden', display:'block'
    });
  }

  return { frame, media };
}

function applyPreviewRotation(card, angle) {
  const ctx = ensureFrame(card);
  if (!ctx || !ctx.frame || !ctx.media) return;
  const ang = normalizeAngle(angle || 0);
  ctx.media.dataset.deg = String(ang);
  fitRotateMedia({ frameEl: ctx.frame, mediaEl: ctx.media, angle: ang });
}

function rotateThumb(card, delta) {
  let a = parseInt(card.getAttribute('data-rotation') || '0', 10);
  if (!Number.isFinite(a)) a = 0;
  a = normalizeAngle(a + delta);
  card.setAttribute('data-rotation', String(a));
  applyPreviewRotation(card, a);
  setSelected(card, true);
  ensureSelectionUI(card);
}

/* -------------------- crop (inalterado) -------------------- */
function clearCrop(thumb) {
  delete thumb.dataset.cropX;
  delete thumb.dataset.cropY;
  delete thumb.dataset.cropW;
  delete thumb.dataset.cropH;
  thumb.querySelector('.gv-crop-overlay')?.remove();
  thumb.querySelector('.gv-crop-badge')?.remove();
}
function startCropOnThumb(thumb) {
  if (thumb.dataset.cropW && thumb.dataset.cropH) { clearCrop(thumb); return; }

  const ctx = ensureFrame(thumb);
  const frame = ctx?.frame;
  const content = ctx?.media;
  if (!content || !frame) return msg('Não foi possível iniciar o recorte (prévia indisponível).', 'erro');

  let overlay = frame.querySelector(':scope > .gv-crop-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'gv-crop-overlay';
    overlay.setAttribute('aria-label', 'Seleção de recorte');
    overlay.setAttribute('role', 'region');
    Object.assign(overlay.style, { position:'absolute', inset:'0', cursor:'crosshair' });
    frame.appendChild(overlay);
  }
  let rectEl = overlay.querySelector('.gv-crop-rect');
  if (!rectEl) {
    rectEl = document.createElement('div');
    rectEl.className = 'gv-crop-rect';
    Object.assign(rectEl.style, {
      position:'absolute', border:'2px dashed currentColor', color:'#09a36b',
      backgroundColor:'rgba(0,0,0,.08)', boxSizing:'border-box', pointerEvents:'none'
    });
    overlay.appendChild(rectEl);
  }
  let badge = thumb.querySelector('.gv-crop-badge');
  if (!badge) {
    badge = document.createElement('div');
    badge.className = 'gv-crop-badge';
    badge.textContent = 'Recorte';
    Object.assign(badge.style, {
      position:'absolute', top:'.35rem', left:'.35rem',
      background:'#09a36b', color:'#fff', fontSize:'.72rem',
      padding:'.15rem .35rem', borderRadius:'.4rem', zIndex:'3'
    });
    thumb.appendChild(badge);
  }

  const contentBox = content.getBoundingClientRect();
  const overlayBox = overlay.getBoundingClientRect();

  let dragging = false;
  let startX = 0, startY = 0;

  function clamp(val, min, max){ return Math.max(min, Math.min(max, val)); }
  function toPercentBox(sel) {
    const nx = (sel.left  - contentBox.left) / contentBox.width;
    const ny = (sel.top   - contentBox.top ) / contentBox.height;
    const nw =  sel.width / contentBox.width;
    const nh =  sel.height/ contentBox.height;
    return { x: clamp(nx,0,1), y: clamp(ny,0,1), w: clamp(nw,0,1), h: clamp(nh,0,1) };
  }

  function onDown(e) {
    dragging = true;
    const ptX = (e.touches?.[0]?.clientX ?? e.clientX);
    const ptY = (e.touches?.[0]?.clientY ?? e.clientY);
    startX = clamp(ptX, contentBox.left, contentBox.right);
    startY = clamp(ptY, contentBox.top , contentBox.bottom);
    rectEl.style.left = `${startX - overlayBox.left}px`;
    rectEl.style.top  = `${startY - overlayBox.top }px`;
    rectEl.style.width = '0px';
    rectEl.style.height = '0px';
    e.preventDefault();
  }
  function onMove(e) {
    if (!dragging) return;
    const ptX = (e.touches?.[0]?.clientX ?? e.clientX);
    const ptY = (e.touches?.[0]?.clientY ?? e.clientY);
    const curX = clamp(ptX, contentBox.left, contentBox.right);
    const curY = clamp(ptY, contentBox.top , contentBox.bottom);
    const left = Math.min(startX, curX);
    const top  = Math.min(startY, curY);
    const right= Math.max(startX, curX);
    const bot  = Math.max(startY, curY);
    rectEl.style.left   = `${left  - overlayBox.left}px`;
    rectEl.style.top    = `${top   - overlayBox.top }px`;
    rectEl.style.width  = `${right - left}px`;
    rectEl.style.height = `${bot   - top }px`;
    e.preventDefault();
  }
  function onUp() {
    if (!dragging) return;
    dragging = false;
    const r = rectEl.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) { clearCrop(thumb); return; }
    const nx = (r.left - contentBox.left) / contentBox.width;
    const ny = (r.top  - contentBox.top ) / contentBox.height;
    const nw =  r.width / contentBox.width;
    const nh =  r.height/ contentBox.height;
    thumb.dataset.cropX = String(Math.max(0, Math.min(1, nx)));
    thumb.dataset.cropY = String(Math.max(0, Math.min(1, ny)));
    thumb.dataset.cropW = String(Math.max(0, Math.min(1, nw)));
    thumb.dataset.cropH = String(Math.max(0, Math.min(1, nh)));
    setSelected(thumb, true);
    ensureSelectionUI(thumb);
  }

  overlay.addEventListener('mousedown', onDown, { passive:false });
  overlay.addEventListener('mousemove', onMove, { passive:false });
  window.addEventListener('mouseup', onUp, { passive:true, once:true });

  overlay.addEventListener('touchstart', onDown, { passive:false });
  overlay.addEventListener('touchmove', onMove, { passive:false });
  window.addEventListener('touchend', onUp, { passive:true, once:true });
}

/* -------------------- coleta/POST (inalterado) -------------------- */
function collectSelectedPagesInDisplayOrder() {
  const order = cards();
  const orderIds = order.map(el => String(pageStableId(el)));
  const picked = SelectionStore.all().filter(id => orderIds.includes(String(id)));
  return picked.map(id => {
    const el = order.find(c => String(pageStableId(c)) === String(id));
    return pageNumberFromEl(el);
  }).filter(Boolean);
}
function collectRotationsMap() {
  const r = root(); if (!r) return null;
  const items = r.querySelectorAll(`${ITEM_SELECTOR}[data-rotation]`);
  if (!items.length) return null;
  const map = {};
  items.forEach(el => {
    const p = pageNumberFromEl(el);
    const a = normalizeAngle(el.getAttribute('data-rotation'));
    if (p && a !== 0) map[p] = a;
  });
  return Object.keys(map).length ? map : null;
}
function collectCropsMap() {
  const r = root(); if (!r) return null;
  const items = r.querySelectorAll(ITEM_SELECTOR);
  const mods = {};
  items.forEach(el => {
    const p = pageNumberFromEl(el);
    if (!p) return;

    const x = Number(el.dataset.cropX);
    const y = Number(el.dataset.cropY);
    const w = Number(el.dataset.cropW);
    const h = Number(el.dataset.cropH);
    if (![x,y,w,h].every(Number.isFinite) || !(w>0 && h>0)) return;

    mods[p] = { crop: { unit:'percent', origin:'topleft',
      x: +x.toFixed(6), y: +y.toFixed(6), w: +w.toFixed(6), h: +h.toFixed(6) } };
  });
  return Object.keys(mods).length ? mods : null;
}

async function postSplit({ file, pages, rotations, outName, mods, mode }) {
  const fd = new FormData();
  fd.append('file', file);
  if (mode === 'selected' && Array.isArray(pages) && pages.length) {
    fd.append('pages', JSON.stringify(pages));
  }
  if (rotations && Object.keys(rotations).length) {
    fd.append('rotations', JSON.stringify(rotations));
  }
  if (mods && Object.keys(mods).length) {
    fd.append('modificacoes', JSON.stringify(mods));
  }

  const headers = {};
  const csrf = getCSRFToken();
  if (csrf) headers['X-CSRFToken'] = csrf;

  const res = await fetch('/api/split', { method: 'POST', headers, body: fd, credentials: 'same-origin' });
  if (!res.ok) {
    const t = await res.text().catch(()=> '');
    throw new Error(`Split falhou: HTTP ${res.status} ${t}`);
  }
  const blob = await res.blob();
  const disp = res.headers.get('Content-Disposition') || '';
  const m = /filename\*?=(?:UTF-8''|")?([^\";]+)/i.exec(disp);
  const filename = m ? decodeURIComponent(m[1]) : (outName || (mode==='all' ? 'paginas_divididas.zip' : 'paginas_selecionadas.pdf'));

  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  a.remove(); URL.revokeObjectURL(url);
}

/* -------------------- BLOQUEIO DE EDITOR/TECLAS (inalterado) -------------- */
function disableEditorTriggers() {
  if (document.__splitNoEditor) return;
  document.__splitNoEditor = true;

  const r = root(); if (!r) return;

  document.addEventListener('dblclick', (ev) => {
    const within = r.contains(ev.target) && !!ev.target.closest(ITEM_SELECTOR);
    if (within) { ev.stopImmediatePropagation(); ev.preventDefault(); }
  }, true);

  document.addEventListener('keydown', (ev) => {
    if (!r.contains(document.activeElement)) return;
    const k = (ev.key || '').toLowerCase();
    if (k === 'enter' || k === 'e') {
      ev.stopImmediatePropagation();
    }
  }, true);
}

function isEditControl(el) {
  if (!el || el.nodeType !== 1) return false;
  const act = (el.dataset?.action || '').toLowerCase();
  const title = (el.getAttribute('title') || '').toLowerCase();
  const aria  = (el.getAttribute('aria-label') || '').toLowerCase();
  const cls = el.classList || new DOMTokenList();
  if (act === 'edit' || act === 'open-editor' || /\bedit\b/.test(act)) return true;
  if (/\bedit\b/.test(title) || /\beditor\b/.test(title)) return true;
  if (/\bedit\b/.test(aria)  || /\beditor\b/.test(aria))  return true;
  if (cls.contains('btn-edit') || cls.contains('open-editor') || cls.contains('editor-open') || cls.contains('edit')) return true;
  if (el.tagName === 'A') {
    const href = el.getAttribute('href') || '';
    if (/\/edit(?:[/?#]|$)/i.test(href)) return true;
  }
  const dhref = el.dataset?.href || el.getAttribute('data-href') || '';
  if (dhref && /\/edit(?:[/?#]|$)/i.test(String(dhref))) return true;
  return false;
}
function pruneEditButtons() {
  const r = root(); if (!r) return;
  r.querySelectorAll('.thumb-actions, .file-controls').forEach(bar => {
    [...bar.children].forEach(btn => { if (isEditControl(btn)) btn.remove(); });
  });
}
function bindPruneObserver() {
  const r = root(); if (!r || r.__splitPruneObs) return;
  const run = () => pruneEditButtons();
  run();
  const mo = new MutationObserver(muts => {
    for (const m of muts) {
      for (const n of m.addedNodes || []) {
        if (n.nodeType === 1 && (
            n.matches?.('.thumb-actions, .file-controls, [data-action], a') ||
            n.querySelector?.('.thumb-actions, .file-controls, [data-action], a')
        )) { run(); return; }
      }
    }
  });
  mo.observe(r, { childList: true, subtree: true });
  r.__splitPruneObs = mo;
}

/* -------------------- seleção global (clique/teclas) ---------------------- */
function bindSelectionGlobal() {
  if (document.__splitDelegBound) return;
  document.__splitDelegBound = true;

  const isInteractive = (el) =>
    !!el.closest('[data-action],button,a,label,input,select,textarea,[contenteditable],.gv-crop-overlay,.file-controls');

  const getCard = (t) => {
    const r = root(); if (!r) return null;
    const c = t.closest?.(ITEM_SELECTOR);
    return (c && r.contains(c)) ? c : null;
  };

  let down = null;

  document.addEventListener('pointerdown', (ev) => {
    const card = getCard(ev.target);
    if (!card || isInteractive(ev.target)) { down = null; return; }
    down = { card, x: ev.clientX, y: ev.clientY };
  }, true);

  document.addEventListener('pointerup', (ev) => {
    if (!down) return;
    const card = getCard(ev.target);
    const moved = Math.hypot(ev.clientX - down.x, ev.clientY - down.y);
    if (card === down.card && moved < 5) {
      setSelected(card, !isSelected(card));
      ensureSelectionUI(card);
      card.focus?.({ preventScroll: true });
    }
    down = null;
  }, true);

  document.addEventListener('keydown', (ev) => {
    const r = root(); if (!r) return;
    const active = document.activeElement?.closest?.(ITEM_SELECTOR);
    if (!active || !r.contains(active)) return;

    if (ev.key === ' ' || ev.key === 'Enter') {
      ev.preventDefault();
      setSelected(active, !isSelected(active));
      ensureSelectionUI(active);
      ev.stopImmediatePropagation();
      return;
    }
    if (ev.key === 'Escape') {
      clearAllSelection();
      ev.stopImmediatePropagation();
    }
  }, true);
}

function bindCards() {
  cards().forEach(el => {
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    el.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      if (!cb.__stopClick) {
        cb.addEventListener('click', e => e.stopPropagation());
        cb.__stopClick = true;
      }
    });
  });
}

/* -------------------- init -------------------- */
function disableDropOverlays() {
  document.querySelectorAll('.drop-overlay,.drop-hint').forEach(el => { el.style.pointerEvents = 'none'; });
}
let __prevCardCount = 0;

function initOnce() {
  SelectionStore.setKey(computePersistKey());

  disableEditorTriggers();
  bindSelectionGlobal();
  bindCards();
  disableDropOverlays();
  cards().forEach(ensureControls);
  cards().forEach(c => {
    ensureSelectionUI(c);
    // aplica fit+rotate centralizado
    const ang = parseInt(c.getAttribute('data-rotation') || '0', 10) || 0;
    applyPreviewRotation(c, ang);
  });
  pruneEditButtons();
  bindPruneObserver();

  reapplySelection();
  __prevCardCount = cards().length;

  const inputEl = document.getElementById(`input-${PREFIX}`);
  if (inputEl) {
    inputEl.addEventListener('change', () => {
      currentFile = inputEl.files?.[0] || null;
      enableActions(!!currentFile);
      SelectionStore.setKey(computePersistKey());
      clearAllSelection();
    });
    if (inputEl.files?.[0]) currentFile = inputEl.files[0];
  }

  document.addEventListener('gv:file-dropped', (ev) => {
    const { prefix, file } = ev.detail || {};
    if (prefix === PREFIX && file instanceof File) {
      currentFile = file; enableActions(true);
      SelectionStore.setKey(computePersistKey());
      clearAllSelection();
    }
  });

  const btnSplit = document.getElementById(`btn-${PREFIX}`);
  if (btnSplit) {
    btnSplit.addEventListener('click', async () => {
      if (!currentFile) return msg('Selecione um PDF para dividir.', 'erro');
      const selected = collectSelectedPagesInDisplayOrder();
      if (!selected.length) { msg('Marque ao menos uma página ou use "Separar todas as páginas".', 'erro'); return; }
      const rotations = collectRotationsMap();
      const mods = collectCropsMap();
      try {
        enableActions(false);
        await postSplit({ file: currentFile, pages: selected, rotations, mods, mode: 'selected', outName: 'paginas_selecionadas.pdf' });
      } catch (e) { msg(e?.message || 'Falha ao dividir o PDF.', 'erro'); }
      finally { enableActions(true); }
    });
  }

  const btnAll = document.getElementById('btn-split-all');
  if (btnAll) {
    btnAll.addEventListener('click', async () => {
      if (!currentFile) return msg('Selecione um PDF para dividir.', 'erro');
      const rotations = collectRotationsMap();
      const mods = collectCropsMap();
      try {
        enableActions(false);
        await postSplit({ file: currentFile, pages: undefined, rotations, mods, mode: 'all', outName: 'paginas_divididas.zip' });
      } catch (e) { msg(e?.message || 'Falha ao dividir o PDF.', 'erro'); }
      finally { enableActions(true); }
    });
  }

  // delegação para botões do card (rotate/crop)
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest?.('[data-action],button.rotate-page,a'); if (!btn) return;
    const r = root(); if (!r || !r.contains(btn)) return;

    if (isEditControl(btn)) { ev.preventDefault(); ev.stopImmediatePropagation(); ev.stopPropagation(); return; }

    const card = hostOf(btn.closest(ITEM_SELECTOR)); if (!card) return;
    const act = (btn.dataset.action || '').toLowerCase();

    const isRotate = btn.matches('button.rotate-page') ||
                     act === 'rot-left'  || act === 'rotate-left'  || act === 'rotate-l' ||
                     act === 'rot-right' || act === 'rotate-right' || act === 'rotate-r';
    if (isRotate) {
      ev.preventDefault(); ev.stopImmediatePropagation(); ev.stopPropagation();
      rotateThumb(card, (act.includes('left') || act.endsWith('-l')) ? -90 : +90);
      return;
    }

    if (act === 'crop' || act === 'cut') {
      ev.preventDefault(); ev.stopImmediatePropagation(); ev.stopPropagation();
      startCropOnThumb(card);
      return;
    }
    if (act === 'crop-clear' || act === 'uncrop') {
      ev.preventDefault(); ev.stopImmediatePropagation(); ev.stopPropagation();
      clearCrop(card);
      return;
    }
  }, true);
}

document.addEventListener('DOMContentLoaded', initOnce);

// Reidrata seleção/fit/rotate quando o conteúdo de cards muda
new MutationObserver((muts)=>{
  let changed = false;

  for (const m of muts) {
    const tgt = m.target;
    const tgtCard = tgt?.nodeType === 1 ? (tgt.matches?.(ITEM_SELECTOR) ? tgt : tgt.closest?.(ITEM_SELECTOR)) : null;
    if (tgtCard) {
      changed = true;
      ensureControls(tgtCard);
      ensureSelectionUI(tgtCard);
      const ang = parseInt(tgtCard.getAttribute('data-rotation') || '0', 10) || 0;
      applyPreviewRotation(tgtCard, ang);
    }

    m.addedNodes?.forEach(n=>{
      if(n.nodeType===1){
        if(n.matches?.(ITEM_SELECTOR)){
          changed = true;
          ensureControls(n);
          ensureSelectionUI(n);
          const ang = parseInt(n.getAttribute('data-rotation') || '0', 10) || 0;
          applyPreviewRotation(n, ang);
        }
        n.querySelectorAll?.(ITEM_SELECTOR).forEach(el=>{
          changed = true;
          ensureControls(el);
          ensureSelectionUI(el);
          const ang = parseInt(el.getAttribute('data-rotation') || '0', 10) || 0;
          applyPreviewRotation(el, ang);
        });
      }
    });

    // Se removeram explicitamente o .select-check e item ainda estiver marcado, repõe
    if (m.removedNodes && m.removedNodes.length) {
      m.removedNodes.forEach(n=>{
        if (n.nodeType === 1 && n.classList?.contains('select-check')) {
          const card = m.target?.closest?.(ITEM_SELECTOR) || m.target;
          if (card && (card.dataset.selected === 'true' || card.classList.contains('is-selected'))) {
            ensureSelectionUI(card);
          }
        }
      });
    }
  }

  const count = cards().length;
  if (changed || count !== __prevCardCount) {
    bindCards();
    reapplySelection();
    __prevCardCount = count;
  }
}).observe(document.body, { childList: true, subtree: true });