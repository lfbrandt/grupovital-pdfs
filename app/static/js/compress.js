/* ========================================================================
   Página /compress — rotação/fitting iguais ao /merge e /split
   + neutralização de handlers conflitantes do preview.js
   + grid resolvido por seletor fixo (#preview-compress) — sem “index 0”
   Compatível com CSP rígida (sem inline); sem dupla inicialização.
   >>> Atualização: usa utils.fitRotateMedia() e utils.getThumbWidth()
   ======================================================================== */
'use strict';

import { previewPDF } from './preview.js';
import {
  getCSRFToken,
  normalizeAngle,
  getMediaSize,
  getCropBoxAbs,
  collectPagesRotsCropsAllOrSelection,
  fitRotateMedia,           // << novo (exportado pelo utils.js)
  getThumbWidth,            // << novo (exportado pelo utils.js)
} from './utils.js';

/* ================= Perfil (somente UI do <select>) ================= */
document.addEventListener('DOMContentLoaded', () => {
  const sel  = document.getElementById('profile');
  const hint = document.getElementById('profile-hint');
  if (sel && hint) {
    const applyHint = () => {
      const opt = sel.selectedOptions && sel.selectedOptions[0];
      hint.textContent = opt?.dataset?.hint || '';
    };
    applyHint();
    sel.addEventListener('change', applyHint, { passive: true });
  }
});

/* ================= Guards globais de página ================= */
const __GV_COMPRESS = (window.__GV_COMPRESS = window.__GV_COMPRESS || {});
let __uploadGen = 0;           // contador de mudanças no input
let __uploadInflight = null;   // promessa atual (evita concorrência)

/* ================= Constantes/Helpers ================= */
const PREFIX = 'compress';
const ROOT_SELECTOR = '#preview-' + PREFIX; // => #preview-compress
const ITEM_SELECTOR = '.page-wrapper, .page-thumb, .thumb-card, [data-page]';
const CONTENT_SELECTOR = 'img.thumb-media,canvas.thumb-media,.thumb-canvas,.thumb-image,img,canvas';

const root = () => /** @type {HTMLElement|null} */(document.querySelector(ROOT_SELECTOR));

const hostOf     = (el) => el?.closest?.(ITEM_SELECTOR) || el;
const getContent = (el) => hostOf(el)?.querySelector?.(CONTENT_SELECTOR);

/* =================== Frame & Mídia (modelo igual /merge) =================== */
/*
  Estrutura alvo:
  page-wrapper
    └─ .thumb-frame
        └─ (img|canvas).thumb-media
*/
function ensureFrame(thumb) {
  if (!thumb) return null;

  let frame = thumb.querySelector(':scope > .thumb-frame');
  if (!frame) {
    frame = document.createElement('div');
    frame.className = 'thumb-frame';
    Object.assign(frame.style, {
      position: 'absolute',
      inset: '0',
      inlineSize: '100%',
      blockSize: '100%',
      background: '#fff',
      overflow: 'hidden',
      display: 'block',
      contain: 'paint'
    });
    const cs = getComputedStyle(thumb);
    if (cs.position === 'static') thumb.style.position = 'relative';
    thumb.appendChild(frame);
  }

  // pega conteúdo existente (canvas/img). Se estiver fora do frame, move pra dentro.
  let content = getContent(thumb) || getContent(frame);
  if (content && content.parentElement !== frame) {
    content.parentElement?.removeChild(content);
    frame.appendChild(content);
  }

  // Normaliza classe/estilo da mídia
  if (content) {
    content.classList.add('thumb-media');
    Object.assign(content.style, {
      position: 'absolute',
      left: '50%', top: '50%',
      transform: 'translate(-50%, -50%)',
      transformOrigin: '50% 50%',
      display: 'block',
      maxWidth: 'none',
      height: 'auto',
      backfaceVisibility: 'hidden',
      willChange: 'transform'
    });
  }

  return { frame, content };
}

/* =================== Fit + Rotate com fonte única (utils) =================== */
function applyPreviewRotation(thumb, angle) {
  const ctx = ensureFrame(thumb);
  if (!ctx || !ctx.frame || !ctx.content) return;
  const ang = normalizeAngle(angle || 0);
  ctx.content.dataset.deg = String(ang);
  fitRotateMedia({ frameEl: ctx.frame, mediaEl: ctx.content, angle: ang });
}

function rotateThumb(thumb, delta) {
  let a = parseInt(thumb.getAttribute('data-rotation') || '0', 10);
  if (!Number.isFinite(a)) a = 0;
  a = normalizeAngle(a + delta);
  thumb.setAttribute('data-rotation', String(a));
  applyPreviewRotation(thumb, a);
}

