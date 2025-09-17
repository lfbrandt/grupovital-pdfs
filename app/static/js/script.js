import { previewPDF, getSelectedPages } from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';
import {
  mostrarLoading,
  resetarProgresso,
  atualizarProgresso,
  mostrarMensagem,
  getCSRFToken,
} from './utils.js';
import { xhrRequest, compressFile } from './api.js';

const PDF_EXTS   = ['pdf'];
const IMG_EXTS   = ['jpg','jpeg','png','bmp','tiff'];
const DOC_EXTS   = ['doc','docx','odt','rtf','txt','html'];
const SHEET_EXTS = ['xls','xlsx','ods'];
const PPT_EXTS   = ['ppt','pptx','odp'];

/** ⇩⇩ Estratégia para não-PDF no MERGE:
 * false = bloquear com toast; true = auto-converter via /api/convert e seguir
 */
const MERGE_AUTO_CONVERT_NON_PDF = false;

// Limite para desativar prévia base64 de imagens muito grandes (evita freeze)
const MAX_IMAGE_PREVIEW_BYTES = 10 * 1024 * 1024; // 10 MB

let lastConvertedFile = null;

function getExt(name){ return name.split('.').pop().toLowerCase(); }

// Cooperatively yield: devolve o controle ao browser entre tarefas pesadas
async function yieldToBrowser() {
  await new Promise(r => requestAnimationFrame(r));
  await new Promise(r => requestAnimationFrame(r));
  await new Promise(r => setTimeout(r, 0));
}

function showGenericPreview(file, container){
  if(!container) return;

  // Evita travar ao tentar gerar dataURL imenso
  if (file && typeof file.size === 'number' && file.size > MAX_IMAGE_PREVIEW_BYTES) {
    container.innerHTML = `
      <div class="generic-preview-fallback" aria-live="polite">
        <div class="file-name">${file.name}</div>
        <div class="file-note">Prévia desativada (imagem muito grande: ${(file.size/1024/1024).toFixed(1)} MB)</div>
      </div>
    `;
    return;
  }

  const reader = new FileReader();
  reader.onload = e => {
    container.innerHTML = `
      <img class="generic-preview-img" src="${e.target.result}" alt="${file.name}" draggable="false" />
      <div class="file-name">${file.name}</div>
    `;
  };
  reader.readAsDataURL(file);
}

/* ========== helpers de PDF (capa + número de páginas) ========== */
async function readArrayBuffer(file){
  if (file.arrayBuffer) return await file.arrayBuffer();
  return await new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.onerror = rej;
    fr.readAsArrayBuffer(file);
  });
}

// Renderiza a 1ª página como miniatura do cartão (leve)
async function renderPdfCover(file, container, maxW = 260){
  try{
    const data = await readArrayBuffer(file);
    const pdf = await window.pdfjsLib.getDocument({ data }).promise;
    const page = await pdf.getPage(1);
    const viewport = page.getViewport({ scale: 1 });
    const scale = Math.min(maxW / viewport.width, 1.8);
    const vp = page.getViewport({ scale });

    const canvas = document.createElement('canvas');
    canvas.width = Math.ceil(vp.width);
    canvas.height = Math.ceil(vp.height);
    const ctx = canvas.getContext('2d', { alpha: false });

    await page.render({ canvasContext: ctx, viewport: vp }).promise;
    container.innerHTML = '';
    container.appendChild(canvas);
  }catch{
    container.textContent = 'Prévia indisponível';
  }
}

// Lê o total de páginas
async function getPdfPageCount(file){
  try{
    const data = await readArrayBuffer(file);
    const pdf = await window.pdfjsLib.getDocument({ data }).promise;
    return pdf.numPages || 1;
  }catch{
    return 1;
  }
}

/* ================================
   HELPERS: crops/rots por página
   ================================ */
function getCropBoxAbs(el){
  if (!el) return null;
  let abs = el.dataset.cropAbs || el.dataset.cropabs;
  if (abs) {
    try {
      const box = JSON.parse(abs);
      if (Array.isArray(box) && box.length === 4) return box.map(Number);
    } catch {}
  }
  const norm = el.dataset.crop;
  if (norm && (el.dataset.pdfW || el.dataset.pdfw) && (el.dataset.pdfH || el.dataset.pdfh)) {
    try {
      const { x0, y0, x1, y1 } = JSON.parse(norm);
      const W = Number(el.dataset.pdfW || el.dataset.pdfw || 0);
      const H = Number(el.dataset.pdfH || el.dataset.pdfh || 0);
      if (W > 0 && H > 0) {
        return [x0 * W, y0 * H, x1 * W, y1 * H].map(n => Math.max(0, Math.round(n)));
      }
    } catch {}
  }
  return null;
}

