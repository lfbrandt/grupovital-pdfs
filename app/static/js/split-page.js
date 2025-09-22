// Página "Dividir PDF": seleção por miniaturas + ações (girar/recortar/remover) + POST para /api/split
// Coopera com preview.js (não força estilos inline da mídia). CSP ok (sem inline).
'use strict';

const PREFIX = 'split';
const ROOT_SELECTOR = '#preview-' + PREFIX;
// cobre todos os formatos que o preview pode gerar
const ITEM_SELECTOR = '.page-wrapper, .page-thumb, .thumb-card, [data-page], [data-page-id], [data-src-page]';

let currentFile = null;

/* -------------------- utils globais -------------------- */
const U = (window.utils || {});
const normalizeAngle = U.normalizeAngle || (a => { a = Number(a) || 0; a %= 360; if (a < 0) a += 360; return a; });

// usa o util oficial se existir; fallback para <meta name="csrf-token"> ou cookie csrf_token
function getCSRFToken() {
  try {
    if (typeof U.getCSRFToken === 'function') return U.getCSRFToken();
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta?.content) return meta.content;
    const m = document.cookie.match(/(?:^|;)\s*csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  } catch { return ''; }
}

/* -------------------- util -------------------- */
function msg(text, tipo) {
  try { if (typeof window.mostrarMensagem === 'function') return window.mostrarMensagem(text, tipo); } catch (_) {}
  (tipo === 'erro' ? console.error : console.log)(text);
}
function enableActions(enabled) {
  const btnSplit = document.getElementById(`btn-${PREFIX}`);
  const btnAll   = document.getElementById('btn-split-all');
  const btnClr   = document.getElementById('btn-clear-all');
  if (btnSplit) btnSplit.disabled = !enabled;
  if (btnAll)   btnAll.disabled   = !enabled;
  if (btnClr)   btnClr.disabled   = false; // permitir limpar sempre
}

/* -------------------- helpers -------------------- */
const root    = () => document.querySelector(ROOT_SELECTOR);
const cards   = () => Array.from(root()?.querySelectorAll(ITEM_SELECTOR) || []);
const indexOf = (el) => cards().indexOf(el);
const hostOf  = (el) => el?.closest?.(ITEM_SELECTOR) || el;

/** Número de página ORIGINAL 1-based.
 *  Preferimos data-src-page (0-based vindo do preview) e somamos +1.
 *  Fallback: data-page / data-page-id (já costumam vir 1-based).
 */
function srcPageNumber(el) {
  const h = hostOf(el);
  if (!h) return null;

  const raw0 = h.dataset?.srcPage ?? h.getAttribute?.('data-src-page');
  const n0 = parseInt(raw0, 10);
  if (Number.isFinite(n0) && n0 >= 0) return n0 + 1; // zero-based -> 1-based

  const raw1 = h.dataset?.page ?? h.getAttribute?.('data-page')
           ?? h.dataset?.pageId ?? h.getAttribute?.('data-page-id');
  const n1 = parseInt(raw1, 10);
  if (Number.isFinite(n1) && n1 > 0) return n1;

  // último recurso: posição visual
  const i = indexOf(h);
  return i >= 0 ? i + 1 : null;
}

