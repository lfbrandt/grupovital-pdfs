// app/static/js/preview.js
// Preview leve (thumb) + preview avançado com pdf.js (seleção, rotação, reordenar)
// Abre o wizard (page-editor) ao dar duplo-clique/✎ e recarrega o PDF da sessão ao salvar.

import * as PageEditorMod from './page-editor.js';
import { getCSRFToken, xhrRequest } from './utils.js';

/* ------------------------------------------------------------------
   page-editor (import resiliente)
------------------------------------------------------------------- */
const openPageEditorFn =
  (PageEditorMod && (PageEditorMod.openPageEditor || PageEditorMod.default))
    ? (PageEditorMod.openPageEditor || PageEditorMod.default)
    : null;

/* ------------------------------------------------------------------
   Helpers básicos
------------------------------------------------------------------- */
export function isPdfFile(file) {
  const name = (file?.name || '').toLowerCase();
  const type = (file?.type || '').toLowerCase();
  return name.endsWith('.pdf') || type === 'application/pdf';
}

function broadcast(name, detail) {
  try { document.dispatchEvent(new CustomEvent(name, { detail })); }
  catch { /* ignore */ }
}

function clearChildren(node) {
  if (!node) return;
  while (node.firstChild) node.removeChild(node.firstChild);
}

function addCacheBust(url, seed) {
  try {
    const u = new URL(url, window.location.origin);
    u.searchParams.set('v', String(seed ?? Date.now()));
    return u.pathname + u.search + u.hash;
  } catch {
    return url + (url.includes('?') ? '&' : '?') + 'v=' + (seed ?? Date.now());
  }
}

async function postJSON(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCSRFToken(),
      'Accept': 'application/json'
    },
    credentials: 'same-origin',
    cache: 'no-store',
    body: JSON.stringify(body || {})
  }).then(r => r.json().catch(()=>({})));
}

/* ==================================================================
   Estado global de render — impede concorrência entre refreshes
================================================================== */
const ACTIVE = {
  doc: null,
  gen: 0,
  tasks: new Map(),   // pageNum -> renderTask
  container: null,
  sessionId: null,
  lastUrl: null,
};

function cancelPageTask(pageNum) {
  const t = ACTIVE.tasks.get(pageNum);
  if (t && typeof t.cancel === 'function') {
    try { t.cancel(); } catch {}
  }
  ACTIVE.tasks.delete(pageNum);
}
function cancelAll() {
  for (const [n, t] of ACTIVE.tasks) {
    try { t.cancel(); } catch {}
    ACTIVE.tasks.delete(n);
  }
  if (ACTIVE.doc) { try { ACTIVE.doc.destroy(); } catch {} }
  ACTIVE.doc = null;
}

/* ------------------------------------------------------------------
   Preview leve via servidor (miniatura da 1ª página)
------------------------------------------------------------------- */
export async function previewThumb(file, imgEl, { silentNonPdf = true } = {}) {
  if (!file || !imgEl) return;

  if (!isPdfFile(file)) {
    if (silentNonPdf) {
      delete imgEl.dataset.thumbId;
      imgEl.removeAttribute('src');
      imgEl.alt = 'Sem miniatura';
      imgEl.setAttribute('draggable', 'false');
    }
    return;
  }

  try {
    const form = new FormData();
    form.append('file', file);
    const headers = { 'X-CSRFToken': getCSRFToken() };

    const resp = await xhrRequest('/api/preview', { method: 'POST', body: form, headers });
    if (!resp || !resp.thumb_url) throw new Error('Resposta inválida da API de preview.');

    imgEl.alt = `Miniatura de ${file.name}`;
    imgEl.src = resp.thumb_url;
    imgEl.dataset.thumbId = resp.thumb_id;
    imgEl.setAttribute('draggable', 'false');
  } catch (err) {
    console.error('[previewThumb] erro gerando miniatura', err);
    delete imgEl.dataset.thumbId;
    imgEl.removeAttribute('src');
    imgEl.alt = 'Sem miniatura';
    imgEl.setAttribute('draggable', 'false');
  }
}

/* ==================================================================
   Preview avançado (pdf.js)
================================================================== */

const DEFAULT_THUMB_W = 160;
const INITIAL_BATCH    = 8;

