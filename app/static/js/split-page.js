// app/static/js/split-page.js
// Página "Dividir PDF": seleção por miniaturas + ações (girar/recortar) + POST para /api/split

const PREFIX = 'split';
const ROOT_SELECTOR = '#preview-' + PREFIX;
// Não use ".page" (pode ser wrapper genérico). Incluí [data-page] por segurança.
const ITEM_SELECTOR = '.page-wrapper, .page-thumb, .thumb-card, [data-page]';
const CONTENT_SELECTOR = 'img,canvas,.thumb-canvas,.thumb-image';

let currentFile = null;

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
function normalizeAngle(a){ a = Number(a) || 0; a = a % 360; if (a < 0) a += 360; return a; }

/* -------------------- helpers -------------------- */
const root   = () => document.querySelector(ROOT_SELECTOR);
const cards  = () => Array.from(root()?.querySelectorAll(ITEM_SELECTOR) || []);
const indexOf = (el) => cards().indexOf(el);
const hostOf = (el) => el?.closest?.(ITEM_SELECTOR) || el;
const getContent = (el) => hostOf(el)?.querySelector?.(CONTENT_SELECTOR);

/* Rotação do preview usa um frame, para girar conteúdo + overlays juntos */
function ensureFrame(thumb) {
  if (!thumb) return null;
  let frame = thumb.querySelector(':scope > .thumb-frame');
  let content = getContent(thumb);
  if (!frame) {
    frame = document.createElement('div');
    frame.className = 'thumb-frame';
    Object.assign(frame.style, { position:'relative', width:'100%', height:'100%', display:'block' });
    if (content) {
      content.parentNode.insertBefore(frame, content);
      frame.appendChild(content);
      content.style.display = 'block';
      content.style.maxWidth = '100%';
      content.style.height = 'auto';
    } else {
      thumb.appendChild(frame);
    }
  } else if (!content) {
    content = getContent(frame) || frame.querySelector(CONTENT_SELECTOR);
  }
  return { frame, content };
}

function pageNumberFromEl(el) {
  const h = hostOf(el);
  const ds = h?.dataset || {};
  const v = ds.page ?? ds.pageId ?? h.getAttribute?.('data-page');
  const n = parseInt(v, 10);
  if (Number.isFinite(n) && n > 0) return n;
  const i = indexOf(h);
  return i >= 0 ? i + 1 : null;
}

/* -------------------- seleção -------------------- */
function setSelected(el, on) {
  const h = hostOf(el);
  if (!h) return;
  const flag = !!on;
  h.classList.toggle('is-selected', flag);
  h.setAttribute('aria-selected', flag ? 'true' : 'false');
  h.dataset.selected = flag ? 'true' : 'false';

  let check = h.querySelector('.select-check');
  if (flag && !check) {
    check = document.createElement('div');
    check.className = 'select-check';
    check.textContent = '✔';
    h.appendChild(check);
  }
  if (!flag && check) check.style.opacity = 0;

  const cb = h.querySelector('input[name="page-select"]');
  if (cb) cb.checked = flag;
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
    .forEach(el => setSelected(el, false));
  try { window.SplitUI?.clear?.(); } catch {}
}

/* Delegação GLOBAL robusta — captura antes do DnD e ignora controles */
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

  // Capture phase para não ser bloqueado por stopPropagation() do DnD
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
      card.focus?.({ preventScroll: true });
    }
    down = null;
  }, true);

  // Teclado: Space/Enter = toggle; Esc = limpar (só se foco está num card do split)
  document.addEventListener('keydown', (ev) => {
    const r = root(); if (!r) return;
    const active = document.activeElement?.closest?.(ITEM_SELECTOR);
    if (!active || !r.contains(active)) return;
    if (ev.key === ' ' || ev.key === 'Enter') {
      ev.preventDefault();
      setSelected(active, !isSelected(active));
    } else if (ev.key === 'Escape') {
      clearAllSelection();
    }
  });
}

/* Garante tabindex e bloqueia propagação de checkboxes internos */
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

/* -------------------- rotação -------------------- */
function applyPreviewRotation(thumb, angle) {
  const { frame } = ensureFrame(thumb);
  if (!frame) return;
  frame.style.transformOrigin = '50% 50%';
  frame.style.transform = `rotate(${normalizeAngle(angle)}deg)`;
}
function rotateThumb(thumb, delta) {
  let a = parseInt(thumb.getAttribute('data-rotation') || '0', 10);
  if (!Number.isFinite(a)) a = 0;
  a = normalizeAngle(a + delta);
  thumb.setAttribute('data-rotation', String(a));
  applyPreviewRotation(thumb, a);
  setSelected(thumb, true);
}

/* -------------------- crop -------------------- */
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

  const { frame, content } = ensureFrame(thumb);
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
      background:'#09a36b', color:'#fff', fontSize: '.72rem',
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
    const perc = toPercentBox(r);
    thumb.dataset.cropX = String(perc.x);
    thumb.dataset.cropY = String(perc.y);
    thumb.dataset.cropW = String(perc.w);
    thumb.dataset.cropH = String(perc.h);
    setSelected(thumb, true);
  }

  overlay.addEventListener('mousedown', onDown, { passive:false });
  overlay.addEventListener('mousemove', onMove, { passive:false });
  window.addEventListener('mouseup', onUp, { passive:true, once:true });

  overlay.addEventListener('touchstart', onDown, { passive:false });
  overlay.addEventListener('touchmove', onMove, { passive:false });
  window.addEventListener('touchend', onUp, { passive:true, once:true });
}

