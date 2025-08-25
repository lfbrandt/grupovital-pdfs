// app/static/js/preview.js
import { openPageEditor } from './page-editor.js';
import { getCSRFToken, xhrRequest } from './utils.js';

/* ------------------------------------------------------------------
   Helpers
-------------------------------------------------------------------*/
export function isPdfFile(file) {
  const name = (file?.name || '').toLowerCase();
  const type = (file?.type || '').toLowerCase();
  return name.endsWith('.pdf') || type === 'application/pdf';
}

/* ------------------------------------------------------------------
   Preview leve via servidor (miniatura da 1ª página)
   - silencioso para não-PDF por padrão
-------------------------------------------------------------------*/
/**
 * Gera miniatura (1ª página) no servidor e seta no <img>.
 * @param {File} file
 * @param {HTMLImageElement} imgEl
 * @param {{silentNonPdf?: boolean}} options
 */
export async function previewThumb(file, imgEl, { silentNonPdf = true } = {}) {
  if (!file || !imgEl) return;

  // Se não for PDF: limpa imagem e sai em silêncio (ou avisa se quiser)
  if (!isPdfFile(file)) {
    if (silentNonPdf) {
      delete imgEl.dataset.thumbId;
      imgEl.removeAttribute('src');
      imgEl.alt = 'Sem miniatura';
      imgEl.setAttribute('draggable', 'false');
      return;
    } else {
      return;
    }
  }

  try {
    const form = new FormData();
    form.append('file', file);

    const headers = { 'X-CSRFToken': getCSRFToken() };

    const resp = await xhrRequest('/api/preview', {
      method: 'POST',
      body: form,
      headers,
    });

    if (!resp || !resp.thumb_url) {
      throw new Error('Resposta inválida da API de preview.');
    }

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
   DAQUI PRA BAIXO: preview avançado (pdf.js)
================================================================== */

// ===== Config =====
const MIN_WIDTH = 240;           // largura mínima do canvas (ajusta pelo container)
const INITIAL_BATCH = 3;         // páginas renderizadas imediatamente

// ===== Seleção de páginas =====
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

// ===== Toolbar do preview de resultado =====
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
  });

  const btnClearSel = document.createElement('button');
  btnClearSel.type = 'button';
  btnClearSel.textContent = 'Limpar seleção';
  btnClearSel.setAttribute('data-no-drag', '');
  btnClearSel.addEventListener('click', () => {
    containerEl.selectedPages = new Set();
    containerEl.querySelectorAll('.page-wrapper').forEach(w => w.setAttribute('aria-selected', 'false'));
    setBtnDisabled(true);
  });

  toolbar.append(btnSelectAll, btnClearSel);
  containerEl.before(toolbar);
}

// ===== Cálculo de largura alvo =====
function getTargetWidth(containerEl, pdfWidth) {
  const parent = containerEl.closest('.upload-preview-wrapper') || containerEl.parentElement || containerEl;
  const cw = parent.clientWidth || pdfWidth;
  const target = Math.max(MIN_WIDTH, Math.min(420, cw - 24));
  return target;
}