function collectPagesRotsCropsFromGrid(gridEl){
  const pages = getSelectedPages(gridEl, true);
  const rotations = pages.map(pg => {
    const el = gridEl.querySelector(`.page-wrapper[data-page="${pg}"]`);
    return Number(el?.dataset.rotation) || 0;
  });
  const crops = [];
  pages.forEach(pg => {
    const el = gridEl.querySelector(`.page-wrapper[data-page="${pg}"]`);
    const box = getCropBoxAbs(el);
    if (box) crops.push({ page: pg, box });
  });
  return { pages, rotations, crops };
}

function stampFileMetaOnGrid(gridEl, fileIndex){
  gridEl.querySelectorAll('.page-wrapper').forEach(wrap => {
    if (!wrap.dataset.fileIndex) wrap.dataset.fileIndex = String(fileIndex);
    if (wrap.dataset.srcPage == null) {
      const pg1 = Number(wrap.dataset.page || '1');
      wrap.dataset.srcPage = String(Math.max(0, pg1 - 1));
    }
  });
}

function removePage(containerEl, pageNum, wrap){
  wrap.remove();
  containerEl.selectedPages?.delete(pageNum);
}

/* ======================================================================
   SELEÇÃO (MERGE): Shift range, Ctrl/Cmd toggle, ESC limpa, grupo visual
   ====================================================================== */
function attachMergeSelection(container){
  if (!container || container.__selBound) return;
  container.__selBound = true;
  const itemSel = '.file-wrapper';
  const getItems = ()=> Array.from(container.querySelectorAll(itemSel));

  container.__selection = new Set();
  container.__lastClickedIndex = null;

  container.__applySelectionClasses = () => {
    const items = getItems();
    items.forEach(el => {
      const on = container.__selection.has(el);
      el.classList.toggle('is-selected', on);
      el.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  };

  container.addEventListener('click', (e) => {
    const card = e.target.closest(itemSel);
    if (!card || !container.contains(card)) return;

    const items = getItems();
    const idx = items.indexOf(card);

    if (e.metaKey || e.ctrlKey) {
      if (container.__selection.has(card)) container.__selection.delete(card);
      else container.__selection.add(card);
      container.__lastClickedIndex = idx;
    } else if (e.shiftKey && container.__lastClickedIndex != null) {
      const [a,b] = [container.__lastClickedIndex, idx].sort((x,y)=>x-y);
      container.__selection.clear();
      items.slice(a, b+1).forEach(el => container.__selection.add(el));
    } else {
      container.__selection.clear();
      container.__selection.add(card);
      container.__lastClickedIndex = idx;
    }

    container.__applySelectionClasses();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      container.__selection.clear();
      container.__applySelectionClasses();
    }
  });
}

/* ======================================================================
   DnD (cartões e páginas) — Pointer Events + swap/insert + auto-scroll
   ====================================================================== */
function swapInParent(a, b) {
  if (!a || !b || !a.parentNode || a.parentNode !== b.parentNode) return;
  const parent = a.parentNode;
  const marker = document.createComment('dnd-swap');
  parent.replaceChild(marker, a);
  parent.replaceChild(a, b);
  parent.replaceChild(b, marker);
}

function emitReorder(container, selector) {
  const order = Array.from(container.querySelectorAll(selector)).map(el =>
    el.dataset.index ?? el.dataset.pageId ?? ''
  );
  container.dispatchEvent(new CustomEvent('reorder', { detail: { order } }));
}

function autoScrollViewport(clientX, clientY, { edge = 30, speed = 16 }) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let dx = 0;
  let dy = 0;
  if (clientX < edge) dx = -speed;
  else if (clientX > vw - edge) dx = speed;

  if (clientY < edge) dy = -speed;
  else if (clientY > vh - edge) dy = speed;

  if (dx || dy) window.scrollBy(dx, dy);
}