/* =================== Neutralização do preview.js (somente ROTATE) ========= */
function patchPreviewRotateButtons() {
  const r = root(); if (!r) return;
  r.querySelectorAll('button.rotate-page:not([data-gv-patched])').forEach(btn => {
    const clone = btn.cloneNode(true);     // remove listeners originais
    clone.dataset.gvPatched = '1';
    if (!clone.dataset.action) clone.setAttribute('data-action', 'rotate-right');
    btn.replaceWith(clone);
  });
}

/** Remove botões que não sejam X/Rotate (defesa extra) */
function pruneToolbarButtons() {
  const r = root(); if (!r) return;
  r.querySelectorAll('.thumb-actions, .file-controls').forEach(bar => {
    [...bar.children].forEach(btn => {
      const cls = new Set([...btn.classList]);
      const act = (btn.dataset.action || '').toLowerCase();
      const isRotate = cls.has('rotate-page') ||
        act === 'rotate-right' || act === 'rot-right' ||
        act === 'rotate-left'  || act === 'rot-left';
      const isRemove = cls.has('remove-file') ||
        act === 'remove' || act === 'delete' || act === 'close';
      if (!(isRotate || isRemove)) btn.remove();
    });
  });
}

/** Observa novos nós do GRID para re-patches */
function bindPatchObserver() {
  const r = root(); if (!r || r.__patchObs) return;
  const run = () => { patchPreviewRotateButtons(); pruneToolbarButtons(); };
  run();
  const mo = new MutationObserver((muts) => {
    let need = false;
    muts.forEach(m => m.addedNodes && m.addedNodes.forEach(n => {
      if (n.nodeType === 1 &&
         (n.matches?.('button.rotate-page, .thumb-actions, .file-controls') ||
          n.querySelector?.('button.rotate-page, .thumb-actions, .file-controls'))) need = true;
    }));
    if (need) run();
  });
  mo.observe(r, { childList: true, subtree: true });
  r.__patchObs = mo;
}

/* =================== Bloqueios de eventos paralelos (GRID) ================= */
const _swallowEvents = ['pointerdown','pointerup','mousedown','mouseup'];
function swallowToolbarEvents() {
  const r = root(); if (!r) return;
  if (r.__compressSwallowBound) return;
  r.__compressSwallowBound = true;

  _swallowEvents.forEach((type) => {
    r.addEventListener(type, (ev) => {
      const t = ev.target;
      if (!t) return;
      const btn = t.closest?.('[data-action],button.rotate-page');
      if (!btn || !r.contains(btn)) return;
      ev.stopPropagation();
    }, true); // capture
  });
}

/* =================== Toolbar (nosso handler — GRID) =================== */
function bindToolbarActions() {
  const r = root(); if (!r) return;
  if (r.__compressToolbarBound) return;
  r.__compressToolbarBound = true;

  r.addEventListener('click', (ev) => {
    const btn = ev.target.closest?.('[data-action],button.rotate-page'); if (!btn) return;
    if (!r.contains(btn)) return;

    const thumb = hostOf(btn.closest(ITEM_SELECTOR)); if (!thumb) return;
    const act = (btn.dataset.action || '').toLowerCase();

    // ROTATE: tratamos aqui
    const isRotateBtn = btn.matches('button.rotate-page') ||
                        act === 'rot-right' || act === 'rotate-right' ||
                        act === 'rot-left'  || act === 'rotate-left';
    if (isRotateBtn) {
      ev.preventDefault(); ev.stopPropagation();
      rotateThumb(thumb, (act.includes('left')) ? -90 : +90);
      return;
    }

    // “remove” deixa o preview.js cuidar
  }, true);
}

/* =================== Desabilita editor/dblclick no GRID =================== */
function disableEditorTriggers() {
  const r = root(); if (!r) return;
  if (r.__compressNoEditor) return;
  r.__compressNoEditor = true;

  r.addEventListener('dblclick', (ev) => {
    if (ev.target.closest(ITEM_SELECTOR)) { ev.stopPropagation(); ev.preventDefault(); }
  }, true);

  r.addEventListener('keydown', (ev) => {
    if (!r.contains(document.activeElement)) return;
    const k = (ev.key || '').toLowerCase();
    if (k === 'enter' || k === 'e') { ev.stopPropagation(); }
  }, true);
}

/* =================== Submit: envia pages/rotations/crops =================== */
function ensureHidden(form, name, value){
  let input = form.querySelector(`input[name="${name}"]`);
  if (!input) { input = document.createElement('input'); input.type='hidden'; input.name=name; form.appendChild(input); }
  input.value = value ?? '';
}

