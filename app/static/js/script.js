import { previewPDF, getSelectedPages } from './preview.js';
import { createFileDropzone } from '../fileDropzone.js';
import {
  mostrarLoading,
  resetarProgresso,
  atualizarProgresso,
  mostrarMensagem,
  getCSRFToken,
} from './utils.js';
import { xhrRequest, compressFile } from './api.js';

const PDF_EXTS   = ['pdf'];
const IMG_EXTS   = ['jpg','jpeg','png','bmp','tiff'];
const DOC_EXTS   = ['doc','docx','odt','rtf','txt','html'];
const SHEET_EXTS = ['xls','xlsx','ods'];
const PPT_EXTS   = ['ppt','pptx','odp'];

// guarda o último PDF convertido (para baixar selecionadas)
let lastConvertedFile = null;

function getExt(name){ return name.split('.').pop().toLowerCase(); }

function showGenericPreview(file, container){
  if(!container) return;
  const reader = new FileReader();
  reader.onload = e => {
    container.innerHTML = `
      <img class="generic-preview-img" src="${e.target.result}" alt="${file.name}" />
      <div class="file-name">${file.name}</div>
    `;
  };
  reader.readAsDataURL(file);
}

function makePagesSortable(containerEl){
  if(window.Sortable && containerEl){
    Sortable.create(containerEl, { animation:150, ghostClass:'sortable-ghost', draggable:'.page-wrapper' });
  }
}

// só controla botões de navegação (prev/next)
function initPageControls(){
  document.querySelectorAll('button[id^="btn-prev-"], button[id^="btn-next-"]').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      const parts = btn.id.split('-');           // ex: ['btn','prev','convert']
      const prefix = parts.slice(2).join('-');   // 'convert'
      const container = document.querySelector(`#preview-${prefix}`);
      if(!container) return;
      const pages = Array.from(container.querySelectorAll('.page-wrapper'));
      const currentIndex = pages.findIndex(p=>!p.classList.contains('hidden'));
      if(currentIndex < 0) return;
      pages[currentIndex].classList.add('hidden');
      const nextIndex = btn.id.includes('prev') ? Math.max(0,currentIndex-1) : Math.min(pages.length-1,currentIndex+1);
      pages[nextIndex].classList.remove('hidden');
    });
  });

  // Mantém envio de páginas selecionadas em forms com data-prefix
  document.querySelectorAll('form[data-prefix]').forEach(form=>{
    form.addEventListener('submit', ()=>{
      const prefix = form.dataset.prefix;
      const container = document.querySelector(`#preview-${prefix}`);
      if(!container) return;
      const selected = Array.from(container.querySelectorAll('.page-wrapper.selected')).map(w=>w.dataset.page);
      const input = document.createElement('input');
      input.type='hidden'; input.name='pages'; input.value=JSON.stringify(selected);
      form.appendChild(input);
    });
  });
}