function makeSortableGrid(container, itemSelector, opts = {}) {
  const {
    mode = 'swap',
    dragHandle = null,
    scrollEdge = 28,
    scrollSpeed = 18,
    hoverDelayMs = 80,
    dragPreview = 'card',
  } = opts;

  if (!container || container.__dndBound) return;
  container.__dndBound = true;

  const getItems = () => Array.from(container.querySelectorAll(itemSelector));
  const isInteractiveTarget = (el) =>
    !!el.closest?.('button, a, input, textarea, select, [contenteditable="true"], [role="button"], [data-no-drag]');

  let dragging = null;
  let placeholder = null;
  let startRect = null;
  let startX = 0, startY = 0;
  let offsetX = 0, offsetY = 0;
  let lastTarget = null;
  let hoverTimer = null;
  let pointerId = null;

  let armed = false;
  let activated = false;
  let downAt = 0;
  const THRESH_PX = 6;
  const HOLD_TOUCH_MS = 140;

  const getSelectedItems = () => {
    const sel = container.__selection;
    if (!(sel && sel.size)) return [];
    return getItems().filter(el => sel.has(el));
  };
  let draggingGroup = false;

  function clearHoverTimer() {
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
  }
  function setDropHighlight(target) {
    if (lastTarget && lastTarget !== target) lastTarget.classList.remove('drop-highlight');
    if (target) target.classList.add('drop-highlight');
    lastTarget = target;
  }
  function moveDragged(item, clientX, clientY) {
    if (!activated || dragPreview === 'none') return;
    const x = clientX - offsetX;
    const y = clientY - offsetY;
    item.style.left = `${x}px`;
    item.style.top  = `${y}px`;
    item.style.transform = 'translate(0,0)';
  }
  function reorderInsert(target, clientX, clientY) {
    const r = target.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top  + r.height / 2;
    const horizontal = Math.abs(clientX - cx) > Math.abs(clientY - cy);
    if (horizontal) {
      if (clientX < cx) target.parentNode.insertBefore(placeholder, target);
      else              target.parentNode.insertBefore(placeholder, target.nextSibling);
    } else {
      if (clientY < cy) target.parentNode.insertBefore(placeholder, target);
      else              target.parentNode.insertBefore(placeholder, target.nextSibling);
    }
  }

  function activateDrag(item) {
    if (activated) return;
    activated = true;

    if (!(container.__selection?.has(item))) {
      container.__selection?.clear?.();
      container.__selection?.add?.(item);
      container.__applySelectionClasses?.();
    }
    draggingGroup = container.__selection && container.__selection.size > 1 && container.__selection.has(item);

    startRect = item.getBoundingClientRect();
    placeholder = document.createElement('div');
    placeholder.className = 'file-placeholder';
    placeholder.style.width  = `${startRect.width}px`;
    placeholder.style.height = `${startRect.height}px`;
    item.parentNode.insertBefore(placeholder, item);

    container.classList.toggle('group-dragging', draggingGroup);
    container.__selection?.forEach?.(el => el.classList.toggle('is-drag-proxy', draggingGroup));

    item.classList.add('dnd-dragging');
    item.style.width = `${startRect.width}px`;
    item.style.height = `${startRect.height}px`;
    item.style.position = 'fixed';
    item.style.left = `${startRect.left}px`;
    item.style.top = `${startRect.top}px`;
    item.style.zIndex = '9999';
    item.style.pointerEvents = 'none';
    item.style.transform = 'translate(0,0)';
    if (dragPreview === 'none') { item.style.opacity = '0'; item.style.boxShadow = 'none'; }
  }

  function cleanupItemStyles(item) {
    item.classList.remove('dnd-dragging');
    item.style.width = '';
    item.style.height = '';
    item.style.position = '';
    item.style.left = '';
    item.style.top = '';
    item.style.transform = '';
    item.style.zIndex = '';
    item.style.pointerEvents = '';
    item.style.opacity = '';
    item.style.boxShadow = '';
  }

  function endDrag(successDrop = false) {
    clearHoverTimer();
    if (lastTarget) { lastTarget.classList.remove('drop-highlight'); lastTarget = null; }
    if (!dragging) return;

    if (activated && placeholder) {
      if (successDrop) {
        if (draggingGroup) {
          const beforeNode = placeholder;
          getSelectedItems().forEach(el => container.insertBefore(el, beforeNode));
          placeholder.remove();
        } else {
          placeholder.parentNode.replaceChild(dragging, placeholder);
        }
      } else {
        placeholder.remove();
      }
    }

    cleanupItemStyles(dragging);
    try { dragging.releasePointerCapture(pointerId); } catch {}
    dragging.removeEventListener('pointermove', onPointerMove);
    dragging = null;

    container.classList.remove('group-dragging');
    container.__selection?.forEach?.(el => el.classList.remove('is-drag-proxy'));

    if (activated) emitReorder(container, itemSelector);

    document.body.classList.remove('dnd-no-select');
    activated = false;
    armed = false;
  }

  function onPointerDown(e) {
    if (!(e.pointerType === 'touch' || e.buttons === 1 || e.button === 0)) return;

    const fromHandle = dragHandle ? e.target.closest(dragHandle) : null;
    const item = (fromHandle ? fromHandle.closest(itemSelector) : e.target.closest(itemSelector));
    if (!item || !container.contains(item)) return;

    if (isInteractiveTarget(e.target)) return;

    dragging = item;
    pointerId = e.pointerId;

    const r = item.getBoundingClientRect();
    startX = e.clientX; startY = e.clientY;
    offsetX = startX - r.left; offsetY = startY - r.top;

    armed = true;
    activated = false;
    downAt = performance.now();

    document.body.classList.add('dnd-no-select');
    try { item.setPointerCapture(pointerId); } catch {}

    item.addEventListener('pointermove', onPointerMove);
    item.addEventListener('pointerup', onPointerUp, { once: true });
    item.addEventListener('pointercancel', onPointerCancel, { once: true });
  }

  function onPointerMove(e) {
    if (!dragging || !armed) return;

    const movedEnough = Math.hypot(e.clientX - startX, e.clientY - startY) >= THRESH_PX;
    const longPress   = (e.pointerType === 'touch') && (performance.now() - downAt >= HOLD_TOUCH_MS);

    if (!activated && (movedEnough || longPress)) {
      activateDrag(dragging);
    }

    if (activated) {
      moveDragged(dragging, e.clientX, e.clientY);
      autoScrollViewport(e.clientX, e.clientY, { edge: scrollEdge, speed: scrollSpeed });

      const el = document.elementFromPoint(e.clientX, e.clientY);
      const target = el ? el.closest(itemSelector) : null;
      const validTarget = target && target !== dragging ? target : null;
      setDropHighlight(validTarget);
      if (!validTarget) { clearHoverTimer(); return; }

      if (mode === 'insert') {
        reorderInsert(validTarget, e.clientX, e.clientY);
      } else {
        if (!hoverTimer || lastTarget !== validTarget) {
          clearHoverTimer();
          hoverTimer = setTimeout(() => {
            placeholder && swapInParent(placeholder, validTarget);
            clearHoverTimer();
          }, hoverDelayMs);
        }
      }
    }
  }

  function onPointerUp() { endDrag(true); }
  function onPointerCancel() { endDrag(false); }

  container.addEventListener('pointerdown', onPointerDown);
}

