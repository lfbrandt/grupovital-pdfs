(function(){
  let observer;

  function clearPreview(){
    const container = document.getElementById('preview-container');
    if(container) container.innerHTML = '';
  }

  function showSpinner(){
    const container = document.getElementById('preview-container');
    const spinner = document.getElementById('loading-spinner');
    if(container && spinner){
      container.appendChild(spinner);
      spinner.classList.add('overlay');
      spinner.classList.remove('hidden');
    }
  }

  function hideSpinner(){
    const spinner = document.getElementById('loading-spinner');
    if(spinner){
      spinner.classList.add('hidden');
      spinner.classList.remove('overlay');
      if(spinner.parentElement && spinner.parentElement.id === 'preview-container'){
        spinner.parentElement.removeChild(spinner);
        document.body.appendChild(spinner);
      }
    }
  }

  async function renderPage(pdf, pageNum, total){
    const container = document.getElementById('preview-container');
    if(!container) return;
    const page = await pdf.getPage(pageNum);
    const viewport = page.getViewport({scale:1});

    const wrapper = document.createElement('div');
    wrapper.className = 'page-wrapper';

    const canvas = document.createElement('canvas');
    canvas.width = viewport.width;
    canvas.height = viewport.height;
    wrapper.appendChild(canvas);

    const badge = document.createElement('div');
    badge.className = 'page-badge';
    badge.textContent = `Pg ${pageNum}`;
    wrapper.appendChild(badge);

    const sr = document.createElement('span');
    sr.className = 'sr-only';
    sr.textContent = `Página ${pageNum} de ${total}`;
    wrapper.appendChild(sr);

    container.insertBefore(wrapper, container.lastElementChild);

    const ctx = canvas.getContext('2d');
    await page.render({canvasContext: ctx, viewport}).promise;
  }

  async function previewPDF(file){
    const container = document.getElementById('preview-container');
    if(!container) return false;
    clearPreview();
    showSpinner();

    try{
      const buffer = await file.arrayBuffer();
      const pdf = await pdfjsLib.getDocument(new Uint8Array(buffer)).promise;
      const totalPages = pdf.numPages;
      let nextPage = 1;

      const sentinel = document.createElement('div');
      container.appendChild(sentinel);

      async function loadNextBatch(){
        const max = Math.min(totalPages, nextPage + 4);
        for(let i = nextPage; i <= max; i++){
          await renderPage(pdf, i, totalPages);
        }
        nextPage = max + 1;
        if(nextPage > totalPages){
          sentinel.remove();
          if(observer) observer.disconnect();
        }
      }

      await loadNextBatch();

      observer = new IntersectionObserver(entries => {
        if(entries.some(e => e.isIntersecting)) loadNextBatch();
      });
      observer.observe(sentinel);

      return true;
    }catch(err){
      clearPreview();
      mostrarMensagem('PDF inválido para preview', 'erro');
      console.error(err);
      return false;
    }finally{
      hideSpinner();
    }
  }

  window.previewPDF = previewPDF;
  window.clearPreview = clearPreview;
})();
