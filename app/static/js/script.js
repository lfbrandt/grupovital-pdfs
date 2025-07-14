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
    const inputSel   = dzEl.dataset.input;
    const listSel    = dzEl.dataset.list;
    const previewSel = dzEl.dataset.preview;
    const spinnerSel = dzEl.dataset.spinner;
    const btnSel     = dzEl.dataset.action;

    const dz = createFileDropzone({
      dropzone: dzEl,
      input: document.querySelector(inputSel),
      list: document.querySelector(listSel),
      extensions: dzEl.dataset.extensions ? dzEl.dataset.extensions.split(',') : ['.pdf'],
      multiple: dzEl.dataset.multiple === 'true',
      onChange: files => {
        if (files.length) {
          previewPDF(files[0], previewSel, spinnerSel, btnSel);
        } else {
          document.querySelector(previewSel).innerHTML = '';
          clearSelection();
          document.querySelector(btnSel).disabled = true;
        }
      }
    });

    document.querySelector(btnSel).addEventListener('click', e => {
      e.preventDefault();
      const files = dz.getFiles();
      if (!files.length) return mostrarMensagem('Selecione um PDF.', 'erro');
      const pages = getSelectedPages();
      if (!pages.length) return mostrarMensagem('Marque ao menos uma página.', 'erro');

      const form = new FormData();
      form.append('file', files[0]);
      form.append('pages', JSON.stringify(pages));

      fetch('/api/merge', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken() },
        body: form
      })
      .then(res => {
        if (!res.ok) throw new Error('Erro ao gerar PDF.');
        return res.blob();
      })
      .then(blob => {
        const url = URL.createObjectURL(blob);
        const a   = document.createElement('a');
        a.href    = url;
        a.download= 'pdf_selecionado.pdf';
        document.body.appendChild(a);
        a.click();
        a.remove();
      })
      .catch(err => mostrarMensagem(err.message, 'erro'));
    });
  });
});