/* ===== Sessão: utilidades ===== */
function extractSessionIdFromUrl(url) {
  if (typeof url !== 'string') return null;
  const m = url.match(/\/api\/edit\/file\/([A-Za-z0-9_-]+)(?=[/?#]|$)/);
  return m ? m[1] : null;
}
function setContainerSessionId(containerEl, sid) {
  if (!containerEl || !sid) return;
  containerEl.dataset.sessionId = sid;
  ACTIVE.sessionId = sid;
  broadcast('gv:session:set', { session_id: sid });
}
/** API pública para definir a sessão manualmente (ex.: após o upload). */
export function setPreviewSessionId(sessionId, containerSel = '#preview-edit') {
  const root = typeof containerSel === 'string' ? document.querySelector(containerSel) : containerSel;
  if (!root || !sessionId) return;
  setContainerSessionId(root, sessionId);
}

/** Fecha e limpa a sessão atual (servidor + client). */
async function closeSession(containerEl) {
  const sid = containerEl?.dataset?.sessionId || ACTIVE.sessionId || extractSessionIdFromUrl(ACTIVE.lastUrl || '');
  if (sid) {
    try { await postJSON('/api/edit/close', { session_id: sid }); }
    catch (e) { console.debug('[preview] closeSession falhou/ignorado', e); }
  }
  ACTIVE.sessionId = null;
  ACTIVE.lastUrl = null;
  if (containerEl) delete containerEl.dataset.sessionId;
  broadcast('gv:preview:empty', { session_id: sid || null });
}

/* ===== Seleção de páginas ===== */
export function initPageSelection(containerEl) {
  containerEl.selectedPages = new Set();
}
export function getSelectedPages(containerEl, keepOrder = false) {
  if (!containerEl?.selectedPages) return [];
  const pages = Array.from(containerEl.selectedPages);
  if (!keepOrder) return pages.sort((a, b) => a - b);
  const order = Array.from(containerEl.querySelectorAll('.page-wrapper'))
    .map(w => Number(w.dataset.page));
  return pages.sort((a, b) => order.indexOf(a) - order.indexOf(b));
}

/* ===== Toolbar (resultado) ===== */
function addResultToolbar(containerEl, setBtnDisabled) {
  const toolbar = document.createElement('div');
  toolbar.classList.add('result-toolbar');
  toolbar.setAttribute('data-no-drag', '');

  const btnSelectAll = document.createElement('button');
  btnSelectAll.type = 'button';
  btnSelectAll.textContent = 'Selecionar todas';
  btnSelectAll.setAttribute('data-no-drag', '');
  btnSelectAll.addEventListener('click', () => {
    containerEl.selectedPages = new Set(
      [...containerEl.querySelectorAll('.page-wrapper')].map(w => Number(w.dataset.page))
    );
    containerEl.querySelectorAll('.page-wrapper').forEach(w => w.setAttribute('aria-selected', 'true'));
    setBtnDisabled(false);
    broadcast('gv:preview:pageSelect', { selected: getSelectedPages(containerEl) });
  });

  const btnClearSel = document.createElement('button');
  btnClearSel.type = 'button';
  btnClearSel.textContent = 'Limpar seleção';
  btnClearSel.setAttribute('data-no-drag', '');
  btnClearSel.addEventListener('click', () => {
    containerEl.selectedPages = new Set();
    containerEl.querySelectorAll('.page-wrapper').forEach(w => w.setAttribute('aria-selected', 'false'));
    setBtnDisabled(true);
    broadcast('gv:preview:pageSelect', { selected: [] });
  });

  toolbar.append(btnSelectAll, btnClearSel);
  containerEl.before(toolbar);
}

/* ===== CSS helper ===== */
function cssThumbWidth(containerEl) {
  const v = getComputedStyle(containerEl).getPropertyValue('--thumb-w').trim();
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_THUMB_W;
}
function getOrder(containerEl) {
  return Array.from(containerEl.querySelectorAll('.page-wrapper')).map(w => Number(w.dataset.page));
}

/* ===== Badges de posição ===== */
function updateOrderBadges(containerEl) {
  const items = Array.from(containerEl.querySelectorAll('.page-wrapper'));
  items.forEach((w, idx) => {
    let ob = w.querySelector('.order-badge');
    if (!ob) {
      ob = document.createElement('div');
      ob.className = 'order-badge';
      ob.setAttribute('data-no-drag', '');
      w.appendChild(ob);
    }
    ob.textContent = `#${idx + 1}`;
  });
}

/* ===== Drag & Drop sorting ===== */
function initGridReorder(containerEl) {
  let draggingEl = null;

  const setDraggable = (wrap) => {
    wrap.setAttribute('draggable', 'true');

    wrap.addEventListener('dragstart', (e) => {
      if (e.target.closest('[data-no-drag]')) { e.preventDefault(); return; }
      draggingEl = wrap;
      wrap.classList.add('is-dragging');
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', wrap.dataset.page || '');
      try {
        const cnv = wrap.querySelector('canvas');
        if (cnv && cnv.width && cnv.height) {
          const ghost = document.createElement('canvas');
          const gctx = ghost.getContext('2d');
          const scale = 0.35;
          ghost.width  = Math.max(32, Math.floor(cnv.width * scale));
          ghost.height = Math.max(32, Math.floor(cnv.height * scale));
          gctx.drawImage(cnv, 0, 0, ghost.width, ghost.height);
          e.dataTransfer.setDragImage(ghost, ghost.width / 2, ghost.height / 2);
        }
      } catch {}
    });

    wrap.addEventListener('dragend', () => {
      if (!draggingEl) return;
      draggingEl.classList.remove('is-dragging');
      draggingEl = null;
      containerEl.querySelectorAll('.is-drop-target').forEach(el => el.classList.remove('is-drop-target'));
      updateOrderBadges(containerEl);
      broadcast('gv:preview:reorder', { order: getOrder(containerEl) });
      if (!containerEl.querySelector('.page-wrapper')) {
        closeSession(containerEl);
      }
    });

    // Teclado: Shift+Setas para mover
    wrap.addEventListener('keydown', (ev) => {
      const k = ev.key;
      if (!ev.shiftKey) return;
      if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(k)) return;
      ev.preventDefault();

      const items = Array.from(containerEl.querySelectorAll('.page-wrapper'));
      const idx = items.indexOf(wrap);
      if (idx < 0) return;

      let targetIdx = idx;
      const cols = calcApproxCols(containerEl, wrap);

      if (k === 'Home') targetIdx = 0;
      else if (k === 'End') targetIdx = items.length - 1;
      else if (k === 'ArrowLeft')  targetIdx = Math.max(0, idx - 1);
      else if (k === 'ArrowRight') targetIdx = Math.min(items.length - 1, idx + 1);
      else if (k === 'ArrowUp')    targetIdx = Math.max(0, idx - cols);
      else if (k === 'ArrowDown')  targetIdx = Math.min(items.length - 1, idx + cols);

      if (targetIdx !== idx) {
        const ref = (targetIdx > idx) ? items[targetIdx].nextSibling : items[targetIdx];
        containerEl.insertBefore(wrap, ref);
        wrap.focus();
        updateOrderBadges(containerEl);
        broadcast('gv:preview:reorder', { order: getOrder(containerEl) });
      }
    });
  };

  containerEl.querySelectorAll('.page-wrapper').forEach(setDraggable);

  const mo = new MutationObserver((mutations) => {
    mutations.forEach(m => {
      m.addedNodes.forEach(node => {
        if (node.nodeType === 1 && node.classList?.contains('page-wrapper')) setDraggable(node);
      });
    });
  });
  mo.observe(containerEl, { childList: true });

  containerEl.addEventListener('dragover', (e) => {
    if (!draggingEl) return;
    e.preventDefault();

    const target = e.target.closest('.page-wrapper');
    if (!target || target === draggingEl) return;

    containerEl.querySelectorAll('.is-drop-target').forEach(el => el.classList.remove('is-drop-target'));

    const rect = target.getBoundingClientRect();
    const before =
      e.clientY < rect.top + rect.height / 2 ||
      (Math.abs(e.clientY - (rect.top + rect.height / 2)) < rect.height * 0.2 &&
       e.clientX < rect.left + rect.width / 2);

    target.classList.add('is-drop-target');
    if (before) containerEl.insertBefore(draggingEl, target);
    else        containerEl.insertBefore(draggingEl, target.nextSibling);
  });

  containerEl.addEventListener('dragleave', (e) => {
    const t = e.target.closest?.('.page-wrapper');
    if (t) t.classList.remove('is-drop-target');
  });

  containerEl.addEventListener('drop', (e) => {
    const t = e.target.closest?.('.page-wrapper');
    if (t) t.classList.remove('is-drop-target');
    e.preventDefault();
    updateOrderBadges(containerEl);
  });

  function calcApproxCols(root, sample) {
    const w = (root.clientWidth || 1);
    const itemW = sample?.clientWidth || 1;
    const gap = parseInt(getComputedStyle(root).getPropertyValue('--thumb-gap') || '12', 10) || 12;
    return Math.max(1, Math.round(w / (itemW + gap)));
  }
}

/* ===== Re-render responsivo (resize/DPR) ===== */
function debounce(fn, wait = 150) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}
function rerenderVisible(containerEl) {
  if (!ACTIVE.doc || !containerEl) return;
  const wraps = Array.from(containerEl.querySelectorAll('.page-wrapper'));
  const vh = (window.innerHeight || 800) + 200; // margem
  wraps.forEach((wrap) => {
    const rect = wrap.getBoundingClientRect();
    if (rect.bottom >= -200 && rect.top <= vh) {
      const pg  = Number(wrap.dataset.page);
      const rot = parseInt(wrap.dataset.rotation || '0', 10);
      renderPage(ACTIVE.doc, pg, containerEl, rot, ACTIVE.gen).catch(()=>{});
    }
  });
}

/* ===== Render (seguro) ===== */
async function renderPage(pdf, pageNumber, containerEl, rotation = 0, genToken = ACTIVE.gen) {
  if (genToken !== ACTIVE.gen) return;

  const wrapSel = `.page-wrapper[data-page="${pageNumber}"]`;
  const wrap = containerEl.querySelector(wrapSel);
  if (!wrap) return;

  const oldCanvas = wrap.querySelector(`canvas[data-page="${pageNumber}"]`);
  const canvas = document.createElement('canvas');
  canvas.dataset.page = String(pageNumber);
  canvas.setAttribute('draggable', 'false');
  if (oldCanvas) oldCanvas.replaceWith(canvas);
  else wrap.appendChild(canvas);

  cancelPageTask(pageNumber);

  let page;
  try {
    page = await pdf.getPage(pageNumber);
  } catch (err) {
    console.warn('[preview] getPage falhou', err);
    return;
  }

  const dpr = window.devicePixelRatio || 1;

  // respeita rotação intrínseca + extra
  const intrinsic = ((Number(page.rotate) || 0) % 360 + 360) % 360;
  wrap.dataset.baseRotation = String(intrinsic);
  const extra  = ((Number(rotation) || 0) % 360 + 360) % 360;
  const effRot = (intrinsic + extra) % 360;

  const unrot = page.getViewport({ scale: 1, rotation: 0 });
  const baseViewport = page.getViewport({ scale: 1, rotation: effRot });
  const targetW = (wrap && wrap.clientWidth) ? wrap.clientWidth : cssThumbWidth(containerEl);
  const scale = (targetW * dpr) / baseViewport.width;
  const vp = page.getViewport({ scale, rotation: effRot });

  if (wrap && (!wrap.dataset.pdfW || !wrap.dataset.pdfH)) {
    wrap.dataset.pdfW = String(unrot.width);
    wrap.dataset.pdfH = String(unrot.height);
  }

  const cropData = (() => { try { return wrap?.dataset?.crop ? JSON.parse(wrap.dataset.crop) : null; } catch { return null; } })();

  const doRender = (canvasContext, viewport) => {
    const task = page.render({ canvasContext, viewport });
    ACTIVE.tasks.set(pageNumber, task);
    return task.promise.finally(() => {
      const current = ACTIVE.tasks.get(pageNumber);
      if (current === task) ACTIVE.tasks.delete(pageNumber);
      try { page.cleanup(); } catch {}
    });
  };

  try {
    if (!cropData) {
      canvas.width  = Math.floor(vp.width);
      canvas.height = Math.floor(vp.height);
      canvas.style.width = Math.round(targetW) + 'px';
      canvas.style.height = 'auto';
      const ctx = canvas.getContext('2d', { alpha: false });
      // ctx.imageSmoothingEnabled = true; // padrão já é true
      // ctx.imageSmoothingQuality = 'high'; // opcional
      await doRender(ctx, vp);
      return;
    }

    // se tiver crop salvo, renderiza full e recorta
    const full = document.createElement('canvas');
    full.width  = Math.floor(vp.width);
    full.height = Math.floor(vp.height);
    const fctx = full.getContext('2d', { alpha: false });
    await doRender(fctx, vp);

    const W = unrot.width, H = unrot.height;
    const { x0, y0, x1, y1 } = cropData;
    const rectPdf = [x0 * W, y0 * H, x1 * W, y1 * H];

    const [vx0, vy0, vx1, vy1] = vp.convertToViewportRectangle(rectPdf);
    const cx = Math.min(vx0, vx1);
    const cy = Math.min(vy0, vy1);
    const cw = Math.abs(vx1 - vx0);
    const ch = Math.abs(vy1 - vy0);

    const outW = Math.max(1, Math.floor(cw));
    const outH = Math.max(1, Math.floor(ch));

    canvas.width  = outW;
    canvas.height = outH;
    canvas.style.width  = Math.round(outW / dpr) + 'px';
    canvas.style.height = 'auto';

    const ctx2 = canvas.getContext('2d');
    ctx2.drawImage(full, cx, cy, cw, ch, 0, 0, canvas.width, canvas.height);
  } catch (err) {
    if (!String(err?.name).includes('RenderingCancelledException')) {
      console.warn('[preview] erro de render:', err);
    }
  }
}

/* ===== Remover página ===== */
function removePage(containerEl, pageNum, wrap) {
  if (!wrap) return;
  wrap.remove();
  containerEl.selectedPages?.delete(pageNum);
  updateOrderBadges(containerEl);
  broadcast('gv:preview:pageSelect', { selected: getSelectedPages(containerEl) });

  // Se ficar vazio, encerra a sessão no servidor (limpa diretório)
  if (!containerEl.querySelector('.page-wrapper')) {
    closeSession(containerEl);
  }
}

/* ===== Carregar PDF ===== */
async function loadPdfDoc(input) {
  if (typeof pdfjsLib === 'undefined' || !pdfjsLib?.getDocument) {
    throw new Error('pdf.js não carregado. Inclua o pdfjs-dist no template.');
  }
  if (typeof input === 'string') {
    const bust = addCacheBust(input);
    const res = await fetch(bust, { credentials: 'same-origin', cache: 'no-store' });
    const blob = await res.blob();
    const ab = await blob.arrayBuffer();
    return await pdfjsLib.getDocument(new Uint8Array(ab)).promise;
  }
  const blob = (input instanceof Blob) ? input : new Blob([input]);
  const ab = await blob.arrayBuffer();
  return await pdfjsLib.getDocument(new Uint8Array(ab)).promise;
}

/* ===== abre o wizard (texto/whiteout) a partir da página ===== */
async function openInlineEditor(wrap, pageNumber) {
  if (!openPageEditorFn) return;

  const pdf = ACTIVE.doc;
  if (!pdf) return;

  let page;
  try {
    page = await pdf.getPage(pageNumber);
  } catch (e) {
    console.error('[openInlineEditor] getPage falhou', e);
    return;
  }

  const baseVp = page.getViewport({ scale: 1, rotation: 0 });
  const pageW = baseVp.width;
  const pageH = baseVp.height;

  // rotação efetiva da view (base + extra da UI)
  const baseRot  = parseInt(wrap.dataset.baseRotation || '0', 10) || 0;
  const extraRot = parseInt(wrap.dataset.rotation     || '0', 10) || 0;
  const viewRotation = ((baseRot + extraRot) % 360 + 360) % 360;

  // escala inicial com DPR e rotação da view
  const initScale = Math.max(1, (window.devicePixelRatio || 1));
  const initVp = page.getViewport({ scale: initScale, rotation: viewRotation });

  const cnv = document.createElement('canvas');
  cnv.width  = Math.floor(initVp.width);
  cnv.height = Math.floor(initVp.height);
  const ctx  = cnv.getContext('2d', { alpha:false });
  await page.render({ canvasContext: ctx, viewport: initVp }).promise;

  const bitmap = await new Promise((resolve, reject) => {
    cnv.toBlob(b => b ? resolve(b) : reject(new Error('toBlob falhou')), 'image/png', 0.98);
  });

  // upgrade nitidez respeitando a mesma rotação
  const getBitmap = async (needScale) => {
    const scale = Math.max(initScale, needScale);
    const vp = page.getViewport({ scale, rotation: viewRotation });
    const c = document.createElement('canvas');
    c.width  = Math.floor(vp.width);
    c.height = Math.floor(vp.height);
    const cctx = c.getContext('2d', { alpha:false });
    await page.render({ canvasContext: cctx, viewport: vp }).promise;
    return await new Promise((resolve) => c.toBlob(b => resolve(b), 'image/png', 0.98));
  };

  const containerEl = ACTIVE.container || wrap.closest('[data-session-id]') || document.querySelector('#preview-edit');

  // 1) Pega da árvore
  let sessionId = containerEl?.dataset?.sessionId || null;
  // 2) Se não tiver, tenta da última URL usada
  if (!sessionId && ACTIVE.lastUrl) sessionId = extractSessionIdFromUrl(ACTIVE.lastUrl);
  // 3) Guarda de volta no container (para próximas aberturas)
  if (sessionId) setContainerSessionId(containerEl, sessionId);

  await openPageEditorFn({
    bitmap,                         // PNG inicial (rotacionado como a view)
    pageIndex: pageNumber - 1,      // 0-based
    pdfPageSize: { width: pageW, height: pageH }, // base (sem rotação)
    sessionId,
    getBitmap,
    viewRotation                    // mapeamento view->base
  });
}

/* ===== Preview principal ===== */
export async function previewPDF(fileOrUrl, container, spinnerSel, btnSel) {
  const containerEl = typeof container === 'string'
    ? document.querySelector(container)
    : container;
  if (!containerEl) return;

  const spinnerEl = spinnerSel ? document.querySelector(spinnerSel) : null;
  const btnEl     = btnSel     ? document.querySelector(btnSel)     : null;
  const isResult  = containerEl.dataset.mode === 'result';

  // Se a URL for de sessão, grava no container para o editor usar
  if (typeof fileOrUrl === 'string') {
    const sid = extractSessionIdFromUrl(fileOrUrl);
    if (sid) setContainerSessionId(containerEl, sid);
    ACTIVE.lastUrl = fileOrUrl;
  }

  const setSpin = (on) => {
    if (!spinnerEl) return;
    spinnerEl.classList.toggle('hidden', !on);
    spinnerEl.setAttribute('aria-hidden', on ? 'false' : 'true');
  };
  const setBtnDisabled = (on) => { if (btnEl) btnEl.disabled = !!on; };

  const myGen = ++ACTIVE.gen; // invalida renders antigos
  cancelAll();
  ACTIVE.container = containerEl;

  // (re)bind único do resize para este container
  if (containerEl.__resizeHandler) {
    window.removeEventListener('resize', containerEl.__resizeHandler);
  }
  containerEl.__resizeHandler = debounce(() => rerenderVisible(containerEl), 150);
  window.addEventListener('resize', containerEl.__resizeHandler, { passive: true });

  try {
    initPageSelection(containerEl);

    const hadSpinnerInside = spinnerEl && containerEl.contains(spinnerEl);
    clearChildren(containerEl);
    if (hadSpinnerInside) containerEl.appendChild(spinnerEl);

    setSpin(true);
    setBtnDisabled(true);

    const pdf = await loadPdfDoc(fileOrUrl);
    if (myGen !== ACTIVE.gen) { try { pdf.destroy(); } catch {} return; }
    ACTIVE.doc = pdf;

    if (isResult) addResultToolbar(containerEl, setBtnDisabled);

    for (let i = 1; i <= pdf.numPages; i++) {
      const wrap = document.createElement('div');
      wrap.classList.add('page-wrapper', 'page-thumb');
      wrap.dataset.page = i;
      wrap.dataset.pageId = String(i);
      wrap.dataset.rotation = '0';
      wrap.dataset.baseRotation = '0';
      wrap.setAttribute('role', 'option');

      // Seleção inicial: somente em modo resultado seleciona tudo
      const initiallySelected = isResult;
      wrap.setAttribute('aria-selected', initiallySelected ? 'true' : 'false');
      if (initiallySelected && containerEl.selectedPages) {
        containerEl.selectedPages.add(i);
      }

      wrap.tabIndex = 0;

      wrap.addEventListener('pointerdown', (e) => {
        if (e.target.closest('[data-no-drag]')) e.stopPropagation();
      }, true);

      const controls = document.createElement('div');
      controls.classList.add('file-controls');
      controls.setAttribute('data-no-drag', '');

      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.classList.add('remove-file');
      removeBtn.title = 'Remover página';
      removeBtn.setAttribute('aria-label', `Remover página ${i}`);
      removeBtn.setAttribute('data-no-drag', '');
      removeBtn.textContent = '×';
      removeBtn.addEventListener('click', e => {
        e.stopPropagation();
        removePage(containerEl, i, wrap);
        if (!containerEl.querySelector('.page-wrapper')) setBtnDisabled(true);
      });

      const rotateBtn = document.createElement('button');
      rotateBtn.type = 'button';
      rotateBtn.classList.add('rotate-page');
      rotateBtn.title = 'Girar página';
      rotateBtn.setAttribute('aria-label', `Girar página ${i}`);
      rotateBtn.setAttribute('data-no-drag', '');
      rotateBtn.textContent = '⟳';
      rotateBtn.addEventListener('click', async e => {
        e.stopPropagation();
        const rot = (parseInt(wrap.dataset.rotation, 10) + 90) % 360;
        wrap.dataset.rotation = rot.toString();

        const base = parseInt(wrap.dataset.baseRotation || '0', 10);
        const abs  = (base + rot) % 360;

        await renderPage(ACTIVE.doc || pdf, i, containerEl, rot, myGen);
        const rotations = {}; rotations[String(i)] = abs;
        broadcast('gv:preview:rotation', { rotations });
      });

      const cropBtn = document.createElement('button');
      cropBtn.type = 'button';
      cropBtn.classList.add('crop-page');
      cropBtn.title = openPageEditorFn ? 'Editar (texto/whiteout)' : 'Editor indisponível';
      cropBtn.setAttribute('aria-label', `Editar página ${i}`);
      cropBtn.setAttribute('data-no-drag', '');
      cropBtn.textContent = '✎';
      if (!openPageEditorFn) cropBtn.disabled = true;
      cropBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        openInlineEditor(wrap, i).catch(err => console.error('[openInlineEditor]', err));
      });

      controls.append(removeBtn, rotateBtn, cropBtn);

      const badge = document.createElement('div');
      badge.classList.add('page-badge');
      badge.textContent = `Pg ${i}`;
      badge.setAttribute('data-no-drag', '');

      const canvas = document.createElement('canvas');
      canvas.dataset.page = i;
      canvas.setAttribute('draggable', 'false');

      wrap.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        openInlineEditor(wrap, i).catch(err => console.error('[openInlineEditor]', err));
      });

      wrap.addEventListener('click', () => {
        if (!containerEl.selectedPages) return;
        if (wrap.getAttribute('aria-selected') === 'true') {
          wrap.setAttribute('aria-selected', 'false');
          containerEl.selectedPages.delete(i);
        } else {
          wrap.setAttribute('aria-selected', 'true');
          containerEl.selectedPages.add(i);
        }
        const sel = getSelectedPages(containerEl);
        setBtnDisabled(sel.length === 0 && isResult);
        broadcast('gv:preview:pageSelect', { selected: sel });
      });
      wrap.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); wrap.click(); }
      });

      wrap.append(controls, badge, canvas);
      containerEl.appendChild(wrap);
    }

    initGridReorder(containerEl);
    updateOrderBadges(containerEl);

    // Habilita botão (se houver) conforme seleção inicial quando em modo resultado
    if (isResult) {
      const hasSel = getSelectedPages(containerEl).length > 0;
      setBtnDisabled(!hasSel);
    }

    const initial = Math.min(pdf.numPages, INITIAL_BATCH);
    for (let i = 1; i <= initial; i++) {
      const wrap = containerEl.querySelector(`.page-wrapper[data-page="${i}"]`);
      const rot = parseInt(wrap?.dataset?.rotation || '0', 10);
      // eslint-disable-next-line no-await-in-loop
      await renderPage(pdf, i, containerEl, rot, myGen);
    }

    if (pdf.numPages > INITIAL_BATCH) {
      const observer = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            const wrap = entry.target;
            const pg   = Number(wrap.dataset.page);
            obs.unobserve(wrap);
            const rot = parseInt(wrap.dataset.rotation || '0', 10);
            renderPage(pdf, pg, containerEl, rot, myGen);
          }
        });
      }, { rootMargin: '120px 0px 120px 0px' });

      containerEl.querySelectorAll('.page-wrapper')
        .forEach(wrap => observer.observe(wrap));
    }

    containerEl.dispatchEvent(new CustomEvent('preview:ready'));
    document.dispatchEvent(new CustomEvent('preview:ready'));
  } catch (err) {
    console.error('[previewPDF] erro renderizando', err);
    // Mensagem amigável SEM innerHTML (compatível com SES)
    const friendly =
      (err && err.message && err.message.includes('pdf.js não carregado'))
        ? 'Falha ao gerar preview: pdf.js não está incluído no template.'
        : 'Falha ao gerar preview do PDF';
    const containerEl2 = (typeof container === 'string') ? document.querySelector(container) : container;
    if (containerEl2) {
      clearChildren(containerEl2);
      const div = document.createElement('div');
      div.className = 'preview-error';
      div.appendChild(document.createTextNode(friendly));
      containerEl2.appendChild(div);
    }
    throw err;
  } finally {
    if (spinnerSel) {
      const sp = document.querySelector(spinnerSel);
      if (sp) { sp.classList.add('hidden'); sp.setAttribute('aria-hidden', 'true'); }
    }
    const btn = btnSel ? document.querySelector(btnSel) : null;
    if (btn) btn.disabled = false;
  }
}

