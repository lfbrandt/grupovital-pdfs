import { previewPDF, clearSelection, getSelectedPages } from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';

function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

function mostrarMensagem(msg, tipo = 'sucesso') {
  const el = document.getElementById('mensagem-feedback');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('sucesso', 'erro', 'hidden');
  el.classList.add(tipo);
  setTimeout(() => el.classList.add('hidden'), 5000);
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.dropzone').forEach(dzEl => {
    const inputSel     = dzEl.dataset.input;
    const listSel      = dzEl.dataset.list;
    const previewSel   = dzEl.dataset.preview;
    const spinnerSel   = dzEl.dataset.spinner;
    const btnSel       = dzEl.dataset.action;
    const endpoint     = dzEl.dataset.endpoint;
    const field        = dzEl.dataset.field || 'file';
    const downloadName = dzEl.dataset.download || 'resultado.pdf';
    const multiple     = dzEl.dataset.multiple === 'true';
    const selectPages  = dzEl.dataset.selectPages === 'true';

    const dz = createFileDropzone({
      dropzone: dzEl,
      input: document.querySelector(inputSel),
      list: document.querySelector(listSel),
      extensions: dzEl.dataset.extensions ? dzEl.dataset.extensions.split(',') : [],
      multiple,
      onChange: files => {
        if (previewSel) {
          if (files.length && files[0].name.toLowerCase().endsWith('.pdf')) {
            previewPDF(files[0], previewSel, spinnerSel, btnSel);
          } else {
            document.querySelector(previewSel).innerHTML = '';
            clearSelection();
            document.querySelector(btnSel).disabled = !files.length;
          }
        } else if (btnSel) {
          document.querySelector(btnSel).disabled = !files.length;
        }
      }
    });

    const actionBtn = document.querySelector(btnSel);
    if (!actionBtn || !endpoint) return;

    actionBtn.addEventListener('click', e => {
      e.preventDefault();
      const files = dz.getFiles();
      if (!files.length) return mostrarMensagem('Selecione um arquivo.', 'erro');

      const form = new FormData();
      if (selectPages) {
        const pages = getSelectedPages();
        if (!pages.length) return mostrarMensagem('Marque ao menos uma página.', 'erro');
        form.append('file', files[0]);
        form.append('pages', JSON.stringify(pages));
      } else if (field === 'files' && multiple) {
        files.forEach(f => form.append('files', f));
      } else if (multiple && files.length > 1 && field === 'file') {
        form.append('file', files[0]);
      } else {
        form.append(field, files[0]);
      }

      fetch(endpoint, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken() },
        body: form
      })
      .then(res => {
        if (!res.ok) throw new Error('Erro ao processar arquivo.');
        return res.blob();
      })
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const a   = document.createElement('a');
        a.href = url;
        if (dzEl.dataset.downloadAutoName === 'true' && files.length === 1) {
          const base = files[0].name.replace(/\.[^/.]+$/, '');
          a.download = `${base}.pdf`;
        } else {
          a.download = downloadName;
        }
        document.body.appendChild(a);
        a.click();
        a.remove();
      })
      .catch(err => mostrarMensagem(err.message, 'erro'));
    });
  });
});
