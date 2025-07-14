import {
  previewPDF,
  clearSelection,
  getSelectedPages,
  clearFileSelection,
  getSelectedFiles,
  selectedFiles
} from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';
import {
  mostrarMensagem,
  getCSRFToken,
} from './utils.js';
import {
  convertFiles,
  extractPages,
  compressFile,
} from './api.js';

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.dropzone').forEach(dzEl => {
    const inputEl    = dzEl.querySelector('input[type="file"]');
    const previewSel = dzEl.dataset.preview;
    const spinnerSel = dzEl.dataset.spinner;
    const btnSel     = dzEl.dataset.action;
    const filesContainer = document.querySelector(previewSel);
    const exts       = dzEl.dataset.extensions ? dzEl.dataset.extensions.split(',') : ['.pdf'];
    const allowMultiple = dzEl.dataset.multiple === 'true';

    const dz = createFileDropzone({
      dropzone: dzEl,
      input:    inputEl,
      extensions: exts,
      multiple:   allowMultiple,
      onChange: files => {
        clearSelection();
        clearFileSelection();
        filesContainer.innerHTML = '';
        document.querySelector(btnSel).disabled = true;

        if (!files.length) return;

        files.forEach((file, idx) => {
          const fw = document.createElement('div');
          fw.classList.add('file-wrapper');
          fw.dataset.index = idx;
          fw.innerHTML = `
            <div class="file-controls">
              <button class="move-left" aria-label="Mover para esquerda">\u2039</button>
              <span class="file-badge">Arquivo ${idx + 1}</span>
              <button class="move-right" aria-label="Mover para direita">\u203a</button>
            </div>
            <div class="file-name">${file.name}</div>
            <div class="preview-grid"></div>
          `;

          if (btnSel.includes('merge')) {
            fw.classList.add('selected');
            selectedFiles.add(idx);
            fw.addEventListener('click', () => {
              const i = Number(fw.dataset.index);
              if (selectedFiles.has(i)) {
                selectedFiles.delete(i);
                fw.classList.remove('selected');
              } else {
                selectedFiles.add(i);
                fw.classList.add('selected');
              }
            });
          }

          fw.querySelector('.move-left').addEventListener('click', e => {
            e.stopPropagation();
            const prev = fw.previousElementSibling;
            if (prev) filesContainer.insertBefore(fw, prev);
          });

          fw.querySelector('.move-right').addEventListener('click', e => {
            e.stopPropagation();
            const next = fw.nextElementSibling;
            if (next) filesContainer.insertBefore(next, fw);
          });

          filesContainer.appendChild(fw);
          previewPDF(file, fw.querySelector('.preview-grid'), spinnerSel, btnSel);
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
          const orderedWrappers = Array.from(
            filesContainer.querySelectorAll('.file-wrapper')
          );
          const filesToMerge = orderedWrappers
            .filter(w => selectedFiles.has(Number(w.dataset.index)))
            .map(w => dz.getFiles()[Number(w.dataset.index)]);
          if (!filesToMerge.length) {
            return mostrarMensagem('Selecione ao menos um arquivo para juntar.', 'erro');
          }
          const form = new FormData();
          filesToMerge.forEach(f => form.append('files', f, f.name));
          form.append('pages', JSON.stringify([]));
          fetch('/api/merge', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken() },
            body: form
          })
            .then(res => res.blob())
            .then(blob => {
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = 'merge.pdf';
              a.click();
            })
            .catch(err => console.error(err));
        }
        return;
      }

      if (id.includes('split')) {
        const pages = getSelectedPages();
        if (!pages.length) {
          return mostrarMensagem('Marque ao menos uma página para dividir.', 'erro');
        }
        const form = new FormData();
        form.append('file', files[0]);
        form.append('pages', JSON.stringify(pages));
        fetch('/api/split', {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken() },
          body: form
        })
          .then(res => res.blob())
          .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'split.pdf';
            a.click();
          })
          .catch(err => console.error(err));
        return;
      }

      if (id.includes('compress')) {
        compressFile(files[0]);
      }
    });
  });
});

