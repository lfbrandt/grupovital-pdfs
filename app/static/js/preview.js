const PREVIEW_BATCH_SIZE = 5;

export async function previewPDF(file, containerEl, spinnerEl, actionBtnEl) {
  const container = document.querySelector(containerEl);
  const spinner = document.querySelector(spinnerEl);
  const btn = document.querySelector(actionBtnEl);

  if (!container || !spinner || !btn) return;
  container.innerHTML = '';
  spinner.style.display = 'flex';
  btn.disabled = true;

  try {
    const arrayBuffer = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument(new Uint8Array(arrayBuffer)).promise;

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
  } finally {
    spinner.style.display = 'none';
  }
}

async function renderPage(pdf, pageNumber) {
  const page = await pdf.getPage(pageNumber);
  const viewport = page.getViewport({ scale: 1.0 });
  const canvas = document.querySelector(`canvas[data-page="${pageNumber}"]`);
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({ canvasContext: canvas.getContext('2d'), viewport }).promise;
}

export function clearPreview(containerEl, actionBtnEl) {
  const container = document.querySelector(containerEl);
  const btn = document.querySelector(actionBtnEl);
  if (container) container.innerHTML = '';
  if (btn) btn.disabled = true;
}
