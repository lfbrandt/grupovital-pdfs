// static/js/pdfjs-setup.js
(function () {
  function setWorker() {
    if (window.pdfjsLib && window.pdfjsLib.GlobalWorkerOptions) {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = (
        window.__PDF_WORKER_SRC ||
        (document.currentScript && document.currentScript.getAttribute('data-worker-src')) ||
        '/static/pdfjs/pdf.worker.min.js' // <- agora bate com sua árvore de pastas
      );
      return true;
    }
    return false;
  }
  if (setWorker()) return;
  window.addEventListener('load', setWorker, { once: true });
})();