// ===== Renderização: rotação + crop =====
async function renderPage(pdf, pageNumber, containerEl, rotation = 0) {
  const page = await pdf.getPage(pageNumber);

  const unrot = page.getViewport({ scale: 1, rotation: 0 });
  const baseViewport = page.getViewport({ scale: 1, rotation });
  const dpr = window.devicePixelRatio || 1;
  const targetW = getTargetWidth(containerEl, baseViewport.width);
  const scale = (targetW * dpr) / baseViewport.width;
  const vp = page.getViewport({ scale, rotation });

  const wrap = containerEl.querySelector(`.page-wrapper[data-page="${pageNumber}"]`);
  const canvas = containerEl.querySelector(`canvas[data-page="${pageNumber}"]`);
  const ctx = canvas.getContext('2d');

  if (wrap && (!wrap.dataset.pdfW || !wrap.dataset.pdfH)) {
    wrap.dataset.pdfW = String(unrot.width);
    wrap.dataset.pdfH = String(unrot.height);
  }

  const cropData = wrap?.dataset?.crop ? JSON.parse(wrap.dataset.crop) : null;
  if (!cropData) {
    canvas.width  = Math.floor(vp.width);
    canvas.height = Math.floor(vp.height);
    canvas.style.width = Math.min(targetW, vp.width / dpr) + 'px';
    canvas.style.height = 'auto';
    await page.render({ canvasContext: ctx, viewport: vp, intent: 'print' }).promise;
    return;
  }

  const full = document.createElement('canvas');
  full.width = Math.floor(vp.width);
  full.height = Math.floor(vp.height);
  const fctx = full.getContext('2d', { alpha: false });
  await page.render({ canvasContext: fctx, viewport: vp, intent: 'print' }).promise;

  const W = unrot.width, H = unrot.height;
  const { x0, y0, x1, y1 } = cropData;
  const rectPdf = [x0 * W, y0 * H, x1 * W, y1 * H];

  const [vx0, vy0, vx1, vy1] = vp.convertToViewportRectangle(rectPdf);
  const cx = Math.min(vx0, vx1);
  const cy = Math.min(vy0, vy1);
  const cw = Math.abs(vx1 - vx0);
  const ch = Math.abs(vy1 - vy0);

  canvas.width  = Math.max(1, Math.floor(cw));
  canvas.height = Math.max(1, Math.floor(ch));
  canvas.style.width  = Math.min(targetW, cw / dpr) + 'px';
  canvas.style.height = 'auto';

  const ctx2 = canvas.getContext('2d');
  ctx2.drawImage(full, cx, cy, cw, ch, 0, 0, canvas.width, canvas.height);
}

// ===== Remover página do preview =====
function removePage(containerEl, pageNum, wrap) {
  wrap.remove();
  containerEl.selectedPages?.delete(pageNum);
}

