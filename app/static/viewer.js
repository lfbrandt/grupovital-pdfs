const url = document.getElementById('pdf-render').dataset.url;

let pdfDoc = null,
    pageNum = 1,
    isRendering = false,
    pendingPage = null;

const scale = 1.5,
      canvas = document.getElementById('pdf-render'),
      ctx = canvas.getContext('2d');

function renderPage(num) {
  isRendering = true;
  pdfDoc.getPage(num).then(page => {
    const viewport = page.getViewport({ scale });
    canvas.height = viewport.height;
    canvas.width  = viewport.width;
    return page.render({ canvasContext: ctx, viewport }).promise;
  }).then(() => {
    isRendering = false;
    if (pendingPage !== null) {
      renderPage(pendingPage);
      pendingPage = null;
    }
  });
  document.getElementById('page_num').textContent = num;
}

function queueRender(num) {
  isRendering ? pendingPage = num : renderPage(num);
}

document.getElementById('prev').addEventListener('click', () => {
  if (pageNum <= 1) return;
  pageNum--;
  queueRender(pageNum);
});

document.getElementById('next').addEventListener('click', () => {
  if (pageNum >= pdfDoc.numPages) return;
  pageNum++;
  queueRender(pageNum);
});

pdfjsLib.getDocument(url).promise
  .then(doc => {
    pdfDoc = doc;
    document.getElementById('page_count').textContent = pdfDoc.numPages;
    renderPage(pageNum);
  })
  .catch(err => console.error('Erro ao carregar PDF:', err));

