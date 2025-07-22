import { createFileDropzone } from '../fileDropzone.js';
import { previewPDF } from './preview.js';

export class PdfWidget {
  constructor({ dropzoneEl, previewSel, spinnerSel, btnSel, action }) {
    this.dropzoneEl = dropzoneEl;
    this.previewSel = previewSel;
    this.spinnerSel = spinnerSel;
    this.btnSel = btnSel;
    this.action = action;
    this.dz = null;
    this.previewEl = null;
  }

  init() {
    const inputEl  = this.dropzoneEl.querySelector('input[type="file"]');
    const previewEl = document.querySelector(this.previewSel);
    const listSel  = this.dropzoneEl.dataset.list;
    const listEl   = listSel ? document.querySelector(listSel) : null;
    const btn      = document.querySelector(this.btnSel);
    if (btn) btn.disabled = true;

    this.previewEl = previewEl;

    const exts = this.dropzoneEl.dataset.extensions
      ? this.dropzoneEl.dataset.extensions.split(',').map(e => e.replace(/^\./, ''))
      : ['pdf'];
    const allowMultiple = this.dropzoneEl.dataset.multiple === 'true';

    this.dz = createFileDropzone({
      dropzone: this.dropzoneEl,
      input: inputEl,
      list: listEl,
      extensions: exts,
      multiple: allowMultiple,
      onChange: files => {
        this.renderFiles(files);
        if (btn) btn.disabled = files.length === 0;
      }
    });

    if (btn) {
      btn.addEventListener('click', e => {
        e.preventDefault();
        if (this.action) this.action(this.dz.getFiles(), this.previewEl, this);
      });
    }
  }

  renderFiles(files) {
    if (!this.previewEl) return;
    this.previewEl.innerHTML = '';
    files.forEach((file, idx) => {
      const container = document.createElement('div');
      container.classList.add('preview-wrapper');
      this.previewEl.appendChild(container);
      previewPDF(file, container, this.spinnerSel, this.btnSel);

    });
  }

  clear() {
    if (this.dz) this.dz.clear();
    if (this.previewEl) this.previewEl.innerHTML = '';
  }
}
