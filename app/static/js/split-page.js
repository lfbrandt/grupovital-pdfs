// app/static/js/split-page.js
// Página "Dividir PDF": DnD + ações + cropper simples (seguro pra CSP/SES).

import { splitPages } from './api.js';

const PREFIX = 'split';
const LIST_SELECTOR = '#preview-' + PREFIX;
const ITEM_SELECTOR = '.page-thumb';
const CONTENT_SELECTOR = 'img,canvas,.thumb-canvas,.thumb-image';

let currentFile = null;

function msg(text, tipo) {
  try { if (typeof window.mostrarMensagem === 'function') return window.mostrarMensagem(text, tipo); } catch(_){}
  if (tipo === 'erro') console.error(text); else console.log(text);
}
function enableActions(enabled) {
  const btnSplit = document.getElementById(`btn-${PREFIX}`);
  const btnAll   = document.getElementById('btn-split-all');
  if (btnSplit) btnSplit.disabled = !enabled;
  if (btnAll)   btnAll.disabled   = !enabled;
}
function getNonce() {
  const meta = document.querySelector('meta[name="csp-nonce"]');
  if (meta?.content) return meta.content;
  const s = document.querySelector('script[nonce]');
  return s?.nonce || s?.getAttribute?.('nonce') || '';
}
function ensureStyles() {
  if (document.getElementById('gv-split-style')) return;
  const styleEl = document.createElement('style');
  styleEl.id = 'gv-split-style';
  const nonce = getNonce();
  if (nonce) styleEl.setAttribute('nonce', nonce);
  styleEl.textContent = `
    .gv-dnd{ user-select:none; cursor:grab; }
    .gv-is-dragging{ opacity:.9; }
    .gv-pointer-none{ pointer-events:none; }
    .gv-crop-overlay{ position:absolute; inset:0; cursor:crosshair; }
    .gv-crop-rect{ position:absolute; border:2px dashed currentColor; color:#09a36b;
      background-color:rgba(0,0,0,.08); box-sizing:border-box; pointer-events:none; }
    .gv-thumb-wrap{ position:relative; }
    .gv-crop-badge{ position:absolute; top:.35rem; left:.35rem; background:#09a36b; color:#fff;
      font-size:.72rem; padding:.15rem .35rem; border-radius:.4rem; z-index:3; }
  `;
  document.head.appendChild(styleEl);
}

function markDraggable(list) {
  list.querySelectorAll(ITEM_SELECTOR).forEach(el => {
    if (!el.hasAttribute('draggable')) el.setAttribute('draggable', 'true');
    el.classList.add('gv-dnd','gv-thumb-wrap');
  });
}
function saveOrder(list) {
  const order = [...list.querySelectorAll(ITEM_SELECTOR)].map(el => (
    el.dataset.pageId || el.getAttribute('data-page') || ''
  ));
  list.dataset.order = order.join(',');
}
function bindDnd(list) {
  if (!list || list.__dndBound) return;
  list.__dndBound = true;
  let dragged = null;

  list.addEventListener('dragstart', e => {
    const item = e.target.closest(ITEM_SELECTOR);
    if (!item) return;
    dragged = item;
    e.dataTransfer.effectAllowed = 'move';
    try { e.dataTransfer.setData('text/plain', ''); } catch {}
    item.classList.add('gv-is-dragging');
    item.setAttribute('aria-grabbed', 'true');
  });
  list.addEventListener('dragend', () => {
    if (!dragged) return;
    dragged.classList.remove('gv-is-dragging');
    dragged.removeAttribute('aria-grabbed');
    saveOrder(list);
    dragged = null;
  });
  list.addEventListener('dragover', e => {
    if (!dragged) return;
    e.preventDefault();
    const target = e.target.closest(ITEM_SELECTOR);
    if (!target || target === dragged) return;
    const rect = target.getBoundingClientRect();
    const before = (e.clientY - rect.top) < rect.height / 2;
    list.insertBefore(dragged, before ? target : target.nextSibling);
  });
  list.addEventListener('drop', e => e.preventDefault());
}

