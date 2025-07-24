import {
  previewPDF,
  getSelectedPages
} from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';
import {
  mostrarMensagem,
  getCSRFToken,
} from './utils.js';
import {
  convertFiles,
  splitPages,
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
    const exts       = dzEl.dataset.extensions
      ? dzEl.dataset.extensions.split(',').map(e => e.replace(/^\./, ''))
      : ['pdf'];
    const allowMultiple = dzEl.dataset.multiple === 'true';

    let dz;

    function removeFileAtIndex(idx) {
      dz.removeFile(idx);
    }

    function renderFiles(files) {
      filesContainer.innerHTML = '';
      const btn = document.querySelector(btnSel);
      btn.disabled = files.length === 0;

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
          const container = fw.querySelector('.preview-grid');
          const pages = getSelectedPages(container, true);
          if (!pages.length) {
            return mostrarMensagem('Marque ao menos uma página.', 'erro');
          }
          const rotations = pages.map(pg => {
            const pw = container.querySelector(`.page-wrapper[data-page="${pg}"]`);
            return Number(pw.dataset.rotation || 0);
          });
          splitPages(files[0], pages, rotations);
        } else {
          const orderedWrappers = Array.from(
            filesContainer.querySelectorAll('.file-wrapper')
          );

          const form = new FormData();
          const mapped = orderedWrappers.map(w => {
            const idx = Number(w.dataset.index);
            const file = dz.getFiles()[idx];
            form.append('files', file, file.name);

            const container = w.querySelector('.preview-grid');
            const pageEls = Array.from(w.querySelectorAll('.page-wrapper'));
            const pagesInOrder = pageEls.map(p => Number(p.dataset.page));
            const rotationsInOrder = pageEls.map(p => Number(p.dataset.rotation || 0));
            const selected = pagesInOrder.filter(pg => container.selectedPages.has(pg));
            const selectedRot = pageEls
              .filter(p => container.selectedPages.has(Number(p.dataset.page)))
              .map(p => Number(p.dataset.rotation || 0));
            return {
              pages: selected.length ? selected : pagesInOrder,
              rotations: selected.length ? selectedRot : rotationsInOrder,
            };
          });

          const pagesMap = mapped.map(m => m.pages);
          const rotations = mapped.map(m => m.rotations);

          form.append('pagesMap', JSON.stringify(pagesMap));
          form.append('rotations', JSON.stringify(rotations));

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
        const container = fw.querySelector('.preview-grid');
        const pages = getSelectedPages(container, true);
        if (!pages.length) {
          return mostrarMensagem('Marque ao menos uma página para dividir.', 'erro');
        }
        const form = new FormData();
        form.append('file', files[0]);
        form.append('pages', JSON.stringify(pages));
        const rotations = pages.map(pg => {
          const pw = container.querySelector(`.page-wrapper[data-page="${pg}"]`);
          return Number(pw.dataset.rotation || 0);
        });
        form.append('rotations', JSON.stringify(rotations));
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
        const wrappers = Array.from(
          filesContainer.querySelectorAll('.file-wrapper')
        );
        const rotations = wrappers.map(w => {
          const pages = w.querySelectorAll('.page-wrapper');
          return Array.from(pages).map(p => Number(p.dataset.rotation || 0));
        });
        files.forEach((file, i) => compressFile(file, rotations[i] || []));
        return;
      }
    });
  });
});

// Legacy support for direct file input flows
let arquivosSelecionados = [];

function atualizarLista() {
  const lista = document.getElementById('lista-arquivos');
  if (!lista) return;
  lista.innerHTML = arquivosSelecionados.map(f => `<li>${f.name}</li>`).join('');
}

function adicionarArquivo() {
  const input = document.getElementById('file-input');
  if (!input) return;
  const novosArquivos = Array.from(input.files);
  arquivosSelecionados.push(...novosArquivos);
  input.value = '';
  atualizarLista();
}

function adicionarArquivoSplit() {
  const input = document.getElementById('file-input');
  if (!input) return;
  const novosArquivos = Array.from(input.files);
  arquivosSelecionados = [];
  arquivosSelecionados.push(...novosArquivos);
  input.value = '';
  atualizarLista();
}

