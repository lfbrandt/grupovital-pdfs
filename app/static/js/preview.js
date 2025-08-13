// app/static/js/preview.js

// ===== Config =====
const MIN_WIDTH = 240;           // largura mínima (ajusta pelo container)
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
    .map(el => Number(el.dataset.page));
  return order.filter(p => pages.includes(p));
}

function togglePageSelection(containerEl, pg, wrapper) {
  const selected = containerEl.selectedPages.has(pg);
  if (selected) {
    containerEl.selectedPages.delete(pg);
    wrapper.classList.remove('selected');
    wrapper.setAttribute('aria-selected', 'false');
  } else {
    containerEl.selectedPages.add(pg);
    wrapper.classList.add('selected');
    wrapper.setAttribute('aria-selected', 'true');
  }
}

function removePage(containerEl, pageNum, wrapper) {
  containerEl.selectedPages.delete(pageNum);
  wrapper.remove();
}

// ===== Render =====
function getTargetWidth(containerEl, baseViewportWidth) {
  const cw = containerEl.clientWidth || 420;
  const target = Math.max(MIN_WIDTH, cw - 16);      // margem/scroll
  return Math.min(target, baseViewportWidth * 1.75); // limita upscale
}

async function renderPage(pdf, pageNumber, containerEl, rotation = 0) {
  const t0 = performance.now();
  const page = await pdf.getPage(pageNumber);

  const baseViewport = page.getViewport({ scale: 1, rotation });
  const dpr = window.devicePixelRatio || 1;
  const targetW = getTargetWidth(containerEl, baseViewport.width);
  const scale = (targetW * dpr) / baseViewport.width;
  const vp = page.getViewport({ scale, rotation });

  const canvas = containerEl.querySelector(`canvas[data-page="${pageNumber}"]`);
  const ctx = canvas.getContext('2d');

  canvas.width  = Math.floor(vp.width);
  canvas.height = Math.floor(vp.height);
  canvas.style.width = Math.min(targetW, vp.width / dpr) + 'px';
  canvas.style.height = 'auto';

  await page.render({ canvasContext: ctx, viewport: vp }).promise;
  const t1 = performance.now();
  console.log(`renderPage ${pageNumber}: ${(t1 - t0).toFixed(2)}ms`);
}

// ===== UI helpers =====
function addResultToolbar(containerEl, setBtnDisabled) {
  const bar = document.createElement('div');
  bar.className = 'preview-toolbar';

  // Limpar tudo
  const clearBtn = document.createElement('button');
  clearBtn.type = 'button';
  clearBtn.className = 'btn-icon btn-clear-all';
  clearBtn.title = 'Limpar todos os arquivos';
  clearBtn.textContent = '×';
  clearBtn.addEventListener('click', () => {
    containerEl.innerHTML = '';
    containerEl.selectedPages?.clear?.();

    const linkWrap = document.getElementById('link-download-container');
    const linkEl   = document.getElementById('download-link');
    if (linkEl?.href?.startsWith('blob:')) URL.revokeObjectURL(linkEl.href);
    linkEl?.removeAttribute('href');
    linkWrap?.classList.add('hidden');

    document.dispatchEvent(new CustomEvent('gv:clear-converter'));
    setBtnDisabled(true);
  });

  bar.append(clearBtn);
  containerEl.appendChild(bar);
}

// ===== Preview principal =====
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
    if (!containerEl.classList.contains('preview-grid')) {
      containerEl.classList.add('preview-grid');
    }

    initPageSelection(containerEl);
    containerEl.innerHTML = '';
    setSpin(true);
    setBtnDisabled(true);

    const arrayBuf = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument(new Uint8Array(arrayBuf)).promise;

    // toolbar (apenas no preview do resultado)
    if (isResult) addResultToolbar(containerEl, setBtnDisabled);

    // wrappers + controles por página
    for (let i = 1; i <= pdf.numPages; i++) {
      const wrap = document.createElement('div');
      // 👇 compatível com DnD: vira também uma "thumb"
      wrap.classList.add('page-wrapper', 'page-thumb');
      wrap.dataset.page = i;                  // índice lógico (origem)
      wrap.dataset.pageId = String(i);        // usado pelo DnD
      wrap.dataset.rotation = '0';
      wrap.setAttribute('role', 'option');
      wrap.setAttribute('aria-selected', 'true');
      wrap.tabIndex = 0;

      const controls = document.createElement('div');
      controls.classList.add('file-controls');

      // X -> remove só esta página
      const removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.classList.add('remove-file');
      removeBtn.title = 'Remover página';
      removeBtn.textContent = '×';
      removeBtn.addEventListener('click', e => {
        e.stopPropagation();
        removePage(containerEl, i, wrap);
        if (!containerEl.querySelector('.page-wrapper')) setBtnDisabled(true);
      });

      // ⟳ -> rotaciona esta página
      const rotateBtn = document.createElement('button');
      rotateBtn.type = 'button';
      rotateBtn.classList.add('rotate-page');
      rotateBtn.title = 'Girar página';
      rotateBtn.textContent = '⟳';
      rotateBtn.addEventListener('click', async e => {
        e.stopPropagation();
        const rot = (parseInt(wrap.dataset.rotation, 10) + 90) % 360;
        wrap.dataset.rotation = rot.toString();
        await renderPage(pdf, i, containerEl, rot);
      });

      controls.append(removeBtn, rotateBtn);

      const badge = document.createElement('div');
      badge.classList.add('page-badge');
      badge.textContent = `Pg ${i}`;

      const canvas = document.createElement('canvas');
      canvas.dataset.page = i;
      canvas.classList.add('thumb-canvas');

      wrap.append(controls, badge, canvas);
      wrap.addEventListener('click', () => togglePageSelection(containerEl, i, wrap));
      containerEl.appendChild(wrap);
      containerEl.selectedPages.add(i);
      wrap.classList.add('selected');
    }

    // render inicial
    const initial = Math.min(pdf.numPages, INITIAL_BATCH);
    for (let i = 1; i <= initial; i++) {
      const wrap = containerEl.querySelector(`.page-wrapper[data-page="${i}"]`);
      const rot = parseInt(wrap.dataset.rotation, 10);
      await renderPage(pdf, i, containerEl, rot);
    }

    // lazy-load das demais páginas
    if (pdf.numPages > INITIAL_BATCH) {
      const observer = new IntersectionObserver((entries, obs) => {
        entries.forEach(entry => {
          if (entry.isIntersecting) {
            const wrap = entry.target;
            const pg   = Number(wrap.dataset.page);
            obs.unobserve(wrap);
            const rot = parseInt(wrap.dataset.rotation, 10);
            renderPage(pdf, pg, containerEl, rot)
              .then(() => console.log(`lazyRenderPage ${pg}`));
          }
        });
      }, { root: containerEl, rootMargin: '200px', threshold: 0.1 });

      Array.from(containerEl.querySelectorAll('.page-wrapper'))
        .slice(INITIAL_BATCH)
        .forEach(wrap => observer.observe(wrap));
    }

    // informa quem quiser ouvir (e.g., módulos DnD) que o preview está pronto
    containerEl.dispatchEvent(new CustomEvent('preview:ready'));
  } catch (err) {
    console.error('[previewPDF] erro renderizando', err);
    containerEl.innerHTML = '<div class="preview-error">Falha ao gerar preview do PDF</div>';
    throw err;
  } finally {
    setSpin(false);
    setBtnDisabled(false);
  }
}