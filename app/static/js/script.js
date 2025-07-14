import { previewPDF, clearPreview } from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';

document.addEventListener('DOMContentLoaded', () => {
  const configs = [
    {
      dropzoneEl: '#dropzone-merge',
      inputEl: '#input-merge',
      listEl: '#list-merge',
      previewEl: '#preview-merge',
      spinnerEl: '#spinner-merge',
      actionBtnEl: '#btn-merge',
      extensions: ['.pdf'],
      multiple: true,
    },
    {
      dropzoneEl: '#dropzone-split',
      inputEl: '#input-split',
      listEl: '#list-split',
      previewEl: '#preview-split',
      spinnerEl: '#spinner-split',
      actionBtnEl: '#btn-split',
      extensions: ['.pdf'],
      multiple: false,
    },
    {
      dropzoneEl: '#dropzone-compress',
      inputEl: '#input-compress',
      listEl: '#list-compress',
      previewEl: '#preview-compress',
      spinnerEl: '#spinner-compress',
      actionBtnEl: '#btn-compress',
      extensions: ['.pdf'],
      multiple: true,
    }
  ];

  configs.forEach(cfg => {
    const dz = createFileDropzone({
      dropzone: document.querySelector(cfg.dropzoneEl),
      input: document.querySelector(cfg.inputEl),
      list: document.querySelector(cfg.listEl),
      extensions: cfg.extensions,
      multiple: cfg.multiple,
      onChange: files => {
        if (files.length) {
          previewPDF(files[0], cfg.previewEl, cfg.spinnerEl, cfg.actionBtnEl);
        } else {
          clearPreview(cfg.previewEl, cfg.actionBtnEl);
        }
      }
    });
  });
});
