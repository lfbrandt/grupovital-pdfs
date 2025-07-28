// app/static/js/preview.js

// largura base em pixels de cada miniatura do PDF
const THUMB_WIDTH = 150;

// número de páginas para renderizar imediatamente
const INITIAL_BATCH = 5;

// Inicia o Set de páginas para um container específico
export function initPageSelection(containerEl) {
  containerEl.selectedPages = new Set();
}

// Retorna as páginas selecionadas, opcionalmente mantendo a ordem visual
export function getSelectedPages(containerEl, keepOrder = false) {
  if (!containerEl?.selectedPages) return [];
  const pages = Array.from(containerEl.selectedPages);
  if (!keepOrder) {
    return pages.sort((a, b) => a - b);
  }
  const order = Array.from(containerEl.querySelectorAll('.page-wrapper'))
    .map(el => Number(el.dataset.page));
  return order.filter(p => pages.includes(p));
}

// Alterna seleção de uma página
function togglePageSelection(containerEl, pg, wrapper) {
  if (containerEl.selectedPages.has(pg)) {
    containerEl.selectedPages.delete(pg);
    wrapper.classList.remove('selected');
  } else {
    containerEl.selectedPages.add(pg);
    wrapper.classList.add('selected');
  }
}

// Remove a miniatura de uma página
function removePage(containerEl, pageNum, wrapper) {
  containerEl.selectedPages.delete(pageNum);
  wrapper.remove();
}

// Renderiza uma página do PDF em um canvas existente
async function renderPage(pdf, pageNumber, containerEl, rotation = 0) {
  const page = await pdf.getPage(pageNumber);
  const baseViewport = page.getViewport({ scale: 1, rotation });
  const dpr = window.devicePixelRatio || 1;
  const scale = (THUMB_WIDTH * dpr) / baseViewport.width;
  const vp = page.getViewport({ scale, rotation });
  const canvas = containerEl.querySelector(`canvas[data-page="${pageNumber}"]`);
  canvas.width  = Math.floor(vp.width);
  canvas.height = Math.floor(vp.height);
  canvas.style.width  = `${THUMB_WIDTH}px`;
  canvas.style.height = `${(vp.height / dpr).toFixed(0)}px`;
  await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
}

/**
 * Faz o preview de um PDF, gerando miniaturas com rotação e exclusão.
 */
export async function previewPDF(file, container, spinnerSel, btnSel) {
  const containerEl = typeof container === 'string'
    ? document.querySelector(container)
    : container;
  const spinnerEl = document.querySelector(spinnerSel);
  const btnEl     = document.querySelector(btnSel);
  if (!containerEl || !btnEl) return;

  // prepara seleção e interface
  initPageSelection(containerEl);
  containerEl.innerHTML = '';
  spinnerEl?.classList.remove('hidden');
  btnEl.disabled = true;

  // carrega PDF via PDF.js
  const arrayBuf = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument(new Uint8Array(arrayBuf)).promise;

  // cria wrappers para cada página
  for (let i = 1; i <= pdf.numPages; i++) {
    const wrap = document.createElement('div');
    wrap.className = 'page-wrapper';
    wrap.dataset.page     = i;
    wrap.dataset.rotation = '0';

    // controles de rotação e remoção
    const controls = document.createElement('div');
    controls.className = 'file-controls';

    const removeBtn = document.createElement('button');
    removeBtn.className = 'remove-file';
    removeBtn.title = 'Remover página';
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', e => {
      e.stopPropagation();
      removePage(containerEl, i, wrap);
      if (!containerEl.querySelector('.page-wrapper')) btnEl.disabled = true;
    });

    const rotateBtn = document.createElement('button');
    rotateBtn.className = 'rotate-page';
    rotateBtn.title = 'Girar página';
    rotateBtn.textContent = '⟳';
    rotateBtn.addEventListener('click', e => {
      e.stopPropagation();
      const rot = (parseInt(wrap.dataset.rotation, 10) + 90) % 360;
      wrap.dataset.rotation = rot.toString();
      wrap.style.transform = `rotate(${rot}deg)`;
    });

    controls.append(removeBtn, rotateBtn);

    const badge = document.createElement('div');
    badge.className = 'page-badge';
    badge.textContent = `Pg ${i}`;
    const canvas = document.createElement('canvas');
    canvas.dataset.page = i;

    wrap.append(controls, badge, canvas);
    wrap.addEventListener('click', () => togglePageSelection(containerEl, i, wrap));
    containerEl.appendChild(wrap);
    containerEl.selectedPages.add(i);
    wrap.classList.add('selected');
  }

  // renderiza batch inicial
  const initial = Math.min(pdf.numPages, INITIAL_BATCH);
  for (let i = 1; i <= initial; i++) {
    await renderPage(pdf, i, containerEl, 0);
  }

  // observer para lazy-load das demais páginas
  if (pdf.numPages > INITIAL_BATCH) {
    const observer = new IntersectionObserver((entries, obs) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const wrap = entry.target;
          const pageNum = Number(wrap.dataset.page);
          obs.unobserve(wrap);
          renderPage(pdf, pageNum, containerEl, 0);
        }
      });
    }, { root: containerEl, rootMargin: '200px', threshold: 0.1 });

    Array.from(containerEl.querySelectorAll('.page-wrapper'))
      .slice(INITIAL_BATCH)
      .forEach(wrap => observer.observe(wrap));
  }

  // finaliza loading
  spinnerEl?.classList.add('hidden');
  btnEl.disabled = false;
}
