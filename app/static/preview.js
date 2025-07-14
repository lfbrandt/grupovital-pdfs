(function(){
  async function previewPDF(file){
    const container = document.getElementById('preview-container');
    if(!container) return;

    container.innerHTML = '';
    const loading = document.createElement('div');
    loading.id = 'preview-loading';
    loading.textContent = 'Carregando pré-visualização…';
    container.appendChild(loading);

    try {
      const buffer = await file.arrayBuffer();
      const pdf = await pdfjsLib.getDocument(new Uint8Array(buffer)).promise;

      container.removeChild(loading);
      const totalPages = pdf.numPages;
      let current = 1;

      async function renderPage(pageNum){
        const page = await pdf.getPage(pageNum);
        const viewport = page.getViewport({scale:1});
        const wrapper = document.createElement('div');
        wrapper.className = 'thumb';

        const canvas = document.createElement('canvas');
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        canvas.setAttribute('aria-label', `Página ${pageNum} do PDF`);
        wrapper.appendChild(canvas);

        const label = document.createElement('span');
        label.className = 'page-number';
        label.textContent = `Pg ${pageNum}`;
        wrapper.appendChild(label);

        const ctx = canvas.getContext('2d');
        page.render({canvasContext: ctx, viewport});

        const delBtn = document.createElement('button');
        delBtn.className = 'thumb-del';
        delBtn.textContent = '🗑';
        delBtn.addEventListener('click', () => wrapper.remove());

        const downBtn = document.createElement('button');
        downBtn.className = 'thumb-download';
        downBtn.textContent = '⬇️';
        downBtn.addEventListener('click', () => {
          canvas.toBlob(b => {
            const a = document.createElement('a');
            a.href = URL.createObjectURL(b);
            a.download = `page-${pageNum}.png`;
            a.click();
            URL.revokeObjectURL(a.href);
          });
        });

        wrapper.appendChild(delBtn);
        wrapper.appendChild(downBtn);

        container.insertBefore(wrapper, loadMoreBtn);
      }

      function loadMore(){
        const max = current + 4;
        while(current <= totalPages && current <= max){
          renderPage(current);
          current++;
        }
        if(current > totalPages){
          loadMoreBtn.remove();
        }
      }

      const loadMoreBtn = document.createElement('button');
      loadMoreBtn.className = 'load-more-btn';
      loadMoreBtn.textContent = 'Carregar mais páginas';
      loadMoreBtn.addEventListener('click', loadMore);

      loadMore();
      if(totalPages > 5) container.appendChild(loadMoreBtn);
    } catch(err) {
      loading.textContent = 'Não foi possível pré-visualizar este PDF';
      console.error(err);
    }
  }

  window.previewPDF = previewPDF;
})();