// ID estável para persistência/seleção (string), derivado do número original
function ensureStableId(el) {
  const h = hostOf(el);
  if (!h) return null;
  let sid = h.getAttribute('data-stable-id');
  if (!sid) {
    const src = srcPageNumber(h);
    sid = (src != null) ? String(src) : `u${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
    h.setAttribute('data-stable-id', sid);
  }
  return sid;
}
function stableIdOf(el) {
  return hostOf(el)?.getAttribute?.('data-stable-id') || ensureStableId(el);
}

/* -------------------- UI do marcador ✔ -------------------- */
function ensureSelectionUI(card) {
  if (!card) return;
  const selected = card.classList.contains('is-selected')
                || card.getAttribute('aria-selected') === 'true'
                || card.dataset?.selected === 'true';
  const existing = card.querySelector('.select-check');

  if (selected && !existing) {
    const check = document.createElement('div');
    check.className = 'select-check';
    check.textContent = '✔';
    card.appendChild(check);
  } else if (!selected && existing) {
    existing.remove();
  }
}

/* -------------------- seleção (com persistência estável) ------------- */
const SelectionStore = (() => {
  let key = 'gv_split_sel_default';
  let set = new Set();
  function load(){ try{ set = new Set(JSON.parse(sessionStorage.getItem(key)||'[]')); }catch{ set=new Set(); } }
  function save(){ try{ sessionStorage.setItem(key, JSON.stringify([...set])); }catch{} }
  function setKey(k){ if (k && k!==key){ key=k; load(); } }
  function add(id){ if (id == null) return; set.add(String(id)); save(); }
  function del(id){ if (id == null) return; set.delete(String(id)); save(); }
  function clear(){ set.clear(); save(); }
  function all(){ return [...set]; }
  load();
  return { setKey, add, del, clear, all };
})();

function setSelected(el, on) {
  const h = hostOf(el); if (!h) return;
  const id = stableIdOf(h); // estável
  const flag = !!on;
  h.classList.toggle('is-selected', flag);
  h.setAttribute('aria-selected', flag ? 'true' : 'false');
  h.dataset.selected = flag ? 'true' : 'false';
  ensureSelectionUI(h);
  if (flag) SelectionStore.add(id); else SelectionStore.del(id);
}
function isSelected(el) {
  const h = hostOf(el);
  return h?.classList.contains('is-selected')
      || h?.getAttribute('aria-selected') === 'true'
      || h?.dataset?.selected === 'true';
}
function clearAllSelection() {
  const r = root(); if (!r) return;
  r.querySelectorAll(`${ITEM_SELECTOR}.is-selected, ${ITEM_SELECTOR}[aria-selected="true"]`)
    .forEach(el => {
      el.classList.remove('is-selected');
      el.setAttribute('aria-selected','false');
      if (el.dataset) el.dataset.selected = 'false';
      el.querySelector('.select-check')?.remove();
    });
  SelectionStore.clear();
}
function reapplySelection() {
  const wanted = new Set(SelectionStore.all().map(String));
  cards().forEach(el => {
    ensureStableId(el);
    const on = wanted.has(String(stableIdOf(el)));
    el.classList.toggle('is-selected', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
    if (el.dataset) el.dataset.selected = on ? 'true' : 'false';
    ensureSelectionUI(el);
  });
}

/* -------------------- controles por card (X/↻, sem duplicar) -------- */
function ensureControls(card){
  if(!card || card.__controlsEnsured) return;

  ensureStableId(card);

  const bars = card.querySelectorAll(':scope > .file-controls, :scope > .thumb-actions');
  if(bars.length>1){ for(let i=1;i<bars.length;i++) bars[i].remove(); }
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
      const b=document.createElement('button'); b.className='remove-file'; b.type='button'; b.title='Remover página';
      b.setAttribute('data-no-drag','true'); b.setAttribute('aria-label','Remover página'); b.textContent='×'; bar.prepend(b);
    }
    if(!bar.querySelector('.rotate-page,[data-action="rot-right"],[data-action="rotate-right"]')){
      const b=document.createElement('button'); b.className='rotate-page'; b.type='button'; b.title='Girar 90°';
      b.setAttribute('data-no-drag','true'); b.setAttribute('aria-label','Girar 90°'); b.textContent='↻'; bar.append(b);
    }
  }
  card.__controlsEnsured = true;
}

/* -------------------- rotação (usa .thumb-frame do preview.js) ------ */
function frameOf(card){
  return card?.querySelector?.(':scope > .thumb-media > .thumb-frame, :scope > .thumb-frame');
}
function applyPreviewRotation(card, angle){
  const frame = frameOf(card); if (!frame) return;
  frame.style.transform = `rotate(${normalizeAngle(angle)}deg)`;
}
function rotateThumb(card, delta){
  let a = parseInt(card.getAttribute('data-rotation') || '0', 10);
  if (!Number.isFinite(a)) a = 0;
  a = normalizeAngle(a + delta);
  card.setAttribute('data-rotation', String(a));
  applyPreviewRotation(card, a);
  setSelected(card, true); // ao rotacionar, considera selecionada
}

/* -------------------- crop (sobrepõe dentro do frame) --------------- */
function clearCrop(thumb) {
  delete thumb.dataset.cropX; delete thumb.dataset.cropY;
  delete thumb.dataset.cropW; delete thumb.dataset.cropH;
  thumb.querySelector('.gv-crop-overlay')?.remove();
  thumb.querySelector('.gv-crop-badge')?.remove();
}
function startCropOnThumb(thumb) {
  if (thumb.dataset.cropW && thumb.dataset.cropH) { clearCrop(thumb); return; }
  const frame = frameOf(thumb);
  if (!frame) return msg('Não foi possível iniciar o recorte (prévia indisponível).', 'erro');

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

  const content = frame.querySelector('canvas,img');
  if (!content) return;

  const contentBox = content.getBoundingClientRect();
  const overlayBox = overlay.getBoundingClientRect();

  let dragging = false;
  let startX = 0, startY = 0;

  function clamp(val, min, max){ return Math.max(min, Math.min(max, val)); }

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
  }

  overlay.addEventListener('mousedown', onDown, { passive:false });
  overlay.addEventListener('mousemove', onMove, { passive:false });
  window.addEventListener('mouseup', onUp, { passive:true, once:true });

  overlay.addEventListener('touchstart', onDown, { passive:false });
  overlay.addEventListener('touchmove', onMove, { passive:false });
  window.addEventListener('touchend', onUp, { passive:true, once:true });
}

/* -------------------- coleta/POST -------------------- */
// Respeita a ORDEM VISUAL (DOM) do grid, envia NÚMEROS ORIGINAIS 1-based e remove duplicatas.
function collectSelectedPagesInDisplayOrder() {
  const order = cards();
  const wanted = new Set(SelectionStore.all().map(String));
  const selectedDom = !wanted.size
    ? new Set(order.filter(isSelected).map(stableIdOf))
    : null;

  const isMarked = (el) => selectedDom ? selectedDom.has(stableIdOf(el)) : wanted.has(stableIdOf(el));

  const out = [];
  const seen = new Set();
  for (const el of order) {
    if (!isMarked(el)) continue;
    const p = srcPageNumber(el);
    if (!Number.isFinite(p) || p <= 0) continue;
    if (seen.has(p)) continue; // dedupe
    seen.add(p);
    out.push(p);
  }
  return out;
}
function collectRotationsMap() {
  const r = root(); if (!r) return null;
  const items = r.querySelectorAll(`${ITEM_SELECTOR}[data-rotation]`);
  if (!items.length) return null;
  const map = {};
  items.forEach(el => {
    const p = srcPageNumber(el);
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
    const p = srcPageNumber(el);
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

  // ---- CSRF fix + cookies da sessão
  const headers = new Headers();
  const csrf = getCSRFToken();
  if (csrf) headers.set('X-CSRFToken', csrf);
  headers.set('X-Requested-With', 'XMLHttpRequest');
  headers.set('Accept', 'application/pdf, application/zip, application/octet-stream');

  const res = await fetch('/api/split', {
    method: 'POST',
    headers,
    body: fd,
    credentials: 'same-origin',   // <-- garante envio do cookie de sessão
    cache: 'no-store',
    redirect: 'follow',
  });

  if (!res.ok) {
    // Mensagem mais clara quando o Render retorna nossa página de erro CSRF (HTML)
    const ct = res.headers.get('Content-Type') || '';
    const txt = await res.text().catch(()=> '');
    if (res.status === 400 && /text\/html/i.test(ct) && /Falha de Verifica/i.test(txt)) {
      throw new Error('Falha de Verificação (CSRF). Atualize a página e tente novamente.');
    }
    throw new Error(`Split falhou: HTTP ${res.status} ${txt.slice(0, 140)}`);
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

/* -------------------- BLOQUEIOS + seleção global -------------------- */
function disableEditorTriggers() {
  if (document.__splitNoEditor) return;
  document.__splitNoEditor = true;
  const r = root(); if (!r) return;

  document.addEventListener('dblclick', (ev) => {
    const within = r.contains(ev.target) && !!ev.target.closest(ITEM_SELECTOR);
    if (within) { ev.stopImmediatePropagation(); ev.preventDefault(); }
  }, true);
}

let __lastPointerToggleAt = 0;

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

  // Toggle via pointer (evita arrasto)
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
      __lastPointerToggleAt = (performance.now ? performance.now() : Date.now());
    }
    down = null;
  }, true);

  // Fallback por clique direto no grid (garante desmarcar sempre)
  document.addEventListener('click', (ev) => {
    const r = root(); if (!r) return;
    if (!r.contains(ev.target)) return;
    if (isInteractive(ev.target)) return;

    const card = getCard(ev.target);
    if (!card) return;

    const now = (performance.now ? performance.now() : Date.now());
    if (now - __lastPointerToggleAt < 120) return; // evita toggle duplo (pointerup + click)

    ev.preventDefault();
    setSelected(card, !isSelected(card));
  }, true);

  // Teclado (sem deleção automática para evitar “sumir” páginas sem querer)
  document.addEventListener('keydown', (ev) => {
    const r = root(); if (!r) return;
    const active = document.activeElement?.closest?.(ITEM_SELECTOR);
    if (!active || !r.contains(active)) return;

    if (ev.key === ' ' || ev.key === 'Enter') {
      ev.preventDefault();
      setSelected(active, !isSelected(active));
      ev.stopImmediatePropagation();
      return;
    }
    if (ev.key === 'Escape') {
      clearAllSelection();
      ev.stopImmediatePropagation();
      return;
    }
  }, true);
}
function bindCards() {
  cards().forEach(el => {
    if (!el.hasAttribute('tabindex')) el.setAttribute('tabindex', '0');
    ensureStableId(el);
  });
}

/* -------------------- init -------------------- */
function computePersistKey() {
  const r = root();
  const idx = r?.getAttribute('data-file-index') || r?.getAttribute('data-index') || '';
  const name = (document.querySelector('.file-name')?.textContent || '').trim();
  return `gv_split_sel_${idx || name || 'default'}`;
}
function disableDropOverlays() {
  document.querySelectorAll('.drop-overlay,.drop-hint').forEach(el => { el.style.pointerEvents = 'none'; });
}
let __prevCardCount = 0;

function clearEverything() {
  const r = root(); if (!r) return;
  const ev = new CustomEvent('split:clearAll', { bubbles: true, cancelable: true });
  r.dispatchEvent(ev);
  if (!ev.defaultPrevented) {
    r.querySelectorAll(ITEM_SELECTOR).forEach(c => {
      setSelected(c, false);
      c.removeAttribute('data-rotation');
      const f = frameOf(c); if (f) f.style.transform = '';
      clearCrop(c);
    });
    SelectionStore.clear();
    const inputEl = document.getElementById(`input-${PREFIX}`);
    if (inputEl) inputEl.value = '';
    currentFile = null;
    enableActions(false);
  }
}

function initOnce() {
  SelectionStore.setKey(computePersistKey());

  disableEditorTriggers();
  bindSelectionGlobal();
  bindCards();
  disableDropOverlays();
  cards().forEach(ensureControls);
  cards().forEach(c => {
    const ang = parseInt(c.getAttribute('data-rotation') || '0', 10) || 0;
    applyPreviewRotation(c, ang);
  });

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

  const btnClear = document.getElementById('btn-clear-all');
  if (btnClear) btnClear.addEventListener('click', clearEverything);

  // delegação para botões do card (remove/rotate/crop) – somente controles
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest?.('[data-action],button.rotate-page,.remove-file');
    if (!btn) return;
    const r = root(); if (!r || !r.contains(btn)) return;

    const card = hostOf(btn.closest(ITEM_SELECTOR)); if (!card) return;
    const act = (btn.dataset.action || '').toLowerCase();

    // Remover página (X)
    const isRemove = btn.matches('.remove-file,[data-action="remove"],[data-action="delete"],[data-action="close"]')
                  || act === 'remove' || act === 'delete' || act === 'close';
    if (isRemove) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      SelectionStore.del(stableIdOf(card));
      card.dispatchEvent(new CustomEvent('split:removePage', {
        bubbles: true,
        detail: { page: srcPageNumber(card), id: stableIdOf(card) }
      }));
      card.remove();
      return;
    }

    // Rotacionar
    const isRotate = btn.matches('button.rotate-page')
                  || act === 'rot-left'  || act === 'rotate-left'  || act === 'rotate-l'
                  || act === 'rot-right' || act === 'rotate-right' || act === 'rotate-r';
    if (isRotate) {
      ev.preventDefault(); ev.stopImmediatePropagation();
      rotateThumb(card, (act.includes('left') || act.endsWith('-l')) ? -90 : +90);
      return;
    }

    // Crop
    if (act === 'crop' || act === 'cut')        { ev.preventDefault(); startCropOnThumb(card); return; }
    if (act === 'crop-clear' || act === 'uncrop'){ ev.preventDefault(); clearCrop(card); return; }
  }, true);
}

document.addEventListener('DOMContentLoaded', initOnce);

// Observa thumbs adicionadas dinamicamente
new MutationObserver((muts)=>{
  let changed = false;
  for (const m of muts) {
    m.addedNodes?.forEach(n=>{
      if(n.nodeType===1){
        if(n.matches?.(ITEM_SELECTOR)){
          ensureStableId(n);
          changed = true; ensureControls(n);
          const ang = parseInt(n.getAttribute('data-rotation') || '0', 10) || 0;
          applyPreviewRotation(n, ang);
        }
        n.querySelectorAll?.(ITEM_SELECTOR).forEach(el=>{
          ensureStableId(el);
          changed = true; ensureControls(el);
          const ang = parseInt(el.getAttribute('data-rotation') || '0', 10) || 0;
          applyPreviewRotation(el, ang);
        });
      }
    });
  }
  const count = cards().length;
  if (changed || count !== __prevCardCount) { bindCards(); reapplySelection(); __prevCardCount = count; }
}).observe(document.body, { childList: true, subtree: true });