/* wrappers específicos */
function makeFilesSortable(containerEl, extraOpts = {}){
  attachMergeSelection(containerEl);
  makeSortableGrid(containerEl, '.file-wrapper', {
    mode: 'swap',
    hoverDelayMs: 80,
    scrollEdge: 28,
    scrollSpeed: 18,
    dragPreview: 'none',
    ...extraOpts,
  });
}
function makePagesSortable(containerEl, extraOpts = {}){
  makeSortableGrid(containerEl, '.page-wrapper', {
    mode: 'insert',
    hoverDelayMs: 60,
    dragPreview: 'none',
    ...extraOpts,
  });
}

/* ====================== navegação simples prev/next ====================== */
function initPageControls(){
  document.querySelectorAll('button[id^="btn-prev-"], button[id^="btn-next-"]').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      const parts = btn.id.split('-');
      const prefix = parts.slice(2).join('-');
      const container = document.querySelector(`#preview-${prefix}`);
      if(!container) return;
      const pages = Array.from(container.querySelectorAll('.page-wrapper'));
      const currentIndex = pages.findIndex(p=>!p.classList.contains('hidden'));
      if(currentIndex < 0) return;
      pages[currentIndex].classList.add('hidden');
      const nextIndex = btn.id.includes('prev') ? Math.max(0,currentIndex-1) : Math.min(pages.length-1,currentIndex+1);
      pages[nextIndex].classList.remove('hidden');
    });
  });

  document.querySelectorAll('form[data-prefix]').forEach(form=>{
    form.addEventListener('submit', ()=>{
      const prefix = form.dataset.prefix;
      const container = document.querySelector(`#preview-${prefix}`);
      if(!container) return;
      const selected = Array.from(container.querySelectorAll('.page-wrapper.selected')).map(w=>w.dataset.page);
      const input = document.createElement('input');
      input.type='hidden'; input.name='pages'; input.value=JSON.stringify(selected);
      form.appendChild(input);
    });
  });
}

/* ================ conversão auxiliar (auto-converter não-PDF) ================ */
async function convertFileToPDF(file){
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/convert', {
    method: 'POST',
    body: fd,
    headers: { 'X-CSRFToken': getCSRFToken() }
  });
  if (!res.ok) throw new Error('Falha ao converter: ' + file.name);
  const blob = await res.blob();
  const name = file.name.replace(/\.[^\.]+$/, '') + '.pdf';
  return new File([blob], name, { type: 'application/pdf' });
}

