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
  }

  init() {
    const inputEl = this.dropzoneEl.querySelector('input[type="file"]');
    const previewEl = document.querySelector(this.previewSel);
    const btn = document.querySelector(this.btnSel);

    const exts = this.dropzoneEl.dataset.extensions
      ? this.dropzoneEl.dataset.extensions.split(',').map(e => e.replace(/^\./, ''))
      : ['pdf'];
    const allowMultiple = this.dropzoneEl.dataset.multiple === 'true';

    this.dz = createFileDropzone({
      dropzone: this.dropzoneEl,
      input: inputEl,
      extensions: exts,
      multiple: allowMultiple,
      onChange: files => this.renderFiles(files, previewEl)
    });

    if (btn) {
      btn.addEventListener('click', e => {
        e.preventDefault();
        if (this.action) this.action(this.dz.getFiles(), previewEl);
      });
    }
  }

  renderFiles(files, previewEl) {
    if (!previewEl) return;
    previewEl.innerHTML = '';
    files.forEach(file => {
      const container = document.createElement('div');
      container.classList.add('preview-wrapper');
      previewEl.appendChild(container);
      previewPDF(file, container, this.spinnerSel, this.btnSel);
    });
  }
}
