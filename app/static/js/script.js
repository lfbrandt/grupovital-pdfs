import {
  previewPDF,
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

// grupos de extensões
const PDF_EXTS   = ['pdf'];
const IMG_EXTS   = ['jpg','jpeg','png','bmp','tiff'];
const DOC_EXTS   = ['doc','docx','odt','rtf','txt','html'];
const SHEET_EXTS = ['xls','xlsx','ods'];
const PPT_EXTS   = ['ppt','pptx','odp'];

function getExt(name) {
  return name.split('.').pop().toLowerCase();
}

function showGenericPreview(file, container) {
  const ext = getExt(file.name);
  container.innerHTML = '';
  if (IMG_EXTS.includes(ext)) {
    const img = document.createElement('img');
    img.src = URL.createObjectURL(file);
    img.style.maxWidth = '120px';
    img.style.margin = '8px';
    container.appendChild(img);
  } else {
    container.innerHTML = `
      <div class="file-icon">${ext.toUpperCase()}</div>
      <div class="file-name">${file.name}</div>
    `;
  }
}

function makePagesSortable(containerEl) {
  if (window.Sortable) {
    Sortable.create(containerEl, {
      animation: 150,
      ghostClass: 'sortable-ghost',
      draggable: '.page-wrapper'
    });
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.dropzone').forEach(dzEl => {
    const inputEl    = dzEl.querySelector('input[type="file"]');
    const previewSel = dzEl.dataset.preview;
    const spinnerSel = dzEl.dataset.spinner;
    const btnSel     = dzEl.dataset.action;
    const filesContainer = document.querySelector(previewSel);

    if (btnSel.includes('merge') || btnSel.includes('convert') || btnSel.includes('compress')) {
      filesContainer.classList.add('files-container');
      if (window.Sortable) {
        Sortable.create(filesContainer, {
          animation: 150,
          ghostClass: 'sortable-ghost',
          draggable: '.file-wrapper',
          onEnd: evt => {
            if (dz && dz.moveFile) {
              dz.moveFile(evt.oldIndex, evt.newIndex);
            }
            Array.from(filesContainer.children).forEach((el, i) => {
              el.dataset.index = i;
            });
          }
        });
      }
    }
    const exts       = dzEl.dataset.extensions ? dzEl.dataset.extensions.split(',') : ['.pdf'];
    const allowMultiple = dzEl.dataset.multiple === 'true';

    let dz;

    function removeFileAtIndex(idx) {
      dz.removeFile(idx);
    }

    function renderFiles(files) {
      clearFileSelection();
      filesContainer.innerHTML = '';
      document.querySelector(btnSel).disabled = true;

      if (!files.length) return;

      files.forEach((file, idx) => {
        const fileUrl = URL.createObjectURL(file);
        const fw = document.createElement('div');
        fw.classList.add('file-wrapper');
        fw.dataset.index = idx;
        fw.innerHTML = `
          <div class="file-controls">
            <button class="view-pdf" aria-label="Visualizar PDF">\uD83D\uDD0D</button>
            <span class="file-badge">Arquivo ${idx + 1}</span>
            <button class="remove-file" aria-label="Remover arquivo">×</button>
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

        fw.querySelector('.remove-file').addEventListener('click', e => {
          e.stopPropagation();
          removeFileAtIndex(idx);
        });

        filesContainer.appendChild(fw);
        const container = fw.querySelector('.preview-grid');
        const ext = getExt(file.name);
        if (btnSel.includes('convert') && !PDF_EXTS.includes(ext)) {
          showGenericPreview(file, container);
        } else {
          previewPDF(file, container, spinnerSel, btnSel);
          const pagesContainer = fw.querySelector('.pages-container');
          if (pagesContainer) makePagesSortable(pagesContainer);
        }
      });
    }

    dz = createFileDropzone({
      dropzone: dzEl,
      input:    inputEl,
      extensions: exts,
      multiple:   allowMultiple,
      onChange: renderFiles
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
          const fw = filesContainer.querySelector('.file-wrapper');
          const pages = getSelectedPages(
            fw.querySelector('.preview-grid'),
            true
          );
          if (!pages.length) {
            return mostrarMensagem('Marque ao menos uma página.', 'erro');
          }
          extractPages(files[0], pages);
        } else {
          const orderedWrappers = Array.from(
            filesContainer.querySelectorAll('.file-wrapper')
          ).filter(w => selectedFiles.has(Number(w.dataset.index)));

          const form = new FormData();
          const pagesMap = orderedWrappers.map(w => {
            const idx = Number(w.dataset.index);
            const file = dz.getFiles()[idx];
            form.append('files', file, file.name);

            const pagesInOrder = Array.from(
              w.querySelectorAll('.page-wrapper')
            ).map(p => Number(p.dataset.page));
            const selected = pagesInOrder.filter(pg =>
              w.querySelector('.preview-grid').selectedPages.has(pg)
            );
            return selected.length ? selected : pagesInOrder;
          });

          form.append('pagesMap', JSON.stringify(pagesMap));

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
        const fw = filesContainer.querySelector('.file-wrapper');
        const pages = getSelectedPages(
          fw.querySelector('.preview-grid'),
          true
        );
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