document.addEventListener('DOMContentLoaded', ()=>{
  console.log('[convert] script carregado');
  initPageControls();

  // --- controle de centralização do <main> ---
  const mainEl = document.querySelector('main');
  const setHasPreview = (on) => { if(mainEl) mainEl.classList.toggle('has-preview', !!on); };

  // Checagem inicial (se já existir preview no DOM ao carregar)
  (() => {
    const rc = document.querySelector('#preview-convert');
    if (rc) setHasPreview(!!rc.querySelector('.page-wrapper'));
  })();

  document.querySelectorAll('.dropzone').forEach(dzEl=>{
    const inputEl    = dzEl.querySelector('input[type="file"]');
    const spinnerSel = dzEl.dataset.spinner;     // ex: "#spinner-convert"
    const btnSel     = dzEl.dataset.action;      // ex: "#btn-convert-all"

    let previewSel = dzEl.dataset.preview || '';
    const isConverter = btnSel && btnSel.includes('convert');
    if(isConverter && !previewSel){ previewSel = '#preview-convert'; } // resultado

    const filesContainer    = previewSel ? document.querySelector(previewSel) : null;
    // inclui split no teste (além de merge/compress)
    const useFilesContainer = !!filesContainer && /(merge|compress|split)/.test(btnSel);
    let dz;

    console.log('[convert] init elements', {
      dropzoneFound: !!dzEl, inputFound: !!inputEl, btnSel, previewSel, spinnerSel, useFilesContainer
    });

    // Abrir seletor de arquivo sem “duplo clique”
    if (inputEl?.classList.contains('dz-input-overlay')) {
      inputEl.addEventListener('click', (e) => e.stopPropagation());
    } else {
      dzEl.addEventListener('click', () => inputEl?.click());
    }

    const exts = dzEl.dataset.extensions
      ? dzEl.dataset.extensions.split(',').map(e=>e.replace(/^\./,''))
      : ['pdf'];
    const multiple = dzEl.dataset.multiple === 'true';

    const btn = document.querySelector(btnSel);
    const setBtnState = (len)=>{ if(btn) btn.disabled = len === 0; };

    function renderFiles(files){
      setBtnState(files.length);
      if(!useFilesContainer) return; // Converter não mostra thumbs de ENTRADA
      filesContainer.innerHTML = '';
      if(!files.length) return;

      files.forEach((file, idx)=>{
        const fw = document.createElement('div');
        fw.classList.add('file-wrapper');
        fw.dataset.index = idx;
        // 🔥 removemos a LUPA (view-pdf)
        fw.innerHTML = `
          <div class="file-controls">
            <span class="file-badge">Arquivo ${idx + 1}</span>
            <button class="remove-file" aria-label="Remover arquivo">×</button>
          </div>
          <div class="file-name">${file.name}</div>
          <div class="preview-grid"></div>
        `;
        fw.querySelector('.remove-file').addEventListener('click', e=>{
          e.stopPropagation(); dz.removeFile(idx);
        });
        filesContainer.appendChild(fw);
        const previewGrid = fw.querySelector('.preview-grid');
        const ext = getExt(file.name);
        if(!PDF_EXTS.includes(ext)){ showGenericPreview(file, previewGrid); }
        else { previewPDF(file, previewGrid, spinnerSel, btnSel); makePagesSortable(previewGrid); }
      });
    }

    // Dropzone
    dz = createFileDropzone({ dropzone: dzEl, input: inputEl, extensions: exts, multiple, onChange: renderFiles });

    // >>> FONTE ÚNICA DOS ARQUIVOS (fallback pro input)
    const getCurrentFiles = ()=>{
      const dzFiles = (dz && typeof dz.getFiles === 'function') ? dz.getFiles() : [];
      const inputFiles = Array.from(inputEl?.files || []);
      return (dzFiles && dzFiles.length) ? dzFiles : inputFiles;
    };

    // Botão acompanha o estado
    setBtnState(getCurrentFiles().length);
    inputEl?.addEventListener('change', ()=>{
      setBtnState(getCurrentFiles().length);
      renderFiles(getCurrentFiles()); // Converter não mostra thumbs de ENTRADA
    });

    // >>> CLEAR STATE (genérico para todas as telas)
    const clearAllState = () => {
      try {
        if (dz && typeof dz.clear === 'function') {
          dz.clear();
        } else if (dz && typeof dz.getFiles === 'function' && typeof dz.removeFile === 'function') {
          const current = dz.getFiles();
          for (let i = current.length - 1; i >= 0; i--) dz.removeFile(i);
        }
      } catch (e) {
        console.warn('[clear] dz failed', e);
      }

      if (inputEl) inputEl.value = '';

      // Zera listas/preview de entrada quando existirem (merge/split/compress)
      if (useFilesContainer && filesContainer) filesContainer.innerHTML = '';

      // Zera resultado do CONVERTER
      if (isConverter) {
        const resultContainer = document.querySelector('#preview-convert');
        if (resultContainer) resultContainer.innerHTML = '';

        const linkWrap = document.getElementById('link-download-container');
        const link = document.getElementById('download-link');
        if (link?.href?.startsWith('blob:')) {
          try { URL.revokeObjectURL(link.href); } catch {}
        }
        link?.removeAttribute('href');
        linkWrap?.classList.add('hidden');

        lastConvertedFile = null;
        setHasPreview(false); // <<< centraliza o card de novo
      }

      if (btn) btn.disabled = true;
      resetarProgresso();
    };

    // Eventos de clear (compat: novo e antigo)
    const onClearEvent = () => clearAllState();
    document.addEventListener('gv:clear-files', onClearEvent, { passive: true });
    document.addEventListener('gv:clear-converter', onClearEvent, { passive: true });
    dzEl.addEventListener('gv:teardown', () => {
      document.removeEventListener('gv:clear-files', onClearEvent);
      document.removeEventListener('gv:clear-converter', onClearEvent);
    });

    // Botão "Limpar todos"
    const clearBtn = document.getElementById('btn-clear-all');
    if (clearBtn && !clearBtn.dataset.bound) {
      clearBtn.addEventListener('click', () => clearAllState(), { passive: true });
      clearBtn.dataset.bound = '1';
    }

    // Clique principal (converter/merge/split/compress)
    btn?.addEventListener('click', async e=>{
      e.preventDefault();
      const files = getCurrentFiles();
      console.log('[convert] click, files=', files.map(f=>f.name));
      if(!files.length) return mostrarMensagem('Selecione um arquivo.', 'erro');

      const id = btn.id;

      // ——— CONVERT ———
      if (id.includes('convert')) {
        document.querySelector('section.card')?.classList.add('hidden');
        mostrarLoading(spinnerSel, true);
        resetarProgresso();

        const resultContainer = document.querySelector('#preview-convert');
        if (resultContainer) resultContainer.innerHTML = '';
        const linkWrap = document.getElementById('link-download-container');
        linkWrap?.classList.add('hidden');
        setHasPreview(false); // ainda não há preview

        const formData = new FormData();
        formData.append('file', files[0]);

        try {
          console.log('[convert] enviando para /api/convert', { file: files[0]?.name });
          const res = await fetch('/api/convert', {
            method: 'POST',
            body: formData,
            headers: { 'X-CSRFToken': getCSRFToken() }
          });
          if (!res.ok) {
            const txt = await res.text().catch(()=> '');
            console.error('[convert] erro HTTP', res.status, txt);
            throw new Error('Erro ao converter.');
          }

          const blob = await res.blob();
          mostrarMensagem('Convertido com sucesso!', 'sucesso');
          atualizarProgresso(100);

          const url = URL.createObjectURL(blob);
          const link = document.getElementById('download-link');
          const suggestedName = files[0].name.replace(/\.[^\.]+$/, '') + '.pdf';
          if (link) {
            link.href = url;
            link.download = suggestedName;
          }
          linkWrap?.classList.remove('hidden');

          if (resultContainer) {
            // guarda o arquivo pra futuros downloads de páginas selecionadas
            lastConvertedFile = new File([blob], suggestedName, { type: 'application/pdf' });

            await previewPDF(lastConvertedFile, resultContainer, spinnerSel, btnSel);
            setHasPreview(!!resultContainer.querySelector('.page-wrapper')); // <<< ativa centralização off
            resultContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
          }
        } catch (err) {
          console.error('[convert] falha na conversão', err);
          mostrarMensagem(err.message || 'Falha na conversão.', 'erro');
        } finally {
          mostrarLoading(spinnerSel, false);
          document.querySelector('section.card')?.classList.remove('hidden');
          setTimeout(resetarProgresso, 500);
        }
        return;
      }

      // ——— MERGE ———
      if(id.includes('merge')){
        if(!useFilesContainer) return mostrarMensagem('Área de arquivos não encontrada.', 'erro');
        document.querySelector('section.card')?.classList.add('hidden');

        const wrappers = Array.from(filesContainer.children);
        const formData = new FormData();
        wrappers.forEach(w=>{
          const f = files[w.dataset.index];
          formData.append('files', f, f.name);
        });

        const mapped = wrappers.map(w=>{
          const grid = w.querySelector('.preview-grid');
          const pages = getSelectedPages(grid, true);
          const rots = pages.map(pg=>{
            const el = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
            return Number(el?.dataset.rotation) || 0;
          });
          return { pages, rots };
        });
        formData.append('pagesMap', JSON.stringify(mapped.map(m=>m.pages)));
        formData.append('rotations', JSON.stringify(mapped.map(m=>m.rots)));

        try{
          const res = await fetch('/api/merge?flatten=true', {
            method: 'POST',
            headers: { 'X-CSRFToken': getCSRFToken(), 'Accept': 'application/pdf' },
            body: formData
          });
          if(!res.ok) throw new Error('Falha no merge');
          const blob = await res.blob();

          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'merged.pdf';
          a.click();

          filesContainer.innerHTML = '';
          renderFiles([new File([blob], 'merged.pdf', { type: 'application/pdf' })]);
          mostrarMensagem('Juntado com sucesso!', 'sucesso');
        }catch(err){
          mostrarMensagem(err.message || 'Erro no merge.', 'erro');
        }finally{
          document.querySelector('section.card')?.classList.remove('hidden');
        }
        return;
      }

      // ——— SPLIT ———
      if(id.includes('split')){
        if(!useFilesContainer) return mostrarMensagem('Área de arquivos não encontrada.', 'erro');
        document.querySelector('section.card')?.classList.add('hidden');

        const grid = filesContainer.querySelector('.preview-grid');
        const pages = getSelectedPages(grid, true);
        if(!pages.length) return mostrarMensagem('Selecione ao menos uma página.', 'erro');
        const rots = pages.map(pg=>{
          const el = grid.querySelector(`.page-wrapper[data-page="${pg}"]`);
          return Number(el?.dataset.rotation) || 0;
        });

        const formData = new FormData();
        formData.append('file', files[0]);
        formData.append('pages', JSON.stringify(pages));
        formData.append('rotations', JSON.stringify(rots));

        xhrRequest('/api/split', formData, blob=>{
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = 'split.pdf';
          a.click();
          mostrarMensagem('Dividido com sucesso!', 'sucesso');
          document.querySelector('section.card')?.classList.remove('hidden');
        });
        return;
      }

      // ——— COMPRESS ———
      if(id.includes('compress')){
        if(!useFilesContainer) return mostrarMensagem('Área de arquivos não encontrada.', 'erro');
        document.querySelector('section.card')?.classList.add('hidden');

        files.forEach((file, i)=>{
          const wrappers = filesContainer.children;
          const grid = wrappers[i].querySelector('.preview-grid');
          const rots = Array.from(grid.querySelectorAll('.page-wrapper.selected'))
            .map(p=>Number(p.dataset.rotation) || 0);
          compressFile(file, rots).finally(()=>{
            document.querySelector('section.card')?.classList.remove('hidden');
          });
        });
        return;
      }
    });
  });

  // DOWNLOAD INTELIGENTE: decide completo vs selecionadas
  document.addEventListener('click', (e) => {
    const link = e.target.closest('#download-link');
    if (!link) return;

    const resultContainer = document.querySelector('#preview-convert');
    if (!resultContainer) return; // fora da tela de conversão

    e.preventDefault();

    const pages = getSelectedPages(resultContainer, true);

    // Sem seleção => baixa o PDF inteiro (comportamento padrão)
    if (!pages.length) {
      window.open(link.href, '_blank');
      return;
    }

    // rotações só das páginas selecionadas
    const rotations = pages.map(pg => {
      const el = resultContainer.querySelector(`.page-wrapper[data-page="${pg}"]`);
      return Number(el?.dataset.rotation) || 0;
    });

    if (!lastConvertedFile) {
      // fallback: não guardamos o arquivo, usa link como está
      window.open(link.href, '_blank');
      return;
    }

    // baixa apenas selecionadas (com rotação aplicada, se houver)
    const formData = new FormData();
    formData.append('file', lastConvertedFile);
    formData.append('pages', JSON.stringify(pages));
    formData.append('rotations', JSON.stringify(rotations));

    mostrarLoading('#spinner-convert', true);
    xhrRequest('/api/split', formData, (blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = lastConvertedFile.name.replace(/\.pdf$/i, '') + '-selecionadas.pdf';
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 3000);
      mostrarLoading('#spinner-convert', false);
      mostrarMensagem('Páginas selecionadas baixadas!', 'sucesso');
    });
  }, { passive: false });

  // limpa blob do link ao sair da página
  window.addEventListener('beforeunload', () => {
    const link = document.getElementById('download-link');
    if (link?.href?.startsWith('blob:')) {
      try { URL.revokeObjectURL(link.href); } catch {}
    }
  });
});