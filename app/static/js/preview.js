const THUMB_WIDTH = 100;

const selectedPages = new Set();
export function clearSelection() {
  selectedPages.clear();
}
export function getSelectedPages() {
  return Array.from(selectedPages).sort((a, b) => a - b);
}

export async function previewPDF(file, containerSel, spinnerSel, btnSel) {
  clearSelection();
  const container = document.querySelector(containerSel);
  const spinner   = document.querySelector(spinnerSel);
  const btn       = document.querySelector(btnSel);
  if (!container || !spinner || !btn) return;
  container.innerHTML = '';
  spinner.style.display = 'flex';
  btn.disabled = true;

  const arrayBuf = await file.arrayBuffer();
  const pdf      = await pdfjsLib.getDocument(new Uint8Array(arrayBuf)).promise;

  for (let i = 1; i <= pdf.numPages; i++) {
    const wrapper = document.createElement('div');
    wrapper.classList.add('page-wrapper');
    wrapper.dataset.page = i;
    wrapper.innerHTML = `
      <div class="page-badge">Pg ${i}</div>
      <canvas data-page="${i}"></canvas>
    `;
    wrapper.addEventListener('click', () => {
      const pg = +wrapper.dataset.page;
      if (selectedPages.has(pg)) {
        selectedPages.delete(pg);
        wrapper.classList.remove('selected');
      } else {
        selectedPages.add(pg);
        wrapper.classList.add('selected');
      }
    });
    container.appendChild(wrapper);
  }

  let next = 1, BATCH = 5;
  while (next <= pdf.numPages) {
    const end = Math.min(pdf.numPages, next + BATCH - 1);
    await Promise.all(
      Array.from({ length: end - next + 1 }, (_, idx) =>
        renderPage(pdf, next + idx)
      )
    );
    next = end + 1;
  }

  btn.disabled = false;
  spinner.style.display = 'none';
}

async function renderPage(pdf, pageNumber) {
  const page       = await pdf.getPage(pageNumber);
  const baseViewport = page.getViewport({ scale: 1 });
  const dpr        = window.devicePixelRatio || 1;
  const scale      = (THUMB_WIDTH * dpr) / baseViewport.width;
  const vp         = page.getViewport({ scale });
  const canvas     = document.querySelector(`canvas[data-page="${pageNumber}"]`);

  canvas.width      = Math.floor(vp.width);
  canvas.height     = Math.floor(vp.height);
  canvas.style.width  = `${THUMB_WIDTH}px`;
  canvas.style.height = `${(vp.height / dpr).toFixed(0)}px`;

  await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
}
