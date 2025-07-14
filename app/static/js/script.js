import { previewPDF, clearSelection, getSelectedPages } from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';
import {
  mostrarMensagem,
  getCSRFToken,
} from './utils.js';
import {
  convertFiles,
  mergePdfs,
  extractPages,
  splitFile,
  compressFile,
} from './api.js';

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.dropzone').forEach(dzEl => {
    const inputEl    = dzEl.querySelector('input[type="file"]');
    const previewSel = dzEl.dataset.preview;
    const spinnerSel = dzEl.dataset.spinner;
    const btnSel     = dzEl.dataset.action;
    const exts       = dzEl.dataset.extensions ? dzEl.dataset.extensions.split(',') : ['.pdf'];
    const allowMultiple = dzEl.dataset.multiple === 'true';

    const dz = createFileDropzone({
      dropzone: dzEl,
      input:    inputEl,
      extensions: exts,
      multiple:   allowMultiple,
      onChange: files => {
        const root = document.querySelector(previewSel);
        clearSelection();
        root.innerHTML = '';
        document.querySelector(btnSel).disabled = true;

        if (!files.length) return;

        files.forEach(file => {
          const fileWrapper = document.createElement('div');
          fileWrapper.classList.add('file-wrapper');
          root.appendChild(fileWrapper);

          previewPDF(file, fileWrapper, spinnerSel, btnSel);
        });
      }
    });

    const btn = document.querySelector(btnSel);
    btn.addEventListener('click', e => {
      e.preventDefault();
      const files = dz.getFiles();
      if (!files.length) return mostrarMensagem('Selecione um PDF.', 'erro');

      const id = btn.id;
      if (id.includes('convert')) {
        convertFiles(files);
        return;
      }

      if (id.includes('merge')) {
        if (files.length === 1) {
          const pages = getSelectedPages();
          if (!pages.length) {
            return mostrarMensagem('Marque ao menos uma página.', 'erro');
          }
          extractPages(files[0], pages);
        } else {
          mergePdfs(files);
        }
        return;
      }

      if (id.includes('split')) {
        splitFile(files[0]);
        return;
      }

      if (id.includes('compress')) {
        compressFile(files[0]);
      }
    });
  });
});