/* ===================== helpers de grid seguros (anti-índice) ===================== */
function getPreviewGridSafe(prefix) {
  try {
    // 1) Se o SES Guard existir, usa em modo "quiet"
    if (window.__SES_GUARD__ && typeof window.__SES_GUARD__.getPreviewGrid === 'function') {
      const g = window.__SES_GUARD__.getPreviewGrid(prefix, 0, { quiet: true });
      if (g) return g;
    }
  } catch (_) {}
  // 2) Tenta seletor explícito
  const explicit = document.querySelector(`#preview-${prefix}`);
  if (explicit) return explicit;
  // 3) Fallback legado (primeira grid conhecida)
  const grids = document.querySelectorAll('.preview-grid, .thumb-grid, [data-grid="preview"]');
  return grids[0] || null;
}

/** Resolve a grid de PÁGINAS para o fluxo do compress:
 *  - Preferir .file-wrapper > .preview-grid (renderFiles criou assim)
 *  - Caso não exista, usar o próprio #preview-compress se já tiver .page-wrapper
 */
function resolveCompressPagesGrid(filesContainer) {
  if (!filesContainer) return null;
  const inner = filesContainer.querySelector('.file-wrapper .preview-grid');
  if (inner) return inner;
  if (filesContainer.matches('.preview-grid') && filesContainer.querySelector('.page-wrapper')) {
    return filesContainer;
  }
  return null;
}

