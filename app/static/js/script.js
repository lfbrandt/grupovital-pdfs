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
    const inputEl        = dzEl.querySelector('input[type="file"]');
    const previewSel     = dzEl.dataset.preview;
    const spinnerSel     = dzEl.dataset.spinner;
    const btnSel         = dzEl.dataset.action;
    const filesContainer = document.querySelector(previewSel);
    let dz;

    // Layout para merge/convert/compress
    if (btnSel.includes('merge') || btnSel.includes('convert') || btnSel.includes('compress')) {
      filesContainer.classList.add('files-container');
      if (window.Sortable) {
        Sortable.create(filesContainer, {
          animation: 150,
          ghostClass: 'sortable-ghost',
          draggable: '.file-wrapper',
          onEnd: evt => {
            if (dz && dz.moveFile) dz.moveFile(evt.oldIndex, evt.newIndex);
            Array.from(filesContainer.children).forEach((el, i) => el.dataset.index = i);
          }
        });
      }
    }

    const exts          = dzEl.dataset.extensions
      ? dzEl.dataset.extensions.split(',').map(e => e.replace(/^\./, ''))
      : ['pdf'];
    const allowMultiple = dzEl.dataset.multiple === 'true';

    function removeFileAtIndex(idx) {
      dz.removeFile(idx);
    }

    function renderFiles(files) {
      filesContainer.innerHTML = '';
      const btn = document.querySelector(btnSel);
      btn.disabled = files.length === 0;
      if (!files.length) return;

      files.forEach((file, idx) => {
        const fw = document.createElement('div');
        fw.classList.add('file-wrapper');
        fw.dataset.index = idx;
        fw.innerHTML = `
          <div class="file-controls">
            <button class="view-pdf" aria-label="Visualizar PDF">🔍</button>
            <span class="file-badge">Arquivo ${idx + 1}</span>
            <button class="remove-file" aria-label="Remover arquivo">×</button>
          </div>
          <div class="file-name">${file.name}</div>
          <div class="preview-grid"></div>
        `;

        if (btnSel.includes('merge')) fw.classList.add('selected');
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
          makePagesSortable(container);
        }
      });
    }

    dz = createFileDropzone({
      dropzone:   dzEl,
      input:      inputEl,
      extensions: exts,
      multiple:   allowMultiple,
      onChange:   renderFiles
    });

    const btn = document.querySelector(btnSel);
    btn.addEventListener('click', e => {
      e.preventDefault();
      const files = dz.getFiles();
      if (!files.length) return mostrarMensagem('Selecione um PDF.', 'erro');
      const id = btn.id;

      // Conversão
      if (id.includes('convert')) {
        convertFiles(files);
        return;
      }

      // Merge de PDFs (preview do resultado)
      if (id.includes('merge')) {
        const orderedWrappers = Array.from(
          filesContainer.querySelectorAll('.file-wrapper')
        );
        const form = new FormData();

        // 1) Anexa os arquivos na ordem atual
        orderedWrappers.forEach(w => {
          const idx = Number(w.dataset.index);
          const f   = dz.getFiles()[idx];
          form.append('files', f, f.name);
        });

        // 2) Monta só as páginas selecionadas + suas rotações
        const mapped = orderedWrappers.map(w => {
          const grid = w.querySelector('.preview-grid');
          const pages = getSelectedPages(grid, true);
          const rotations = pages.map(pg => {
            const el  = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
            return Number(el.dataset.rotation) || 0;
          });
          return { pages, rotations };
        });

        const pagesMap  = mapped.map(m => m.pages);
        const rotations = mapped.map(m => m.rotations);
        form.append('pagesMap', JSON.stringify(pagesMap));
        form.append('rotations', JSON.stringify(rotations));

        // query-param flatten=true por padrão
        fetch(`/api/merge?flatten=true`, {
          method: 'POST',
          headers: {
            'X-CSRFToken': getCSRFToken(),
            'Accept': 'application/pdf'
          },
          body: form
        })
          .then(res => {
            if (!res.ok) throw new Error('Erro ao juntar PDFs: ' + res.status);
            return res.blob();
          })
          .then(blob => {
            // 3) Download automático
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            a.download = 'juntado.pdf';
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(blobUrl);

            // 4) Preview do PDF juntado
            const mergedFile = new File([blob], 'juntado.pdf', { type: 'application/pdf' });
            filesContainer.innerHTML = '';
            const fw = document.createElement('div');
            fw.classList.add('file-wrapper');
            fw.dataset.index = 0;
            fw.innerHTML = `
              <div class="file-name">juntado.pdf</div>
              <div class="preview-grid"></div>
            `;
            filesContainer.appendChild(fw);
            const previewGrid = fw.querySelector('.preview-grid');
            previewPDF(mergedFile, previewGrid, spinnerSel, btnSel);
            makePagesSortable(previewGrid);

            mostrarMensagem('PDFs juntados com sucesso!');
          })
          .catch(err => {
            console.error(err);
            mostrarMensagem(err.message, 'erro');
          });

        return;
      }

      // Split de PDF único
      if (id.includes('split')) {
        const fw = filesContainer.querySelector('.file-wrapper');
        const grid = fw.querySelector('.preview-grid');
        const pages = getSelectedPages(grid, true);
        if (!pages.length) {
          return mostrarMensagem('Marque ao menos uma página para dividir.', 'erro');
        }
        const rotations = pages.map(pg => {
          const el  = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
          return Number(el.dataset.rotation) || 0;
        });
        splitPages(files[0], pages, rotations);
        return;
      }

      // Compressão
      if (id.includes('compress')) {
        const wrappers = Array.from(filesContainer.querySelectorAll('.file-wrapper'));
        const rotations = wrappers.map(w => {
          const grid = w.querySelector('.preview-grid');
          return Array.from(grid.querySelectorAll('.page-wrapper.selected'))
            .map(p => Number(p.dataset.rotation) || 0);
        });
        files.forEach((file, i) => compressFile(file, rotations[i] || []));
        return;
      }
    });
  });
});