/* ================================================================
   Organize → payload (order + rotations ABSOLUTAS + seleção)
================================================================ */
export function collectOrganizePayload(containerEl) {
  const root = (typeof containerEl === 'string') ? document.querySelector(containerEl) : containerEl;
  if (!root) return { order: [], rotations: {}, pages: [] };

  const wrappers = Array.from(root.querySelectorAll('.page-wrapper'));

  // ordem atual (1-based)
  const order = wrappers
    .map(w => Number(w.dataset.page))
    .filter(n => Number.isFinite(n));

  // rotações ABSOLUTAS: base + extra(UI)
  const rotations = {};
  wrappers.forEach(wrap => {
    const n = Number(wrap.dataset.page);
    const base = Number(wrap.dataset.baseRotation || 0) % 360;
    let extra  = Number(wrap.dataset.rotation || 0) % 360;
    if (extra < 0) extra += 360;
    const abs = (base + extra) % 360;
    if (abs !== base) rotations[String(n)] = abs;
  });

  // páginas selecionadas
  const pages = wrappers
    .filter(w => w.getAttribute('aria-selected') === 'true')
    .map(w => Number(w.dataset.page));

  return { order, rotations, pages };
}

/* ================================================================
   Export (fora do editor / split)
================================================================ */
export function bindOrganizeExport(opts = {}) {
  const {
    containerSel = '#preview-split',
    buttonSel    = '#btn-split-export',
    inputSel     = '#input-split',
    endpoint     = '/api/organize'
  } = opts;

  const containerEl = document.querySelector(containerSel);
  const btn = document.querySelector(buttonSel);
  const input = document.querySelector(inputSel);
  if (!containerEl || !btn || !input) return;

  btn.addEventListener('click', async () => {
    const file = input.files?.[0] || null;
    if (!file) { alert('Selecione um PDF primeiro.'); return; }
    if (!isPdfFile(file)) { alert('Arquivo inválido: selecione um PDF.'); return; }

    const { pages, rotations } = collectOrganizePayload(containerEl);
    if (!pages.length) { alert('Nenhuma página selecionada.'); return; }

    btn.disabled = true;
    try {
      const fd = new FormData();
      fd.append('file', file, file.name || 'input.pdf');
      fd.append('pages', JSON.stringify(pages));
      fd.append('rotations', JSON.stringify(rotations));

      const resp = await fetch(endpoint, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken() },
        body: fd
      });
      if (!resp.ok) { const msg = await resp.text(); alert(msg || 'Falha ao exportar.'); return; }

      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'organizado.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('[bindOrganizeExport] erro', e);
      alert('Erro inesperado ao exportar.');
    } finally {
      btn.disabled = false;
    }
  });
}

