import { previewPDF, clearPreview } from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';

document.addEventListener('DOMContentLoaded', () => {
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

    createFileDropzone(cfg);
  });
});
