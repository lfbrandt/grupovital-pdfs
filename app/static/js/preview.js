// largura base em pixels de cada miniatura do PDF
const THUMB_WIDTH = 150;

// Inicia o Set de páginas para um container específico
export function initPageSelection(containerEl) {
  containerEl.selectedPages = new Set();
}

export function getSelectedPages(containerEl, keepOrder = false) {
  if (!containerEl || !containerEl.selectedPages) return [];
  const pages = Array.from(containerEl.selectedPages);
  if (!keepOrder) {
    return pages.sort((a, b) => a - b);
  }
  const order = Array.from(containerEl.querySelectorAll('.page-wrapper')).map(
    el => Number(el.dataset.page)
  );
  return order.filter(p => pages.includes(p));
}


/**
 * Renderiza as páginas de um PDF dentro do container fornecido.
 *
 * @param {File}        file        Arquivo PDF
 * @param {string|Node} container   Seletor CSS ou elemento onde inserir as páginas
 * @param {string}      spinnerSel  Seletor do overlay de loading
 * @param {string}      btnSel      Seletor do botão de ação
 */
export async function previewPDF(file, container, spinnerSel, btnSel) {
  const containerEl =
    typeof container === 'string'
      ? document.querySelector(container)
      : container;
  const spinnerEl = document.querySelector(spinnerSel);
  const btnEl     = document.querySelector(btnSel);
  if (!containerEl || !spinnerEl || !btnEl) return;

  // armazena seleção própria do container
  initPageSelection(containerEl);

  const pagesContainer = document.createElement('div');
  pagesContainer.classList.add('pages-container');
  containerEl.appendChild(pagesContainer);

  spinnerEl.style.display = 'flex';
  btnEl.disabled = true;

  const arrayBuf = await file.arrayBuffer();
  const pdf      = await pdfjsLib.getDocument(new Uint8Array(arrayBuf)).promise;

  for (let i = 1; i <= pdf.numPages; i++) {
    const wrapper = document.createElement('div');
    wrapper.classList.add('page-wrapper');
    wrapper.dataset.page = i;
    wrapper.dataset.rotation = 0;
    wrapper.innerHTML = `
      <div class="file-controls">
        <button class="remove-file" title="Remover página">×</button>
        <button class="rotate-page" title="Girar página">⟳</button>
      </div>
      <div class="page-badge">Pg ${i}</div>
      <canvas data-page="${i}"></canvas>
    `;
    wrapper.addEventListener('click', () =>
      togglePageSelection(containerEl, i, wrapper)
    );
    wrapper.querySelector('.rotate-page').addEventListener('click', async e => {
      e.stopPropagation();
      const rot = (parseInt(wrapper.dataset.rotation) + 90) % 360;
      wrapper.dataset.rotation = rot;
      await renderPage(pdf, i, pagesContainer, rot);
    });
    wrapper.querySelector('.remove-file').addEventListener('click', e => {
      e.stopPropagation();
      removePage(containerEl, i, wrapper);
      if (!pagesContainer.querySelector('.page-wrapper')) {
        btnEl.disabled = true;
      }
    });
    pagesContainer.appendChild(wrapper);
    containerEl.selectedPages.add(i);
    wrapper.classList.add('selected');
  }

  let next = 1, BATCH = 5;
  while (next <= pdf.numPages) {
    const end = Math.min(pdf.numPages, next + BATCH - 1);
    await Promise.all(
      Array.from({ length: end - next + 1 }, (_, idx) =>
        renderPage(pdf, next + idx, pagesContainer, 0)
      )
    );
    next = end + 1;
  }

  spinnerEl.style.display = 'none';
  btnEl.disabled = false;
}

function togglePageSelection(containerEl, pg, wrapper) {
  if (containerEl.selectedPages.has(pg)) {
    containerEl.selectedPages.delete(pg);
    wrapper.classList.remove('selected');
  } else {
    containerEl.selectedPages.add(pg);
    wrapper.classList.add('selected');
  }
}

function removePage(containerEl, pageNum, wrapper) {
  containerEl.selectedPages.delete(pageNum);
  wrapper.remove();
}

async function renderPage(pdf, pageNumber, container, rotation = 0) {
  const page       = await pdf.getPage(pageNumber);
  const baseViewport = page.getViewport({ scale: 1, rotation });
  const dpr        = window.devicePixelRatio || 1;
  const scale      = (THUMB_WIDTH * dpr) / baseViewport.width;
  const vp         = page.getViewport({ scale, rotation });
  const canvas     = container.querySelector(`canvas[data-page="${pageNumber}"]`);

  canvas.width      = Math.floor(vp.width);
  canvas.height     = Math.floor(vp.height);
  canvas.style.width  = `${THUMB_WIDTH}px`;
  canvas.style.height = `${(vp.height / dpr).toFixed(0)}px`;

  await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
}