/* -------------------- mapping: display(rodado) → base(não rodado) -------------------- */
function degToRightAngle(a){ a = normalizeAngle(a); return (a===90||a===180||a===270)?a:0; }
function transformPointNorm(x, y, angleDeg, inverse=false) {
  const a = degToRightAngle(angleDeg);
  if (!inverse) {
    if (a===0)   return {x,y};
    if (a===90)  return {x:y,           y:1 - x};
    if (a===180) return {x:1 - x,       y:1 - y};
    if (a===270) return {x:1 - y,       y:x};
  } else {
    if (a===0)   return {x,y};
    if (a===90)  return {x:1 - y,       y:x};
    if (a===180) return {x:1 - x,       y:1 - y};
    if (a===270) return {x:y,           y:1 - x};
  }
  return {x,y};
}
function rectDisplayToBase(x, y, w, h, angleDeg) {
  const p0 = transformPointNorm(x, y, angleDeg, true);
  const p1 = transformPointNorm(x + w, y + h, angleDeg, true);
  const minX = Math.min(p0.x, p1.x), maxX = Math.max(p0.x, p1.x);
  const minY = Math.min(p0.y, p1.y), maxY = Math.max(p0.y, p1.y);
  return { x: minX, y: minY, w: (maxX - minX), h: (maxY - minY) };
}
function round6(n){ return Math.max(0, Math.min(1, Math.round(n * 1e6) / 1e6)); }

/* -------------------- coleta -------------------- */
function collectSelectedPagesInDisplayOrder() {
  const items = cards();
  const pages = [];
  for (const el of items) {
    if (!isSelected(el)) continue;
    const p = pageNumberFromEl(el);
    if (p) pages.push(p);
  }
  return pages;
}
function collectRotationsMap() {
  const r = root(); if (!r) return null;
  const items = r.querySelectorAll(`${ITEM_SELECTOR}[data-rotation]`);
  if (!items.length) return null;
  const map = {};
  items.forEach(el => {
    const p = pageNumberFromEl(el);
    const a = normalizeAngle(el.getAttribute('data-rotation'));
    if (p && Number.isFinite(a) && a !== 0) map[p] = a;
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

    const rot = normalizeAngle(el.getAttribute('data-rotation') || 0);
    const baseRect = rectDisplayToBase(x, y, w, h, rot);

    mods[p] = { crop: { unit:'percent', origin:'topleft',
      x: round6(baseRect.x), y: round6(baseRect.y),
      w: round6(baseRect.w), h: round6(baseRect.h) } };
  });
  return Object.keys(mods).length ? mods : null;
}

/* -------------------- POST /api/split -------------------- */
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

  const res = await fetch('/api/split', { method: 'POST', headers, body: fd });
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

/* -------------------- init -------------------- */
function disableDropOverlays() {
  document.querySelectorAll('.drop-overlay,.drop-hint').forEach(el => { el.style.pointerEvents = 'none'; });
}
let __prevCardCount = 0;
function initOnce() {
  bindSelectionGlobal();   // seleção por clique simples (toggle)
  bindCards();
  disableDropOverlays();
  clearAllSelection();
  __prevCardCount = cards().length;

  const inputEl = document.getElementById(`input-${PREFIX}`);
  if (inputEl) {
    inputEl.addEventListener('change', () => {
      currentFile = inputEl.files?.[0] || null;
      enableActions(!!currentFile);
      clearAllSelection();
    });
    if (inputEl.files?.[0]) currentFile = inputEl.files[0];
  }

  document.addEventListener('gv:file-dropped', (ev) => {
    const { prefix, file } = ev.detail || {};
    if (prefix === PREFIX && file instanceof File) {
      currentFile = file; enableActions(true);
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

  // Botões de ação (rot/crop) — não interferem na seleção
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-action]'); if (!btn) return;
    const thumb = hostOf(btn.closest(ITEM_SELECTOR)); if (!thumb) return;
    const act = (btn.dataset.action || '').toLowerCase();
    ev.preventDefault(); ev.stopPropagation();
    if (act === 'rot-left'  || act === 'rotate-left'  || act === 'rotate-l') return rotateThumb(thumb, -90);
    if (act === 'rot-right' || act === 'rotate-right' || act === 'rotate-r') return rotateThumb(thumb, +90);
    if (act === 'crop' || act === 'cut') return startCropOnThumb(thumb);
    if (act === 'crop-clear' || act === 'uncrop') return clearCrop(thumb);
  });
}

document.addEventListener('DOMContentLoaded', initOnce);

// Re-binda e só limpa SE o número de cards mudou (evita limpar por causa do "✔")
new MutationObserver(() => {
  const count = cards().length;
  if (count !== __prevCardCount) {
    bindCards();
    if (count > __prevCardCount) requestAnimationFrame(() => clearAllSelection());
    __prevCardCount = count;
  }
}).observe(document.body, { childList: true, subtree: true });