function bindFormSubmit(){
  if (document.__compressFormBound) return;
  document.__compressFormBound = true;

  document.addEventListener('submit', (e) => {
    const form = e.target?.closest?.('#compress-form'); if (!form) return;

    const grid = root();
    if (!grid) return;

    const { pages, rotations, crops } = collectPagesRotsCropsAllOrSelection(grid);

    // Mapa de rotações absolutas (compat por página)
    const rotMap = {};
    pages.forEach((pg, idx) => {
      const el = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
      const base = Number(el?.dataset?.baseRotation || 0) % 360;
      const extra = rotations[idx] ?? 0;
      const abs = (base + extra) % 360;
      if (abs !== base) rotMap[String(pg)] = abs;
    });

    ensureHidden(form, 'pages', JSON.stringify(pages || []));
    ensureHidden(form, 'rotations', JSON.stringify(rotations || []));
    ensureHidden(form, 'rotations_map', JSON.stringify(rotMap || {}));
    if (crops && crops.length) {
      ensureHidden(form, 'modificacoes', JSON.stringify({ crops }));
    } else {
      ensureHidden(form, 'modificacoes', '');
    }
  }, true);
}

/* =================== Upload → uma única previewPDF por mudança ============ */
function bindUploadPreviewOnce() {
  const input   = /** @type {HTMLInputElement|null} */(document.getElementById('input-compress'));
  const preview = /** @type {HTMLElement|null} */(document.getElementById('preview-compress'));
  const spinnerSel = '#spinner-compress';
  if (!input || !preview) return;

  if (__GV_COMPRESS.inputBound) return;
  __GV_COMPRESS.inputBound = true;

  input.addEventListener('change', async () => {
    const file = (input.files && input.files[0]) || null;
    if (!file) return;

    const myGen = ++__uploadGen;
    console.debug('[compress] upload#%d:', myGen, file.name, file.size);

    if (__uploadInflight) {
      console.debug('[compress] abortando preview anterior (upload em paralelo)');
    }

    try {
      const p = previewPDF(file, preview, spinnerSel, null);
      __uploadInflight = p;
      await p;

      if (__uploadInflight === p) {
        // Após render, re-fit/rotate todas as páginas (garante centralização/nitidez)
        preview.querySelectorAll(ITEM_SELECTOR).forEach(t => {
          const ctx = ensureFrame(t);
          if (!ctx || !ctx.frame || !ctx.content) return;
          const a = parseInt(t.getAttribute('data-rotation') || '0', 10) || 0;
          ctx.content.dataset.deg = String(a);
          fitRotateMedia({ frameEl: ctx.frame, mediaEl: ctx.content, angle: a });
        });
        console.debug('[compress] preview concluído upload#%d', myGen);
      }
    } catch (e) {
      console.error('[compress] erro no preview upload#%d', myGen, e);
    } finally {
      __uploadInflight = null;
    }
  }, { passive: true });
}

/* =================== Init/Rebind ========================================= */
function initCompress() {
  const list = root(); if (!list) return;

  // seletor explícito para guards externos
  try { window.__GV_COMPRESS_GRID_SELECTOR = ROOT_SELECTOR; } catch (_) {}

  disableEditorTriggers();
  swallowToolbarEvents();
  bindToolbarActions();
  bindFormSubmit();

  patchPreviewRotateButtons();
  pruneToolbarButtons();
  bindPatchObserver();

  bindUploadPreviewOnce();

  // Ajusta thumbs já presentes
  list.querySelectorAll(ITEM_SELECTOR).forEach(t => {
    const ctx = ensureFrame(t);
    if (!ctx || !ctx.frame || !ctx.content) return;
    const a = parseInt(t.getAttribute('data-rotation') || '0', 10) || 0;
    ctx.content.dataset.deg = String(a);
    fitRotateMedia({ frameEl: ctx.frame, mediaEl: ctx.content, angle: a });
  });
}

document.addEventListener('DOMContentLoaded', initCompress);

/* Rebind minimalista com debounce (mudanças dinâmicas do GRID) */
let __debounceTO = null;
(function bindLocalObserver(){
  const container = root() || document.documentElement;
  const observer = new MutationObserver(() => {
    const list = root(); if (!list) return;
    if (__debounceTO) clearTimeout(__debounceTO);
    __debounceTO = setTimeout(() => {
      __debounceTO = null;
      initCompress();
    }, 50);
  });
  observer.observe(container, { childList: true, subtree: true });
})();

/* Debug helper */
try { window.GV_COMPRESS_GET_ROTATIONS = () => {
  const grid = root(); if (!grid) return {};
  const { pages, rotations } = collectPagesRotsCropsAllOrSelection(grid);
  const out = {};
  pages.forEach((pg, i) => { const r = rotations[i] || 0; if (r) out[String(pg)] = r; });
  return out;
}; } catch (_){}