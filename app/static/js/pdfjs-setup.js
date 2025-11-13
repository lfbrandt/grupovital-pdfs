// pdfjs-setup.js — configura pdf.js sem inline e compatível com CSP.
// Define workerSrc e standardFontDataUrl (evita warnings de fontes).

(function () {
  'use strict';

  function setup() {
    if (!window.pdfjsLib || !window.pdfjsLib.GlobalWorkerOptions) return false;

    window.pdfjsLib.GlobalWorkerOptions.workerSrc =
      window.__PDF_WORKER_SRC ||
      (document.currentScript && document.currentScript.getAttribute('data-worker-src')) ||
      '/static/pdfjs/pdf.worker.min.js';

    // Copie a pasta "standard_fonts" da mesma versão do pdf.js para este caminho:
    window.pdfjsLib.GlobalWorkerOptions.standardFontDataUrl =
      window.__PDF_STANDARD_FONTS_URL ||
      (document.currentScript && document.currentScript.getAttribute('data-standard-fonts')) ||
      '/static/pdfjs/standard_fonts/';

    return true;
  }

  if (!setup()) {
    window.addEventListener('load', setup, { once: true });
  }
})();