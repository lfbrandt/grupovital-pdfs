// app/static/js/preview-init.js
import { previewThumb } from './preview.js';

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

function init() {
  document
    .querySelectorAll('input[type="file"][data-preview-target]')
    .forEach(bindThumbPreview);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}