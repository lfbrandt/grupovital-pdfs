// app/static/js/preview.js

// largura base em pixels de cada miniatura do PDF
const THUMB_WIDTH = 150;

// número de páginas para renderizar imediatamente
const INITIAL_BATCH = 3;

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

// Renderiza uma página do PDF em um canvas existente (com rotação)
async function renderPage(pdf, pageNumber, containerEl, rotation = 0) {
  const t0 = performance.now();
  const page = await pdf.getPage(pageNumber);

  // Cria viewport com rotação aplicada
  const baseViewport = page.getViewport({ scale: 1, rotation });
  const dpr = window.devicePixelRatio || 1;
  const scale = (THUMB_WIDTH * dpr) / baseViewport.width;
  const vp = page.getViewport({ scale, rotation });

  const canvas = containerEl.querySelector(`canvas[data-page="${pageNumber}"]`);
  const ctx = canvas.getContext('2d');
  canvas.width  = Math.floor(vp.width);
  canvas.height = Math.floor(vp.height);
  canvas.style.width  = `${THUMB_WIDTH}px`;
  canvas.style.height = `${(vp.height / dpr).toFixed(0)}px`;

  await page.render({ canvasContext: ctx, viewport: vp }).promise;
  const t1 = performance.now();
  console.log(`renderPage ${pageNumber}: ${(t1 - t0).toFixed(2)}ms`);
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

  initPageSelection(containerEl);
  containerEl.innerHTML = '';
  spinnerEl?.classList.remove('hidden');
  btnEl.disabled = true;

  const arrayBuf = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument(new Uint8Array(arrayBuf)).promise;

  for (let i = 1; i <= pdf.numPages; i++) {
    const wrap = document.createElement('div');
    wrap.className = 'page-wrapper';
    wrap.dataset.page     = i;
    wrap.dataset.rotation = '0';

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
    rotateBtn.addEventListener('click', async e => {
      e.stopPropagation();
      const rot = (parseInt(wrap.dataset.rotation, 10) + 90) % 360;
      wrap.dataset.rotation = rot.toString();
      // Re-renderiza o canvas com o ângulo atual
      await renderPage(pdf, i, containerEl, rot);
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

  // Renderiza batch inicial com rotação real
  const batchStart = performance.now();
  const initial = Math.min(pdf.numPages, INITIAL_BATCH);
  for (let i = 1; i <= initial; i++) {
    const wrap = containerEl.querySelector(`.page-wrapper[data-page="${i}"]`);
    const rot = parseInt(wrap.dataset.rotation, 10);
    await renderPage(pdf, i, containerEl, rot);
  }
  const batchEnd = performance.now();
  console.log(`initialBatchRender: ${(batchEnd - batchStart).toFixed(2)}ms`);

  // Lazy-load das demais páginas, respeitando rotação
  if (pdf.numPages > INITIAL_BATCH) {
    const observer = new IntersectionObserver((entries, obs) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          const wrap = entry.target;
          const pg   = Number(wrap.dataset.page);
          obs.unobserve(wrap);
          const lazyStart = performance.now();
          const rot = parseInt(wrap.dataset.rotation, 10);
          renderPage(pdf, pg, containerEl, rot)
            .then(() => {
              const lazyEnd = performance.now();
              console.log(`lazyRenderPage ${pg}: ${(lazyEnd - lazyStart).toFixed(2)}ms`);
            });
        }
      });
    }, { root: containerEl, rootMargin: '200px', threshold: 0.1 });

    Array.from(containerEl.querySelectorAll('.page-wrapper'))
      .slice(INITIAL_BATCH)
      .forEach(wrap => observer.observe(wrap));
  }

  spinnerEl?.classList.add('hidden');
  btnEl.disabled = false;
}