/* ================================================================
   Integrações com o editor (eventos)
================================================================ */
document.addEventListener('gv:editor:cropCleared', () => {
  document.querySelectorAll('.page-wrapper[data-page]').forEach(async (wrap) => {
    const had = !!wrap.dataset.crop;
    delete wrap.dataset.crop;
    delete wrap.dataset.cropAbs;
    const btn = wrap.querySelector('.crop-page');
    if (btn) btn.textContent = '✎';
    if (had) {
      try { broadcast('gv:preview:selection', { type: 'crop', rects: [] }); }
      catch {}
    }
  });
});

document.addEventListener('gv:preview:refresh', async (ev) => {
  const { url, containerSel = '#preview-edit', ts } = ev.detail || {};
  if (!url) return;
  const bust = addCacheBust(url, ts);
  try {
    await previewPDF(bust, containerSel, null, null);
  } catch (e) {
    console.error('[gv:preview:refresh] falha ao recarregar', e);
  }
});

/* ===== quando salvar no wizard, recarrega o preview da sessão ===== */
document.addEventListener('page-editor:saved', (ev) => {
  const detail = ev.detail || {};
  const containerEl = ACTIVE.container || document.querySelector('#preview-edit');
  const sid = detail.session_id || containerEl?.dataset?.sessionId || ACTIVE.sessionId || extractSessionIdFromUrl(ACTIVE.lastUrl || '');
  if (!sid) return;

  // Limpa qualquer crop client-side pendente (evita "double crop")
  document.querySelectorAll('.page-wrapper[data-page]').forEach(wrap => {
    delete wrap.dataset.crop;
    delete wrap.dataset.cropAbs;
    const btn = wrap.querySelector('.crop-page');
    if (btn) btn.textContent = '✎';
  });

  setContainerSessionId(containerEl, sid);
  const ts = detail.ts || Date.now();
  const url = addCacheBust(`/api/edit/file/${sid}`, ts);
  ACTIVE.lastUrl = url;
  broadcast('gv:preview:refresh', { url, containerSel: '#preview-edit', ts });
});

