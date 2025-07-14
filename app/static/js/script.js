import { previewPDF, clearPreview, getSelectedPages } from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';

function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

document.addEventListener('DOMContentLoaded', () => {
  let mergeDZ;

  document.querySelectorAll('.dropzone').forEach(dzEl => {
    const cfg = {
      dropzone: dzEl,
      input: dzEl.querySelector('input[type="file"]'),
      list: document.querySelector(dzEl.dataset.list),
      extensions: dzEl.dataset.extensions ? dzEl.dataset.extensions.split(',') : ['.pdf'],
      multiple: dzEl.dataset.multiple !== 'false',
      onChange: files => {
        if (files.length) {
          previewPDF(files[0], dzEl.dataset.preview, dzEl.dataset.spinner, dzEl.dataset.action);
        } else {
          clearPreview(dzEl.dataset.preview, dzEl.dataset.action);
        }
      }
    };

    const instance = createFileDropzone(cfg);
    if (dzEl.id === 'dropzone-merge') mergeDZ = instance;
  });

  const mergeBtn = document.querySelector('#btn-merge');
  if (mergeBtn) {
    mergeBtn.addEventListener('click', () => {
      const pages = getSelectedPages();
      const form = new FormData();
      const file = mergeDZ ? mergeDZ.getFiles()[0] : null;
      if (!file) return;
      form.append('file', file);
      form.append('pages', JSON.stringify(pages));
      fetch('/api/merge', {
        method: 'POST',
        body: form,
        headers: { 'X-CSRFToken': getCSRFToken() }
      })
        .then(r => r.blob())
        .then(blob => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'merged.pdf';
          document.body.appendChild(a);
          a.click();
          a.remove();
        });
    });
  }
});