function pageNumberFromEl(el) {
  const v = el?.dataset?.pageId ?? el?.getAttribute?.('data-page');
  const n = parseInt(v, 10);
  return Number.isFinite(n) ? n : null;
}
function isSelected(el) {
  const cb = el.querySelector?.('input[name="page-select"]');
  if (cb && cb.checked) return true;
  if (el.getAttribute('data-selected') === 'true') return true;
  if (el.getAttribute('aria-pressed') === 'true') return true;
  if (el.classList.contains('is-selected')) return true;
  return false;
}
function collectSelectedPagesInDisplayOrder(list) {
  const items = [...list.querySelectorAll(ITEM_SELECTOR)];
  const pages = [];
  for (const el of items) {
    if (!isSelected(el)) continue;
    const p = pageNumberFromEl(el);
    if (p) pages.push(p);
  }
  return pages;
}
function collectRotationsMap(list) {
  const items = list?.querySelectorAll?.(`${ITEM_SELECTOR}[data-rotation]`) || [];
  if (!items.length) return null;
  const map = {};
  items.forEach(el => {
    const p = pageNumberFromEl(el);
    const a = parseInt(el.getAttribute('data-rotation'), 10);
    if (p && Number.isFinite(a)) map[p] = a;
  });
  return Object.keys(map).length ? map : null;
}
function collectCropsMap(list) {
  const items = list?.querySelectorAll?.(ITEM_SELECTOR) || [];
  const mods = {};
  items.forEach(el => {
    const p = pageNumberFromEl(el);
    if (!p) return;
    const x = Number(el.dataset.cropX);
    const y = Number(el.dataset.cropY);
    const w = Number(el.dataset.cropW);
    const h = Number(el.dataset.cropH);
    if (Number.isFinite(x) && Number.isFinite(y) && Number.isFinite(w) && Number.isFinite(h) && w>0 && h>0) {
      mods[p] = { crop: { unit:'percent', origin:'topleft', x, y, w, h } };
    }
  });
  return Object.keys(mods).length ? mods : null;
}
function collectPagesWithMods(list) {
  const items = list?.querySelectorAll?.(ITEM_SELECTOR) || [];
  const pages = new Set();
  items.forEach(el => {
    const p = pageNumberFromEl(el);
    if (!p) return;
    const hasCrop = el.dataset.cropX && el.dataset.cropY && el.dataset.cropW && el.dataset.cropH;
    const hasRot  = el.hasAttribute('data-rotation');
    if (hasCrop || hasRot) pages.add(p);
  });
  return [...pages];
}

// Cropper
function startCropOnThumb(thumb) {
  const content = thumb.querySelector(CONTENT_SELECTOR);
  if (!content) return msg('Não foi possível iniciar o recorte (prévia indisponível).', 'erro');

  let overlay = thumb.querySelector('.gv-crop-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.className = 'gv-crop-overlay';
    overlay.setAttribute('aria-label', 'Seleção de recorte');
    overlay.setAttribute('role', 'region');
    thumb.appendChild(overlay);
  }
  let rectEl = thumb.querySelector('.gv-crop-rect');
  if (!rectEl) {
    rectEl = document.createElement('div');
    rectEl.className = 'gv-crop-rect';
    overlay.appendChild(rectEl);
  }
  let badge = thumb.querySelector('.gv-crop-badge');
  if (!badge) {
    badge = document.createElement('div');
    badge.className = 'gv-crop-badge';
    badge.textContent = 'Recorte';
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
    const nw = sel.width  / contentBox.width;
    const nh = sel.height / contentBox.height;
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
  }
  function onUp() {
    if (!dragging) return;
    dragging = false;
    const r = rectEl.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) {
      rectEl.remove();
      delete thumb.dataset.cropX;
      delete thumb.dataset.cropY;
      delete thumb.dataset.cropW;
      delete thumb.dataset.cropH;
      if (badge) badge.remove();
      return;
    }
    const perc = toPercentBox(r);
    thumb.dataset.cropX = String(perc.x);
    thumb.dataset.cropY = String(perc.y);
    thumb.dataset.cropW = String(perc.w);
    thumb.dataset.cropH = String(perc.h);

    // Auto-seleciona
    thumb.dataset.selected = 'true';
    thumb.classList.add('is-selected');
    const cb = thumb.querySelector('input[name="page-select"]');
    if (cb) cb.checked = true;
  }

  overlay.addEventListener('mousedown', onDown, { passive:false });
  overlay.addEventListener('mousemove', onMove, { passive:false });
  window.addEventListener('mouseup', onUp, { passive:true, once:true });

  overlay.addEventListener('touchstart', onDown, { passive:false });
  overlay.addEventListener('touchmove', onMove, { passive:false });
  window.addEventListener('touchend', onUp, { passive:true, once:true });
}