/* ================================================================
   Comandos externos para restaurar ordem/rotação
================================================================ */
document.addEventListener('gv:preview:setOrder', (ev) => {
  const { order, containerSel = '#preview-edit' } = ev.detail || {};
  const root = document.querySelector(containerSel);
  if (!root || !Array.isArray(order) || !order.length) return;

  const map = new Map();
  root.querySelectorAll('.page-wrapper').forEach(w => {
    map.set(Number(w.dataset.page), w);
  });

  order.forEach(n => {
    const w = map.get(Number(n));
    if (w) root.appendChild(w);
  });

  const containerEl = document.querySelector(containerSel);
  if (containerEl) {
    const items = Array.from(containerEl.querySelectorAll('.page-wrapper'));
    items.forEach((w, idx) => {
      let ob = w.querySelector('.order-badge');
      if (!ob) {
        ob = document.createElement('div');
        ob.className = 'order-badge';
        ob.setAttribute('data-no-drag', '');
        w.appendChild(ob);
      }
      ob.textContent = `#${idx + 1}`;
    });
  }
  broadcast('gv:preview:reorder', { order });
});

document.addEventListener('gv:preview:setRotations', async (ev) => {
  const { rotations, containerSel = '#preview-edit' } = ev.detail || {};
  const root = document.querySelector(containerSel);
  if (!root || !rotations || !ACTIVE.doc) return;

  for (const [k, v] of Object.entries(rotations)) {
    const pg = Number(k);
    const wrap = root.querySelector(`.page-wrapper[data-page="${pg}"]`);
    if (!wrap) continue;
    const base = parseInt(wrap.dataset.baseRotation || '0', 10);
    const abs  = ((Number(v) || 0) % 360 + 360) % 360;
    const extra = ((abs - base) % 360 + 360) % 360;

    wrap.dataset.rotation = String(extra);
    try { await renderPage(ACTIVE.doc, pg, root, extra, ACTIVE.gen); }
    catch {}
  }
});

/* ================================================================
   Reset externo (limpa grid e fecha sessão)
================================================================ */
export async function resetPreview(containerSel = '#preview-edit') {
  const root = document.querySelector(containerSel);
  if (!root) return;
  clearChildren(root);
  if (root.__resizeHandler) {
    window.removeEventListener('resize', root.__resizeHandler);
    delete root.__resizeHandler;
  }
  await closeSession(root);
}