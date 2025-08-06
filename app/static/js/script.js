import {
  previewPDF,
  getSelectedPages
} from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';
import {
  mostrarLoading,
  resetarProgresso,
  atualizarProgresso,
  mostrarMensagem,
  getCSRFToken,
} from './utils.js';
import {
  xhrRequest,
  convertFiles,
  splitPages,   // caso use em outro lugar
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

    // Se for merge/convert/compress, aplica grid animada
    if (btnSel.includes('merge') || btnSel.includes('convert') || btnSel.includes('compress')) {
      filesContainer.classList.add('files-container');
      if (window.Sortable) {
        Sortable.create(filesContainer, {
          animation: 150,
          ghostClass: 'sortable-ghost',
          draggable: '.file-wrapper',
          onEnd: evt => {
            dz.moveFile(evt.oldIndex, evt.newIndex);
            Array.from(filesContainer.children).forEach((el, i) => el.dataset.index = i);
          }
        });
      }
    }

    const exts          = dzEl.dataset.extensions
      ? dzEl.dataset.extensions.split(',').map(e => e.replace(/^\./, ''))
      : ['pdf'];
    const allowMultiple = dzEl.dataset.multiple === 'true';

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
        fw.querySelector('.remove-file').addEventListener('click', e => {
          e.stopPropagation();
          dz.removeFile(idx);
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
      if (!files.length) return mostrarMensagem('Selecione um arquivo.', 'erro');
      const id = btn.id;

      // ——— CONVERT ———
      if (id.includes('convert')) {
        const previewEl   = document.querySelector('#preview-convertido');
        const linkContainer = document.getElementById('link-download-container');
        const downloadLink  = document.getElementById('download-link');

        // UI reset
        mostrarLoading(true);
        resetarProgresso();
        previewEl.innerHTML = '';
        linkContainer.classList.add('hidden');

        // Só o primeiro arquivo (pode estender para vários)
        const form = new FormData();
        form.append('file', files[0]);

        fetch('/api/convert', {
          method: 'POST',
          body: form,
          headers: { 'X-CSRFToken': getCSRFToken() }
        })
        .then(res => {
          if (!res.ok) throw new Error('Erro ao converter o arquivo.');
          return res.blob();
        })
        .then(blob => {
          mostrarMensagem('Arquivo convertido com sucesso!', 'sucesso');
          atualizarProgresso(100);

          // Link de download
          const url = URL.createObjectURL(blob);
          downloadLink.href = url;
          downloadLink.download = files[0].name.replace(/\.[^.]+$/, '') + '.pdf';
          linkContainer.classList.remove('hidden');

          // Preview
          // previewPDF aceita Blob ou File, mas convertemos em File pra manter nome
          const fileForPreview = new File([blob], downloadLink.download, { type: 'application/pdf' });
          previewPDF(fileForPreview, previewEl);
        })
        .catch(err => {
          console.error(err);
          mostrarMensagem(err.message || 'Falha ao converter o arquivo.', 'erro');
        })
        .finally(() => {
          mostrarLoading(false);
          setTimeout(resetarProgresso, 500);
        });

        return;
      }

      // ——— MERGE ———
      if (id.includes('merge')) {
        const wrappers = Array.from(filesContainer.querySelectorAll('.file-wrapper'));
        const form = new FormData();

        wrappers.forEach(w => {
          const f = files[Number(w.dataset.index)];
          form.append('files', f, f.name);
        });

        // páginas & rotações
        const mapped = wrappers.map(w => {
          const grid = w.querySelector('.preview-grid');
          const pages = getSelectedPages(grid, true);
          const rots  = pages.map(pg => {
            const el = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
            return Number(el.dataset.rotation) || 0;
          });
          return { pages, rots };
        });
        form.append('pagesMap', JSON.stringify(mapped.map(m => m.pages)));
        form.append('rotations', JSON.stringify(mapped.map(m => m.rots)));

        fetch('/api/merge?flatten=true', {
          method: 'POST',
          headers: { 'X-CSRFToken': getCSRFToken(), 'Accept': 'application/pdf' },
          body: form
        })
        .then(res => {
          if (!res.ok) throw new Error('Falha no merge');
          return res.blob();
        })
        .then(blob => {
          // download automático
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url; a.download = 'juntado.pdf';
          document.body.appendChild(a);
          a.click();
          a.remove();

          // preview do resultado
          const mergedFile = new File([blob], 'juntado.pdf', { type: 'application/pdf' });
          filesContainer.innerHTML = '';
          const fw = document.createElement('div');
          fw.classList.add('file-wrapper');
          fw.dataset.index = 0;
          fw.innerHTML = `<div class="file-name">juntado.pdf</div><div class="preview-grid"></div>`;
          filesContainer.appendChild(fw);
          previewPDF(mergedFile, fw.querySelector('.preview-grid'));
          makePagesSortable(fw.querySelector('.preview-grid'));

          mostrarMensagem('PDFs juntados com sucesso!', 'sucesso');
        })
        .catch(err => {
          console.error(err);
          mostrarMensagem(err.message, 'erro');
        });

        return;
      }

      // ——— SPLIT ———
      if (id.includes('split')) {
        const fw   = filesContainer.querySelector('.file-wrapper');
        const grid = fw.querySelector('.preview-grid');
        const pages = getSelectedPages(grid, true);
        if (!pages.length) {
          return mostrarMensagem('Marque ao menos uma página para dividir.', 'erro');
        }
        const rotations = pages.map(pg => {
          const el = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
          return Number(el.dataset.rotation) || 0;
        });

        const form = new FormData();
        form.append('file', files[0]);
        form.append('pages', JSON.stringify(pages));
        form.append('rotations', JSON.stringify(rotations));

        xhrRequest('/api/split', form, blob => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'paginas_selecionadas.pdf';
          document.body.appendChild(a);
          a.click();
          a.remove();
          mostrarMensagem('PDF dividido com sucesso!', 'sucesso');
        });

        return;
      }

      // ——— COMPRESS ———
      if (id.includes('compress')) {
        const wrappers = Array.from(filesContainer.querySelectorAll('.file-wrapper'));
        const allRots = wrappers.map(w => {
          const grid = w.querySelector('.preview-grid');
          return Array.from(grid.querySelectorAll('.page-wrapper.selected'))
            .map(p => Number(p.dataset.rotation) || 0);
        });
        files.forEach((file, i) => compressFile(file, allRots[i] || []));
        return;
      }
    });
  });
});