/* ================================== bootstrap ================================== */
document.addEventListener('DOMContentLoaded', ()=>{
  initPageControls();

  const mainEl = document.querySelector('main');
  const setHasPreview = (on) => { if(mainEl) mainEl.classList.toggle('has-preview', !!on); };

  (() => {
    const rc = document.querySelector('#preview-convert');
    if (rc) setHasPreview(!!rc.querySelector('.page-wrapper'));
  })();

  document.querySelectorAll('.dropzone').forEach(dzEl=>{
    const inputEl    = dzEl.querySelector('input[type="file"]');
    const spinnerSel = dzEl.dataset.spinner;
    const btnSel     = dzEl.dataset.action;

    let previewSel = dzEl.dataset.preview || '';
    const isConverter = btnSel && btnSel.includes('convert');
    if(isConverter && !previewSel){ previewSel = '#preview-convert'; }

    const filesContainer    = previewSel ? document.querySelector(previewSel) : null;
    const useFilesContainer = !!filesContainer && /(merge|compress|split)/.test(btnSel);
    let dz;
    let renderToken = 0;

    const exts = dzEl.dataset.extensions
      ? dzEl.dataset.extensions.split(',').map(e=>e.replace(/^\./,''))
      : ['pdf'];
    const multiple = dzEl.dataset.multiple === 'true';

    const btn = document.querySelector(btnSel);
    const setBtnState = (len)=>{ if(btn) btn.disabled = len === 0; };

    async function renderFiles(files){
      const myToken = ++renderToken;
      setBtnState(files.length);
      if(!useFilesContainer) return;

      filesContainer.innerHTML = '';
      if(!files.length) return;

      const isMerge = btnSel && btnSel.includes('merge');

      // Render cooperativo: evita ocupar o main thread por muito tempo
      const TICK_BUDGET_MS = 16;

      for (let idx = 0; idx < files.length; idx++){
        if (myToken !== renderToken) return;

        const startTick = performance.now();
        const file = files[idx];

        const fw = document.createElement('div');
        fw.classList.add('file-wrapper');
        fw.dataset.index = idx;

        const removeAndUnselect = (fwEl) => {
          if (filesContainer.__selection) filesContainer.__selection.delete(fwEl);
          dz.removeFile(idx);
        };

        if (isMerge){
          fw.innerHTML = `
            <div class="file-controls" data-no-drag>
              <span class="file-badge">Arquivo ${idx + 1}</span>
              <button class="remove-file" aria-label="Remover arquivo" data-no-drag>×</button>
            </div>
            <div class="file-name">${file.name}</div>
            <div class="file-cover"></div>
          `;
          fw.querySelector('.remove-file').addEventListener('click', e=>{
            e.stopPropagation();
            removeAndUnselect(fw);
          });
          filesContainer.appendChild(fw);

          const cover = fw.querySelector('.file-cover');
          const ext = getExt(file.name);
          if(!PDF_EXTS.includes(ext)){
            showGenericPreview(file, cover);
            fw.dataset.pageCount = '1';
          } else {
            // Capa + contagem com yield para respirar
            const [count] = await Promise.all([
              getPdfPageCount(file),
              (async () => { await yieldToBrowser(); return renderPdfCover(file, cover, 260); })()
            ]);
            if (myToken !== renderToken) return;
            fw.dataset.pageCount = String(count);
          }
        } else {
          // split/compress: preview por página (pdf.js), com yield antes
          fw.innerHTML = `
            <div class="file-controls" data-no-drag>
              <span class="file-badge">Arquivo ${idx + 1}</span>
              <button class="remove-file" aria-label="Remover arquivo" data-no-drag>×</button>
            </div>
            <div class="file-name">${file.name}</div>
            <div class="preview-grid"></div>
          `;
          fw.querySelector('.remove-file').addEventListener('click', e=>{
            e.stopPropagation();
            removeAndUnselect(fw);
          });
          filesContainer.appendChild(fw);

          const previewGrid = fw.querySelector('.preview-grid');
          const ext = getExt(file.name);

          // Cede o thread antes de inicializar o pdf.js para arquivos grandes
          await yieldToBrowser();

          if(!PDF_EXTS.includes(ext)){
            showGenericPreview(file, previewGrid);
          } else {
            await previewPDF(file, previewGrid, spinnerSel, btnSel);
            if (myToken !== renderToken) return;
            stampFileMetaOnGrid(previewGrid, idx);
            makePagesSortable(previewGrid);
          }
        }

        // Se o trabalho deste arquivo ultrapassou o orçamento de ~1 frame, cedo o controle
        if (performance.now() - startTick > TICK_BUDGET_MS) {
          await yieldToBrowser();
        }
      }

      if (isMerge) {
        filesContainer.classList.add('files-grid');
        makeFilesSortable(filesContainer);
      }
    }

    dz = createFileDropzone({ dropzone: dzEl, input: inputEl, extensions: exts, multiple, onChange: renderFiles });

    const getCurrentFiles = ()=>{
      const dzFiles = (dz && typeof dz.getFiles === 'function') ? dz.getFiles() : [];
      const inputFiles = Array.from(inputEl?.files || []);
      return (dzFiles && dzFiles.length) ? dzFiles : inputFiles;
    };

    setBtnState(getCurrentFiles().length);
    inputEl?.addEventListener('change', ()=>{
      setBtnState(getCurrentFiles().length);
      renderFiles(getCurrentFiles());
    });

    const clearAllState = () => {
      renderToken++;
      try {
        if (dz && typeof dz.clear === 'function') dz.clear();
        else if (dz && typeof dz.getFiles === 'function' && typeof dz.removeFile === 'function') {
          const current = dz.getFiles();
          for (let i = current.length - 1; i >= 0; i--) dz.removeFile(i);
        }
      } catch {}
      if (inputEl) inputEl.value = '';
      if (useFilesContainer && filesContainer) filesContainer.innerHTML = '';

      if (btnSel.includes('convert')) {
        const resultContainer = document.querySelector('#preview-convert');
        if (resultContainer) resultContainer.innerHTML = '';
        const linkWrap = document.getElementById('link-download-container');
        const link = document.getElementById('download-link');
        if (link?.href?.startsWith('blob:')) { try { URL.revokeObjectURL(link.href); } catch {} }
        link?.removeAttribute('href');
        linkWrap?.classList.add('hidden');
        lastConvertedFile = null;
        const mainEl = document.querySelector('main');
        if (mainEl) mainEl.classList.toggle('has-preview', false);
      }

      if (btn) btn.disabled = true;
      resetarProgresso();
    };

    const onClearEvent = () => clearAllState();
    document.addEventListener('gv:clear-files', onClearEvent, { passive: true });
    document.addEventListener('gv:clear-converter', onClearEvent, { passive: true });
    dzEl.addEventListener('gv:teardown', () => {
      document.removeEventListener('gv:clear-files', onClearEvent);
      document.removeEventListener('gv:clear-converter', onClearEvent);
    });

    const clearBtn = document.getElementById('btn-clear-all');
    if (clearBtn && !clearBtn.dataset.bound) {
      clearBtn.addEventListener('click', () => clearAllState(), { passive: true });
      clearBtn.dataset.bound = '1';
    }

    btn?.addEventListener('click', async e=>{
      e.preventDefault();
      let files = getCurrentFiles();
      if(!files.length) return mostrarMensagem('Selecione um arquivo.', 'erro');

      const id = btn.id;

      // ——— CONVERT ———
      if (id.includes('convert')) {
        document.querySelector('section.card')?.classList.add('hidden');
        mostrarLoading(spinnerSel, true);
        resetarProgresso();

        const resultContainer = document.querySelector('#preview-convert');
        if (resultContainer) resultContainer.innerHTML = '';
        const linkWrap = document.getElementById('link-download-container');
        linkWrap?.classList.add('hidden');

        const formData = new FormData();
        formData.append('file', files[0]);

        try {
          const res = await fetch('/api/convert', {
            method: 'POST',
            body: formData,
            headers: { 'X-CSRFToken': getCSRFToken() }
          });
          if (!res.ok) throw new Error('Erro ao converter.');

          const blob = await res.blob();
          mostrarMensagem('Convertido com sucesso!', 'sucesso');
          atualizarProgresso(100);

          const url = URL.createObjectURL(blob);
          const link = document.getElementById('download-link');
          const suggestedName = files[0].name.replace(/\.[^\.]+$/, '') + '.pdf';
          if (link) { link.href = url; link.download = suggestedName; }
          linkWrap?.classList.remove('hidden');

          if (resultContainer) {
            lastConvertedFile = new File([blob], suggestedName, { type: 'application/pdf' });
            await previewPDF(lastConvertedFile, resultContainer, spinnerSel, btnSel);
            makePagesSortable(resultContainer);
            const mainEl = document.querySelector('main');
            if (mainEl) mainEl.classList.toggle('has-preview', !!resultContainer.querySelector('.page-wrapper'));
            resultContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }
        } catch (err) {
          mostrarMensagem(err.message || 'Falha na conversão.', 'erro');
        } finally {
          mostrarLoading(spinnerSel, false);
          document.querySelector('section.card')?.classList.remove('hidden');
          setTimeout(resetarProgresso, 500);
        }
        return;
      }

      // ——— MERGE ———
      if(id.includes('merge')){
        if(!useFilesContainer) return mostrarMensagem('Área de arquivos não encontrada.', 'erro');
        document.querySelector('section.card')?.classList.add('hidden');

        const nonPdfIdxs = files
          .map((f,i)=>({f,i,ext:getExt(f.name)}))
          .filter(x => x.ext !== 'pdf')
          .map(x => x.i);

        if (nonPdfIdxs.length) {
          if (!MERGE_AUTO_CONVERT_NON_PDF) {
            mostrarMensagem('Apenas PDFs no Juntar. Converta antes em "Conversão" ou ative a auto-conversão.', 'erro');
            document.querySelector('section.card')?.classList.remove('hidden');
            return;
          }
          try {
            mostrarMensagem(`Convertendo ${nonPdfIdxs.length} arquivo(s) para PDF...`, 'info');
            const converted = await Promise.all(nonPdfIdxs.map(i => convertFileToPDF(files[i])));
            nonPdfIdxs.forEach((i, k) => { files[i] = converted[k]; });
            mostrarMensagem('Conversão concluída. Iniciando merge...', 'sucesso');
          } catch (err) {
            mostrarMensagem(err.message || 'Falha ao converter arquivos antes do merge.', 'erro');
            document.querySelector('section.card')?.classList.remove('hidden');
            return;
          }
        }

        const wrappers = Array.from(filesContainer.querySelectorAll('.file-wrapper'));

        const formData = new FormData();
        wrappers.forEach(w=>{
          const f = files[w.dataset.index];
          formData.append('files', f, f.name);
        });

        const pagesMap = await Promise.all(
          wrappers.map(async w => {
            const grid = w.querySelector('.preview-grid');
            if (grid) {
              const { pages } = collectPagesRotsCropsFromGrid(grid);
              return pages;
            }
            const count = Number(w.dataset.pageCount || '1');
            return Array.from({length: count}, (_,i)=> i+1);
          })
        );
        const rotations = wrappers.map(w => (w.querySelector('.preview-grid') ? collectPagesRotsCropsFromGrid(w.querySelector('.preview-grid')).rotations : []));
        const crops     = wrappers.map(w => (w.querySelector('.preview-grid') ? collectPagesRotsCropsFromGrid(w.querySelector('.preview-grid')).crops     : []));

        formData.append('pagesMap', JSON.stringify(pagesMap));
        formData.append('rotations', JSON.stringify(rotations));
        formData.append('crops', JSON.stringify(crops));

        formData.append('flatten', document.querySelector('#opt-flatten')?.checked ? 'true' : 'false');
        formData.append('pdf_settings', '/ebook');

        try{
          const res = await fetch('/api/merge', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken(), 'Accept': 'application/pdf' },
            body: formData
          });
          if(!res.ok) throw new Error('Falha ao juntar PDFs.');
          const blob = await res.blob();

          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'merged.pdf';
          a.click();

          filesContainer.innerHTML = '';
          await renderFiles([new File([blob], 'merged.pdf', { type: 'application/pdf' })]);
          mostrarMensagem('Juntado com sucesso!', 'sucesso');
        }catch(err){
          mostrarMensagem(err.message || 'Erro no merge.', 'erro');
        }finally{
          document.querySelector('section.card')?.classList.remove('hidden');
        }
        return;
      }

      // ——— SPLIT ———
      if(id.includes('split')){
        if(!useFilesContainer) return mostrarMensagem('Área de arquivos não encontrada.', 'erro');
        document.querySelector('section.card')?.classList.add('hidden');

        const grid = filesContainer.querySelector('.preview-grid');
        const { pages, rotations, crops } = collectPagesRotsCropsFromGrid(grid);
        if(!pages.length) return mostrarMensagem('Selecione ao menos uma página.', 'erro');

        const formData = new FormData();
        formData.append('file', files[0]);
        formData.append('pages', JSON.stringify(pages));
        formData.append('rotations', JSON.stringify(rotations));
        if (crops.length) formData.append('modificacoes', JSON.stringify({ crops }));

        xhrRequest('/api/split', formData, blob=>{
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'split.pdf';
          a.click();
          mostrarMensagem('Dividido com sucesso!', 'sucesso');
          document.querySelector('section.card')?.classList.remove('hidden');
        });
        return;
      }

      // ——— COMPRESS ———
      if(id.includes('compress')){
        if(!useFilesContainer) return mostrarMensagem('Área de arquivos não encontrada.', 'erro');
        document.querySelector('section.card')?.classList.add('hidden');

        try {
          // Em /compress usamos upload único; ainda assim, serializamos para futuro multi.
          for (let i = 0; i < files.length; i++) {
            const file = files[i];

            // Resolve o grid de páginas de forma resiliente (sem índice)
            const container = getPreviewGridSafe('compress');      // #preview-compress (container externo)
            const grid = resolveCompressPagesGrid(container)        // .file-wrapper > .preview-grid
                      || container?.querySelector?.('.preview-grid') // fallback
                      || container;                                  // último fallback (se já renderizou direto)

            if (!grid || !grid.querySelector('.page-wrapper')) {
              // Grid ainda não montou; não logamos warn (ruído). Apenas pula.
              continue;
            }

            const { pages, rotations, crops } = collectPagesRotsCropsFromGrid(grid);

            try {
              console.debug('[compress] iniciando', { name: file?.name, pages, rotations, cropsCount: crops.length });
              await compressFile(file, rotations, undefined, {
                pages,
                modificacoes: (crops.length ? { crops } : undefined)
              });
              mostrarMensagem(`Arquivo comprimido: ${file?.name || '(sem nome)'}`, 'sucesso');
            } catch (err) {
              console.error('[compress] erro ao comprimir', file?.name, err);
              mostrarMensagem(err?.message || `Erro ao comprimir: ${file?.name || ''}`, 'erro');
            }

            await yieldToBrowser();
          }
        } finally {
          document.querySelector('section.card')?.classList.remove('hidden');
        }
        return;
      }
    });
  });

  // DOWNLOAD INTELIGENTE (converter)
  document.addEventListener('click', (e) => {
    const link = e.target.closest('#download-link');
    if (!link) return;
    const resultContainer = document.querySelector('#preview-convert');
    if (!resultContainer) return;
    e.preventDefault();

    const pages = getSelectedPages(resultContainer, true);
    if (!pages.length) { window.open(link.href, '_blank'); return; }

    const rotations = pages.map(pg => {
      const el = resultContainer.querySelector(`.page-wrapper[data-page="${pg}"]`);
      return Number(el?.dataset.rotation) || 0;
    });
    const crops = [];
    pages.forEach(pg => {
      const el = resultContainer.querySelector(`.page-wrapper[data-page="${pg}"]`);
      const box = getCropBoxAbs(el);
      if (box) crops.push({ page: pg, box });
    });

    if (!lastConvertedFile) { window.open(link.href, '_blank'); return; }

    const formData = new FormData();
    formData.append('file', lastConvertedFile);
    formData.append('pages', JSON.stringify(pages));
    formData.append('rotations', JSON.stringify(rotations));
    if (crops.length) formData.append('modificacoes', JSON.stringify({ crops }));

    mostrarLoading('#spinner-convert', true);
    xhrRequest('/api/split', formData, (blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = lastConvertedFile.name.replace(/\.pdf$/i, '') + '-selecionadas.pdf';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 3000);
      mostrarLoading('#spinner-convert', false);
      mostrarMensagem('Páginas selecionadas baixadas!', 'sucesso');
    });
  }, { passive: false });

  window.addEventListener('beforeunload', () => {
    const link = document.getElementById('download-link');
    if (link?.href?.startsWith('blob:')) { try { URL.revokeObjectURL(link.href); } catch {} }
  });
});