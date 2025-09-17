// - Mantém a prévia do <input type="file"> em <img data-preview-target="...">
// - Injeta botões ↻/× nas thumbs dos grids (#preview-split / #preview-compress)
// - Rotação aplicada no .thumb-frame (split/compress). Merge opcional (media).

import { previewThumb } from './preview.js';

/* ===================== MINIATURA DO INPUT (form) ======================= */

function isPdfFile(file) {
  if (!file) return false;
  const nameOk = /\.pdf$/i.test(file.name || '');
  const typeOk = (file.type || '') === 'application/pdf';
  return nameOk || typeOk;
}

function pickFirstPdf(fileList) {
  if (!fileList || !fileList.length) return null;
  for (const f of fileList) {
    if (isPdfFile(f)) return f;
  }
  return null;
}

function bindThumbPreview(input) {
  const selector = input.getAttribute('data-preview-target');
  if (!selector) return;
  const imgEl = document.querySelector(selector);
  if (!imgEl) return;

  // começa oculta
  imgEl.hidden = true;
  imgEl.alt = 'Sem miniatura';

  // se a imagem quebrar por qualquer motivo, esconda
  imgEl.addEventListener('error', () => {
    imgEl.hidden = true;
    imgEl.removeAttribute('src');
    imgEl.alt = 'Sem miniatura';
  });

  input.addEventListener('change', async () => {
    let file = input.multiple ? pickFirstPdf(input.files)
                              : (isPdfFile(input.files?.[0]) ? input.files[0] : null);

    if (!file) {
      delete imgEl.dataset.thumbId;
      imgEl.hidden = true;
      imgEl.removeAttribute('src');
      imgEl.alt = 'Sem miniatura';
      return;
    }

    try {
      await previewThumb(file, imgEl); // chama API /api/preview e seta img.src
      imgEl.hidden = false;
    } catch (err) {
      console.error('[preview-init] Falha ao gerar miniatura do PDF:', err);
      delete imgEl.dataset.thumbId;
      imgEl.hidden = true;
      imgEl.removeAttribute('src');
      imgEl.alt = 'Sem miniatura';
    }
  });
}

/* ===================== CONTROLES ↻/× NAS THUMBS ======================== */
/**
 * mode: 'frame' (split/compress) | 'media' (merge)
 */
function getRotateTarget(wrapper, mode) {
  if (!wrapper) return null;
  return mode === 'media'
    ? wrapper.querySelector('.thumb-media')   // MERGE gira a mídia
    : wrapper.querySelector('.thumb-frame');  // SPLIT/COMPRESS giram o frame
}

function getRotation(wrapper) {
  const v = parseInt(wrapper?.dataset?.rotation || '0', 10);
  return Number.isNaN(v) ? 0 : v;
}

function setRotation(wrapper, deg, mode) {
  wrapper.dataset.rotation = String(deg);
  const target = getRotateTarget(wrapper, mode);
  if (!target) return;

  // preserva outros transforms, removendo rotações prévias
  const prev = target.style.transform || '';
  const cleaned = prev.replace(/rotate\([^)]*\)/g, '').trim();
  target.style.transform = (cleaned + ' rotate(' + deg + 'deg)').trim();
}

function ensureControls(wrapper) {
  if (!wrapper || wrapper.querySelector(':scope > .file-controls')) return null;

  const controls = document.createElement('div');
  controls.className = 'file-controls';
  controls.setAttribute('data-no-drag', ''); // impede que o drag pegue os botões

  const btnRotate = document.createElement('button');
  btnRotate.type = 'button';
  btnRotate.className = 'rotate-page';
  btnRotate.setAttribute('aria-label', 'Girar 90°');

  const btnRemove = document.createElement('button');
  btnRemove.type = 'button';
  btnRemove.className = 'remove-file';
  btnRemove.setAttribute('aria-label', 'Remover página');

  controls.appendChild(btnRotate);
  controls.appendChild(btnRemove);
  wrapper.appendChild(controls);
  return controls;
}

function renumberOrderBadges(container) {
  if (!container) return;
  const cards = container.querySelectorAll('.page-wrapper.page-thumb');
  let idx = 1;
  cards.forEach((card) => {
    const badge = card.querySelector('.order-badge, .page-order, .page-index, .page-num');
    if (badge) badge.textContent = '#' + (idx++);
  });
}

function bindThumbHandlers(wrapper, mode) {
  if (!wrapper) return;
  if (wrapper.dataset.controlsBound === '1') return;

  ensureControls(wrapper);
  wrapper.dataset.controlsBound = '1';

  // rotação
  const rotateBtn = wrapper.querySelector(':scope > .file-controls .rotate-page');
  if (rotateBtn) {
    rotateBtn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const curr = getRotation(wrapper);
      const next = (curr + 90) % 360;
      setRotation(wrapper, next, mode);
    }, { passive: true });
  }

  // remover
  const removeBtn = wrapper.querySelector(':scope > .file-controls .remove-file');
  if (removeBtn) {
    removeBtn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      wrapper.remove();
      renumberOrderBadges(wrapper?.parentElement);
    }, { passive: true });
  }

  // rotação inicial no dataset (0) para consistência
  if (!wrapper.dataset.rotation) wrapper.dataset.rotation = '0';
}

function processThumbContainer(container, mode) {
  if (!container) return;
  container.querySelectorAll('.page-wrapper.page-thumb')
    .forEach((card) => bindThumbHandlers(card, mode));
}

function observeThumbContainer(container, mode) {
  if (!container) return;
  const obs = new MutationObserver((mutations) => {
    for (const m of mutations) {
      m.addedNodes && m.addedNodes.forEach((node) => {
        if (!(node instanceof HTMLElement)) return;
        if (node.matches && node.matches('.page-wrapper.page-thumb')) {
          bindThumbHandlers(node, mode);
        } else if (node.querySelectorAll) {
          node.querySelectorAll('.page-wrapper.page-thumb')
            .forEach((card) => bindThumbHandlers(card, mode));
        }
      });
    }
  });
  obs.observe(container, { childList: true, subtree: true });
}

function enableThumbBasicControls(selector, mode) {
  const container = document.querySelector(selector);
  if (!container) return;
  processThumbContainer(container, mode);
  observeThumbContainer(container, mode);
}

/* ============================ INIT ===================================== */

function init() {
  // 1) Prévia de <input type="file" data-preview-target="#seletor">
  document
    .querySelectorAll('input[type="file"][data-preview-target]')
    .forEach(bindThumbPreview);

  // 2) Botões ↻/× nas thumbs (split/compress → giro no FRAME)
  enableThumbBasicControls('#preview-split', 'frame');
  enableThumbBasicControls('#preview-compress', 'frame');

  // 3) Opcional: habilitar também no merge (gira a MÍDIA)
  // enableThumbBasicControls('#preview-merge', 'media');
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}