async function safeSplitPages(file, pages, rotations, outName, options) {
  try {
    enableActions(false);
    const pagesArg = (Array.isArray(pages) && pages.length) ? pages : undefined;
    const rotationsArg = (rotations && Object.keys(rotations).length) ? rotations : undefined;
    await splitPages(file, pagesArg, rotationsArg, outName, options);
  } catch (err) {
    if (err === null || err === undefined) msg('Operação cancelada.', 'erro');
    else if (err?.message) msg(err.message, 'erro');
    else msg('Falha ao dividir o PDF.', 'erro');
  } finally {
    enableActions(true);
  }
}

function initOnce() {
  ensureStyles();

  document.querySelectorAll('.drop-overlay,.drop-hint').forEach(el => {
    el.classList.add('gv-pointer-none');
  });

  const list = document.querySelector(LIST_SELECTOR);
  if (list) { markDraggable(list); bindDnd(list); }

  const inputEl = document.getElementById(`input-${PREFIX}`);
  if (inputEl) {
    inputEl.addEventListener('change', () => {
      currentFile = inputEl.files?.[0] || null;
      enableActions(!!currentFile);
    });
    if (inputEl.files?.[0]) currentFile = inputEl.files[0];
  }

  document.addEventListener('gv:file-dropped', (ev) => {
    const { prefix, file } = ev.detail || {};
    if (prefix === PREFIX && file instanceof File) {
      currentFile = file; enableActions(true);
    }
  });

  const btnSplit = document.getElementById(`btn-${PREFIX}`);
  if (btnSplit) {
    btnSplit.addEventListener('click', () => {
      if (!currentFile) return msg('Selecione um PDF para dividir.', 'erro');
      const list = document.querySelector(LIST_SELECTOR);
      if (!list)  return msg('Pré-visualização não carregada.', 'erro');

      let pages = collectSelectedPagesInDisplayOrder(list);
      if (!pages.length) {
        pages = collectPagesWithMods(list);
        if (!pages.length) return msg('Marque ao menos uma página ou use "Separar todas as páginas".', 'erro');
      }
      const rotations = collectRotationsMap(list);
      const mods = collectCropsMap(list);

      safeSplitPages(currentFile, pages, rotations, 'paginas_selecionadas.pdf', { modificacoes: mods });
    });
  }

  const btnAll = document.getElementById('btn-split-all');
  if (btnAll) {
    btnAll.addEventListener('click', () => {
      if (!currentFile) return msg('Selecione um PDF para dividir.', 'erro');
      const list = document.querySelector(LIST_SELECTOR);
      const rotations = list ? collectRotationsMap(list) : null;
      const mods = list ? collectCropsMap(list) : null;

      safeSplitPages(currentFile, undefined, rotations, 'paginas_divididas.zip', { modificacoes: mods });
    });
  }

  // Botão Recortar
  document.addEventListener('click', (ev) => {
    const cropBtn = ev.target.closest('[data-action="crop"], .btn-crop');
    if (!cropBtn) return;
    ev.preventDefault(); ev.stopPropagation();
    const thumb = cropBtn.closest(ITEM_SELECTOR);
    if (!thumb) return;
    try { startCropOnThumb(thumb); }
    catch { msg('Falha ao iniciar o recorte.', 'erro'); }
  });

  enableActions(!!currentFile);
}

document.addEventListener('DOMContentLoaded', initOnce);

const mo = new MutationObserver(() => {
  const list = document.querySelector(LIST_SELECTOR);
  if (list) { markDraggable(list); bindDnd(list); }
});
mo.observe(document.documentElement, { childList: true, subtree: true });