function renderPreview(thumbnails, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = '';
  thumbnails.forEach((src, idx) => {
    const wrap = document.createElement('div');
    wrap.className = 'page-wrapper';
    wrap.dataset.page = idx + 1;
    wrap.dataset.rotation = 0;
    wrap.innerHTML = `<img src="${src}" alt="page">` +
      `<button class="rotate-btn">⟳</button>` +
      `<button class="delete-btn">X</button>`;
    const img = wrap.querySelector('img');
    wrap.querySelector('.rotate-btn').addEventListener('click', () => {
      const rot = (parseInt(wrap.dataset.rotation) + 90) % 360;
      wrap.dataset.rotation = rot;
      if (img) img.style.transform = `rotate(${rot}deg)`;
    });
    wrap.querySelector('.delete-btn').addEventListener('click', () => {
      wrap.remove();
    });
    container.appendChild(wrap);
  });
}

function initPreview({ route, inputId, containerId, btnId }) {
  const input = document.getElementById(inputId);
  const button = document.getElementById(btnId);
  if (!input) return;
  let currentFile = null;
  input.addEventListener(
    'change',
    e => {
      const file = e.target.files[0];
      if (!file) return;
      currentFile = file;
      const form = new FormData();
      form.append('file', file);
      fetch(route, { method: 'POST', body: form })
        .then(r => r.json())
        .then(d => {
          renderPreview(d.thumbnails, containerId);
          if (button) button.disabled = false;
        });
    },
    true
  );
  return () => currentFile;
}

function initCompressPreview() {
  const getFile = initPreview({
    route: '/api/compress/preview',
    inputId: 'input-compress',
    containerId: 'preview-container',
    btnId: 'btn-compress'
  });

  const btn = document.getElementById('btn-compress');
  const form = btn ? btn.closest('form') : null;
  if (btn) {
    btn.addEventListener(
      'click',
      e => {
        e.stopImmediatePropagation();
      },
      true
    );
  }
  if (form) {
    form.addEventListener('submit', e => {
      e.preventDefault();
      const file = getFile();
      if (!file) return;
      const fd = new FormData(form);
      const wrappers = document.querySelectorAll(
        '#preview-container .page-wrapper'
      );
      const rotations = [];
      wrappers.forEach(w => {
        rotations.push(parseInt(w.dataset.rotation || '0'));
      });
      fd.append('rotations', JSON.stringify(rotations));
      fetch(form.action, { method: 'POST', body: fd })
        .then(r => r.blob())
        .then(blob => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'compressed.pdf';
          a.click();
        })
        .catch(err => console.error(err));
    });
  }
}

function initSplitPreview() {
  const getFile = initPreview({
    route: '/api/split/preview',
    inputId: 'input-split',
    containerId: 'preview-container',
    btnId: 'btn-split'
  });

  const btn = document.getElementById('btn-split');
  if (btn) {
    btn.addEventListener(
      'click',
      e => {
        e.stopImmediatePropagation();
        e.preventDefault();
        const file = getFile();
        if (!file) return;
        const fd = new FormData();
        fd.append('file', file);
        const wrappers = document.querySelectorAll(
          '#preview-container .page-wrapper'
        );
        const pages = [];
        const rotations = [];
        wrappers.forEach((w, idx) => {
          pages.push(idx + 1);
          rotations.push(parseInt(w.dataset.rotation || '0'));
        });
        fd.append('pages', JSON.stringify(pages));
        fd.append('rotations', JSON.stringify(rotations));
        fetch('/api/split', { method: 'POST', body: fd })
          .then(r => r.blob())
          .then(blob => {
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = 'split.zip';
            a.click();
          })
          .catch(err => console.error(err));
      },
      true
    );
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const pathname = window.location.pathname;
  if (pathname.includes('/merge')) {
    // placeholder for merge preview
  } else if (pathname.includes('/converter')) {
    // placeholder for converter preview
  } else if (pathname.includes('/compress')) {
    initCompressPreview();
  } else if (pathname.includes('/split')) {
    initSplitPreview();
  }
});

