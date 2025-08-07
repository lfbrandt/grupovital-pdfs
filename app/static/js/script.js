import { previewPDF, getSelectedPages } from './preview.js';
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
  splitPages,
  compressFile,
} from './api.js';

// Grupos de extensões
const PDF_EXTS   = ['pdf'];
const IMG_EXTS   = ['jpg','jpeg','png','bmp','tiff'];
const DOC_EXTS   = ['doc','docx','odt','rtf','txt','html'];
const SHEET_EXTS = ['xls','xlsx','ods'];
const PPT_EXTS   = ['ppt','pptx','odp'];

function getExt(name) {
  return name.split('.').pop().toLowerCase();
}

// Exibição genérica para arquivos não-PDF, usando Data URL para CSP
function showGenericPreview(file, container) {
  const reader = new FileReader();
  reader.onload = e => {
    container.innerHTML = `
      <img class="generic-preview-img" src="${e.target.result}" alt="${file.name}" />
      <div class="file-name">${file.name}</div>
    `;
  };
  reader.readAsDataURL(file);
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

// Navegação prev/next e coleta de seleção ao submeter
function initPageControls() {
  // prev/next
  document.querySelectorAll('button[id^="btn-"]').forEach(btn => {
    btn.addEventListener('click', () => {
      const prefix = btn.id.split('-')[1];
      const container = document.querySelector(`#preview-${prefix}`);
      const pages = Array.from(container.querySelectorAll('.page-wrapper'));
      const currentIndex = pages.findIndex(p => !p.classList.contains('hidden'));
      pages[currentIndex].classList.add('hidden');

      let nextIndex = currentIndex;
      if (btn.id.includes('prev')) {
        nextIndex = Math.max(0, currentIndex - 1);
      } else {
        nextIndex = Math.min(pages.length - 1, currentIndex + 1);
      }
      pages[nextIndex].classList.remove('hidden');
    });
  });

  // coleta seleção ao submeter
  document.querySelectorAll('form[data-prefix]').forEach(form => {
    form.addEventListener('submit', () => {
      const prefix = form.dataset.prefix;
      const container = document.querySelector(`#preview-${prefix}`);
      const selected = Array.from(
        container.querySelectorAll('.page-wrapper.selected')
      ).map(w => w.dataset.page);

      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'pages';
      input.value = JSON.stringify(selected);
      form.appendChild(input);
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initPageControls();

  document.querySelectorAll('.dropzone').forEach(dzEl => {
    const inputEl        = dzEl.querySelector('input[type="file"]');
    const previewSel     = dzEl.dataset.preview;
    const spinnerSel     = dzEl.dataset.spinner;
    const btnSel         = dzEl.dataset.action;
    const filesContainer = document.querySelector(previewSel);
    let dz;

    // habilita drag‐sort para merge/convert/compress
    if (/merge|convert|compress/.test(btnSel) && window.Sortable) {
      filesContainer.classList.add('files-container');
      Sortable.create(filesContainer, {
        animation: 150,
        ghostClass: 'sortable-ghost',
        draggable: '.file-wrapper',
        onEnd: evt => {
          dz.moveFile(evt.oldIndex, evt.newIndex);
          Array.from(filesContainer.children).forEach((el, i) => {
            el.dataset.index = i;
          });
        }
      });
    }

    const exts = dzEl.dataset.extensions
      ? dzEl.dataset.extensions.split(',').map(e => e.replace(/^\./, ''))
      : ['pdf'];
    const multiple = dzEl.dataset.multiple === 'true';

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
        const previewGrid = fw.querySelector('.preview-grid');
        const ext = getExt(file.name);

        if (/convert/.test(btnSel) && !PDF_EXTS.includes(ext)) {
          showGenericPreview(file, previewGrid);
        } else {
          previewPDF(file, previewGrid, spinnerSel, btnSel);
          makePagesSortable(previewGrid);
        }
      });
    }

    dz = createFileDropzone({
      dropzone: dzEl,
      input: inputEl,
      extensions: exts,
      multiple,
      onChange: renderFiles
    });

    const btn = document.querySelector(btnSel);
    btn.addEventListener('click', async e => {
      e.preventDefault();
      const files = dz.getFiles();
      if (!files.length) return mostrarMensagem('Selecione um arquivo.', 'erro');
      const id = btn.id;

      // ——— CONVERT ———
      if (id.includes('convert')) {
        // esconder apenas o card de upload
        document.querySelector('section.card').classList.add('hidden');
        mostrarLoading(true);
        resetarProgresso();
        document.querySelector('#preview-convert').innerHTML = '';
        document.getElementById('link-download-container').classList.add('hidden');

        const formData = new FormData();
        formData.append('file', files[0]);
        try {
          const res = await fetch('/api/convert', {
            method: 'POST',
            body: formData,
            headers: { 'X-CSRFToken': getCSRFToken() }
          });
          if (!res.ok) throw new Error('Erro ao converter.');
          const blob = await res.blob();
          mostrarMensagem('Convertido com sucesso!', 'sucesso');
          atualizarProgresso(100);

          const url = URL.createObjectURL(blob);
          const link = document.getElementById('download-link');
          link.href = url;
          link.download = files[0].name.replace(/\.[^\.]+$/, '') + '.pdf';
          document.getElementById('link-download-container').classList.remove('hidden');

          previewPDF(
            new File([blob], link.download, { type: 'application/pdf' }),
            document.querySelector('#preview-convert')
          );
        } catch (err) {
          mostrarMensagem(err.message || 'Falha na conversão.', 'erro');
        } finally {
          mostrarLoading(false);
          document.querySelector('section.card').classList.remove('hidden');
          setTimeout(resetarProgresso, 500);
        }
        return;
      }

      // ——— MERGE ———
      if (id.includes('merge')) {
        document.querySelector('section.card').classList.add('hidden');
        const wrappers = Array.from(filesContainer.children);
        const formData = new FormData();
        wrappers.forEach(w => {
          const f = files[w.dataset.index];
          formData.append('files', f, f.name);
        });

        const mapped = wrappers.map(w => {
          const grid = w.querySelector('.preview-grid');
          const pages = getSelectedPages(grid, true);
          const rots = pages.map(pg => {
            const el = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
            return Number(el.dataset.rotation) || 0;
          });
          return { pages, rots };
        });
        formData.append('pagesMap', JSON.stringify(mapped.map(m => m.pages)));
        formData.append('rotations', JSON.stringify(mapped.map(m => m.rots)));

        try {
          const res = await fetch('/api/merge?flatten=true', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken(), 'Accept': 'application/pdf' },
            body: formData
          });
          if (!res.ok) throw new Error('Falha no merge');
          const blob = await res.blob();

          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'merged.pdf';
          a.click();

          filesContainer.innerHTML = '';
          renderFiles([new File([blob], 'merged.pdf', { type: 'application/pdf' })]);
          mostrarMensagem('Juntado com sucesso!', 'sucesso');
        } catch (err) {
          mostrarMensagem(err.message || 'Erro no merge.', 'erro');
        } finally {
          document.querySelector('section.card').classList.remove('hidden');
        }
        return;
      }

      // ——— SPLIT ———
      if (id.includes('split')) {
        document.querySelector('section.card').classList.add('hidden');
        const grid = filesContainer.querySelector('.preview-grid');
        const pages = getSelectedPages(grid, true);
        if (!pages.length) return mostrarMensagem('Selecione ao menos uma página.', 'erro');
        const rots = pages.map(pg => {
          const el = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
          return Number(el.dataset.rotation) || 0;
        });

        const formData = new FormData();
        formData.append('file', files[0]);
        formData.append('pages', JSON.stringify(pages));
        formData.append('rotations', JSON.stringify(rots));

        xhrRequest('/api/split', formData, blob => {
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'split.pdf';
          a.click();
          mostrarMensagem('Dividido com sucesso!', 'sucesso');
          document.querySelector('section.card').classList.remove('hidden');
        });
        return;
      }

      // ——— COMPRESS ———
      if (id.includes('compress')) {
        document.querySelector('section.card').classList.add('hidden');
        files.forEach((file, i) => {
          const wrappers = filesContainer.children;
          const grid = wrappers[i].querySelector('.preview-grid');
          const rots = Array.from(grid.querySelectorAll('.page-wrapper.selected'))
            .map(p => Number(p.dataset.rotation) || 0);
          compressFile(file, rots).finally(() => {
            document.querySelector('section.card').classList.remove('hidden');
          });
        });
        return;
      }
    });
  });
});
