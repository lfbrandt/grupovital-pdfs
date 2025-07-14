const PREVIEW_BATCH_SIZE = 5;
const PREVIEW_TIMEOUT = 30000;
// largura do thumbnail em pixels
const THUMB_WIDTH = 120;

export async function previewPDF(file, containerEl, spinnerEl, actionBtnEl) {
  const container = document.querySelector(containerEl);
  const spinner = document.querySelector(spinnerEl);
  const btn = document.querySelector(actionBtnEl);
  let timeoutId;

  if (!container || !spinner || !btn) return;
  const previousHTML = container.innerHTML;
  spinner.style.display = 'flex';
  btn.disabled = true;

  try {
    const arrayBuffer = await file.arrayBuffer();
    const loadTask = pdfjsLib.getDocument(new Uint8Array(arrayBuffer)).promise;
    const pdf = await Promise.race([
      loadTask,
      new Promise((_, reject) => {
        timeoutId = setTimeout(() => reject(new Error('timeout')), PREVIEW_TIMEOUT);
      })
    ]);
    clearTimeout(timeoutId);

    container.innerHTML = '';

    let nextPage = 1;
    for (let i = 1; i <= pdf.numPages; i++) {
      const wrapper = document.createElement('div');
      wrapper.classList.add('page-wrapper');
      wrapper.setAttribute('aria-label', `Página ${i} de ${pdf.numPages}`);
      wrapper.innerHTML = `
        <div class="page-badge">Pg ${i}</div>
        <canvas data-page="${i}"></canvas>
        <span class="sr-only">Página ${i} de ${pdf.numPages}</span>
      `;
      container.appendChild(wrapper);
    }

    async function loadBatch() {
      const end = Math.min(pdf.numPages, nextPage + PREVIEW_BATCH_SIZE - 1);
      const renders = [];
      for (let p = nextPage; p <= end; p++) {
        renders.push(renderPage(pdf, p));
      }
      await Promise.all(renders);
      nextPage = end + 1;
    }

    while (nextPage <= pdf.numPages) {
      await loadBatch();
    }

    btn.disabled = false;
  } catch (err) {
    console.error('Preview falhou:', err);
    if (typeof mostrarMensagem === 'function') {
      mostrarMensagem('Não foi possível gerar o preview', 'erro');
    }
    container.innerHTML = previousHTML;
    clearTimeout(timeoutId);
  } finally {
    spinner.style.display = 'none';
  }
}

async function renderPage(pdf, pageNumber) {
  const page = await pdf.getPage(pageNumber);
  // viewport original para obter largura da página
  const origViewport = page.getViewport({ scale: 1 });
  const dpr = window.devicePixelRatio || 1;
  const scale = (THUMB_WIDTH * dpr) / origViewport.width;
  const viewport = page.getViewport({ scale });

  const canvas = document.querySelector(`canvas[data-page="${pageNumber}"]`);
  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = `${THUMB_WIDTH}px`;
  canvas.style.height = `${(viewport.height / dpr).toFixed(0)}px`;

  await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
}

export function clearPreview(containerEl, actionBtnEl) {
  const container = document.querySelector(containerEl);
  const btn = document.querySelector(actionBtnEl);
  if (container) container.innerHTML = '';
  if (btn) btn.disabled = true;
}
