import { getSelectedPages } from './preview.js';
import { mostrarMensagem } from './utils.js';
import {
  convertFiles,
  mergePdfs,
  splitPages,
  compressFile,
  API_BASE,
} from './api.js';
import { PdfWidget } from './pdf-widget.js';
import { createFileDropzone } from './fileDropzone.js';

// grupos de extensões
const PDF_EXTS   = ['pdf'];
const IMG_EXTS   = ['jpg','jpeg','png','bmp','tiff'];
const DOC_EXTS   = ['doc','docx','odt','rtf','txt','html'];
const SHEET_EXTS = ['xls','xlsx','ods'];
const PPT_EXTS   = ['ppt','pptx','odp'];

const fileInput    = document.getElementById('file-input');
const dropzoneEl   = document.getElementById('dropzone');
const fileList     = document.getElementById('lista-arquivos');
const converterBtn = document.getElementById('converter-btn');
let dz = null;

function makePagesSortable(containerEl) {
  if (window.Sortable) {
    Sortable.create(containerEl, {
      animation: 150,
      ghostClass: 'sortable-ghost',
      draggable: '.page-wrapper'
    });
  }
}

function handleAction(btn, files, container, widget) {
  const id = btn.id;
  if (id.includes('convert')) {
    convertFiles(files).then(() => {
      if (widget) widget.clear();
    });
    return;
  }

  if (id.includes('merge')) {
    if (files.length === 1) {
      const pages = getSelectedPages(container, true);
      if (!pages.length) return mostrarMensagem('Marque ao menos uma página.', 'erro');
      const rotations = pages.map(pg => {
        const pw = container.querySelector(`.page-wrapper[data-page="${pg}"]`);
        return Number(pw.dataset.rotation || 0);
      });
      splitPages(files[0], pages, rotations);
    } else {
      mergePdfs(files);
    }
    return;
  }

  if (id.includes('split')) {
    const pages = getSelectedPages(container, true);
    if (!pages.length) return mostrarMensagem('Marque ao menos uma página para dividir.', 'erro');
    const rotations = pages.map(pg => {
      const pw = container.querySelector(`.page-wrapper[data-page="${pg}"]`);
      return Number(pw.dataset.rotation || 0);
    });
    splitPages(files[0], pages, rotations);
    return;
  }

  if (id.includes('compress')) {
    compressFile(files[0]);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (fileInput && dropzoneEl) {
    dz = createFileDropzone({
      dropzone: dropzoneEl,
      input: fileInput,
      list: fileList,
      multiple: true,
      onChange: files => {
        if (converterBtn) converterBtn.disabled = files.length === 0;
      }
    });
  }

  document.querySelectorAll('.dropzone').forEach(dzEl => {
    const widget = new PdfWidget({
      dropzoneEl: dzEl,
      previewSel: dzEl.dataset.preview,
      spinnerSel: dzEl.dataset.spinner,
      btnSel: dzEl.dataset.action,
      action: (files, previewEl, w) => {
        const btn = document.querySelector(dzEl.dataset.action);
        handleAction(btn, files, previewEl, w);
      }
    });
    widget.init();
  });
});