// ===== Preview principal avançado =====
export async function previewPDF(file, container, spinnerSel, btnSel) {
  const containerEl = typeof container === 'string'
    ? document.querySelector(container)
    : container;
  if (!containerEl) return;

  const spinnerEl = spinnerSel ? document.querySelector(spinnerSel) : null;
  const btnEl     = btnSel     ? document.querySelector(btnSel)     : null;
  const isResult  = containerEl.dataset.mode === 'result';

  const setSpin = (on) => {
    if (!spinnerEl) return;
    spinnerEl.classList.toggle('hidden', !on);
    spinnerEl.setAttribute('aria-hidden', on ? 'false' : 'true');
  };
  const setBtnDisabled = (on) => { if (btnEl) btnEl.disabled = !!on; };

  try {
    initPageSelection(containerEl);
    containerEl.innerHTML = '';
    setSpin(true);
    setBtnDisabled(true);

    const arrayBuf = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument(new Uint8Array(arrayBuf)).promise;

    if (isResult) addResultToolbar(containerEl, setBtnDisabled);

    for (let i = 1; i <= pdf.numPages; i++) {
      const wrap = document.createElement('div');
      wrap.classList.add('page-wrapper', 'page-thumb');
      wrap.dataset.page = i;
      wrap.dataset.pageId = String(i);
      wrap.dataset.rotation = '0';
      wrap.setAttribute('role', 'option');
      wrap.setAttribute('aria-selected', 'true');
      wrap.tabIndex = 0;

      // evita iniciar DnD ao clicar na barra de controles (garantia local)
      wrap.addEventListener('pointerdown', (e) => {
        if (e.target.closest('[data-no-drag]')) e.stopPropagation();
      }, true);

      // --- Controles (com data-no-drag) ---
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
        await renderPage(pdf, i, containerEl, rot);
      });

      const cropBtn = document.createElement('button');
      cropBtn.type = 'button';
      cropBtn.classList.add('crop-page');
      cropBtn.title = 'Recortar página';
      cropBtn.setAttribute('aria-label', `Recortar página ${i}`);
      cropBtn.setAttribute('data-no-drag', '');
      cropBtn.textContent = '✂';
      cropBtn.addEventListener('click', async e => {
        e.stopPropagation();

        const currentRotation = parseInt(wrap.dataset.rotation || '0', 10);
        const currentCropNorm = wrap.dataset.crop ? JSON.parse(wrap.dataset.crop) : null;

        try {
          const result = await openPageEditor({
            pdf,
            pageNumber: i,
            rotation: currentRotation,
            cropNorm: currentCropNorm
          });

          wrap.dataset.rotation = String(result.rotation);
          if (result.cropNorm) {
            wrap.dataset.crop    = JSON.stringify(result.cropNorm);
            wrap.dataset.cropAbs = JSON.stringify(result.cropAbs);
            cropBtn.textContent  = '✂ ✓';
          } else {
            delete wrap.dataset.crop;
            delete wrap.dataset.cropAbs;
            cropBtn.textContent  = '✂';
          }
          wrap.dataset.pdfW = String(result.pdfW);
          wrap.dataset.pdfH = String(result.pdfH);

          await renderPage(pdf, i, containerEl, result.rotation);
        } catch {
          // cancelado pelo usuário — não faz nada
        }
      });

      controls.append(removeBtn, rotateBtn, cropBtn);

      const badge = document.createElement('div');
      badge.classList.add('page-badge');
      badge.textContent = `Pg ${i}`;
      badge.setAttribute('data-no-drag', '');

      const canvas = document.createElement('canvas');
      canvas.dataset.page = i;
      canvas.setAttribute('draggable', 'false');

      wrap.addEventListener('click', () => {
        if (!containerEl.selectedPages) return;
        if (wrap.getAttribute('aria-selected') === 'true') {
          wrap.setAttribute('aria-selected', 'false');
          containerEl.selectedPages.delete(i);
        } else {
          wrap.setAttribute('aria-selected', 'true');
          containerEl.selectedPages.add(i);
        }
        setBtnDisabled(containerEl.selectedPages.size === 0);
      });
      wrap.addEventListener('keydown', (e) => {
        if (e.key === ' ' || e.key === 'Enter') {
          e.preventDefault();
          wrap.click();
        }
      });

      wrap.append(controls, badge, canvas);
      containerEl.appendChild(wrap);
    }

    const initial = Math.min(pdf.numPages, INITIAL_BATCH);
    for (let i = 1; i <= initial; i++) {
      const wrap = containerEl.querySelector(`.page-wrapper[data-page="${i}"]`);
      const rot = parseInt(wrap.dataset.rotation, 10);
      await renderPage(pdf, i, containerEl, rot);
    }

    if (pdf.numPages > INITIAL_BATCH) {
      const observer = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            const wrap = entry.target;
            const pg   = Number(wrap.dataset.page);
            obs.unobserve(wrap);
            const rot = parseInt(wrap.dataset.rotation, 10);
            renderPage(pdf, pg, containerEl, rot);
          }
        });
      }, { rootMargin: '120px 0px 120px 0px' });

      containerEl.querySelectorAll('.page-wrapper')
        .forEach(wrap => observer.observe(wrap));
    }

    containerEl.dispatchEvent(new CustomEvent('preview:ready'));
  } catch (err) {
    console.error('[previewPDF] erro renderizando', err);
    containerEl.innerHTML = '<div class="preview-error">Falha ao gerar preview do PDF</div>';
    throw err;
  } finally {
    if (typeof spinnerSel !== 'undefined') {
      const spinnerEl = spinnerSel ? document.querySelector(spinnerSel) : null;
      if (spinnerEl) {
        spinnerEl.classList.add('hidden');
        spinnerEl.setAttribute('aria-hidden', 'true');
      }
    }
    const btnEl = btnSel ? document.querySelector(btnSel) : null;
    if (btnEl) btnEl.disabled = false;
  }
}

/* ================================================================
   (Fase 2) coletar payload e bindar Exportar → /api/organize
   - NÃO envia crop nesta fase (só pages + rotations)
================================================================ */
export function collectOrganizePayload(containerEl) {
  if (!containerEl) throw new Error('collectOrganizePayload: container ausente');

  const wrappers = Array.from(containerEl.querySelectorAll('.page-wrapper'));
  const pages = [];
  const rotations = {};

  wrappers.forEach(wrap => {
    const selected = wrap.getAttribute('aria-selected') === 'true';
    if (!selected) return;
    const idx1b = parseInt(wrap.dataset.page, 10);
    if (!Number.isInteger(idx1b)) return;
    pages.push(idx1b);
    const rot = parseInt(wrap.dataset.rotation || '0', 10) || 0;
    if (rot) rotations[String(idx1b)] = rot;
  });

  return { pages, rotations };
}

/**
 * Liga o botão de exportar para enviar o PDF MÃE + payload ao backend.
 * @param {{
 *   containerSel?: string,
 *   buttonSel?: string,
 *   inputSel?: string,
 *   endpoint?: string
 * }} opts
 */
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

      if (!resp.ok) {
        const msg = await resp.text();
        alert(msg || 'Falha ao exportar.');
        return;
      }

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