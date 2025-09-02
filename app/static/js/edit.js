// app/static/js/edit.js
// Orquestra o editor: upload, preview, modos, aplicar e integração com o page-editor.

import { previewPDF, collectOrganizePayload, setPreviewSessionId } from './preview.js';
import { getCSRFToken } from './utils.js';
import * as PageEditorMod from './page-editor.js';

(function () {
  'use strict';

  const $  = (sel, ctx = document) => ctx.querySelector(sel);

  const dropzoneEl  = $('#dropzone-edit');
  const fileInput   = $('#input-edit');
  const previewSel  = '#preview-edit';
  const spinnerSel  = '#spinner-edit';
  const previewEl   = $(previewSel);
  const spinnerEl   = $(spinnerSel);
  const btnApply    = $('#btn-apply');
  const btnDownload = $('#btn-download');
  const modeSelect  = $('#mode-select');
  const help        = $('#mode-help');

  // 🔹 NOVO: stage do PDF grande dentro da página /edit
  const stageEl     = $('#edit-stage');

  // editor opcional: aceita export default OU named openPageEditor
  const openPageEditor = (PageEditorMod && (PageEditorMod.openPageEditor ?? PageEditorMod.default)) || null;

  let sessionId = null;
  let currentPage = 1; // 1-based

  // Helpers UI
  const setHelp = (t) => { if (help) help.textContent = t || ''; };
  const enableApply = (on) => { if (btnApply) btnApply.disabled = !on; };
  const showDownload = (url) => { if (btnDownload && url) { btnDownload.href = url; btnDownload.classList.remove('is-hidden'); } };
  const pdfUrl = () => (sessionId ? `/api/edit/file/${sessionId}?t=${Date.now()}` : null);
  const spin = (on) => {
    if (!spinnerEl) return;
    spinnerEl.classList.toggle('hidden', !on);
    spinnerEl.setAttribute('aria-hidden', on ? 'false' : 'true');
  };

  // ================= Upload =================
  function bindDropzone(){
    dropzoneEl?.addEventListener('click', () => fileInput?.click());
    dropzoneEl?.addEventListener('dragover', (e) => { e.preventDefault(); dropzoneEl.classList.add('is-dragover'); });
    dropzoneEl?.addEventListener('dragleave', () => dropzoneEl.classList.remove('is-dragover'));
    dropzoneEl?.addEventListener('drop', (e) => {
      e.preventDefault(); dropzoneEl.classList.remove('is-dragover');
      const f = e.dataTransfer?.files?.[0]; if (f) doUpload(f);
    });
    fileInput?.addEventListener('change', () => {
      const f = fileInput.files?.[0]; if (f) doUpload(f);
    });
  }

  async function doUpload(file){
    if (!file || (!file.name?.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf')) {
      alert('Envie um PDF.');
      return;
    }
    const fd = new FormData(); fd.append('file', file);

    spin(true);
    try{
      const resp = await fetch('/api/edit/upload', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken() },
        body: fd,
      });
      const data = await resp.json().catch(()=> ({}));
      if(!resp.ok) throw new Error(data?.error || 'Falha no upload');
      sessionId = data.session_id;

      setPreviewSessionId(sessionId, previewSel);
      await renderPreview();           // cria o grid
      currentPage = 1;                 // reseta seleção
      await renderStageForPage(1);     // 🔹 renderiza o PDF grande (página 1)
      enableApply(true);
      setHelp('PDF carregado. Reordene, gire, exclua ou abra a página para tapar/texto (✎).');
      btnDownload?.classList.add('is-hidden');
    } catch(e){
      console.error(e);
      alert('Erro no upload: ' + e.message);
    } finally {
      spin(false);
    }
  }

  // ================= Preview (grid) =================
  async function renderPreview(){
    if (!sessionId) return;
    const url = pdfUrl();
    if (!url) return;
    await previewPDF(url, previewSel, spinnerSel);
  }

  // ====================== STAGE (PDF grande) ======================
  function ensureStageCanvas(){
    if (!stageEl) return null;
    let canvas = stageEl.querySelector('canvas.pe-canvas');
    if (!canvas){
      canvas = document.createElement('canvas');
      canvas.className = 'pe-canvas';
      stageEl.appendChild(canvas);

      // overlay opcional (para futuros cursores/guia)
      const overlay = document.createElement('div');
      overlay.className = 'pe-overlay';
      stageEl.appendChild(overlay);
    }
    return canvas;
  }

  async function blobToBitmap(blob){
    if ('createImageBitmap' in window){
      return await createImageBitmap(blob);
    }
    // Fallback: HTMLImageElement
    const url = URL.createObjectURL(blob);
    try {
      const img = await new Promise((resolve, reject)=>{
        const im = new Image();
        im.onload = ()=> resolve(im);
        im.onerror = reject;
        im.src = url;
      });
      return img;
    } finally { URL.revokeObjectURL(url); }
  }

  async function renderStageForPage(pageIndex){
    if (!stageEl || !sessionId) return;
    const canvas = ensureStageCanvas();
    if (!canvas) return;

    // largura alvo (coerente com o SCSS: máx 980px ou 92vw)
    const targetCssW = Math.max(
      320,
      Math.min(980, Math.floor(window.innerWidth * 0.92), stageEl.clientWidth || 980)
    );

    // nitidez por DPR, sem exagero
    const dpr = Math.max(1, Math.min(3.5, (window.devicePixelRatio || 1) * 1.4));
    const scaleParam = dpr.toFixed(2);

    try{
      spin(true);
      const resp = await fetch(`/api/edit/page-image/${sessionId}/${pageIndex}?scale=${scaleParam}`);
      if (!resp.ok) throw new Error('Falha ao obter imagem da página');
      const blob = await resp.blob();
      const bmp  = await blobToBitmap(blob);

      // mantém proporção
      const ratio = bmp.height / bmp.width;
      const cssW = Math.min(targetCssW, bmp.width); // evita esticar além do bitmap
      const cssH = Math.round(cssW * ratio);

      // canvas físico (px reais) + mapeamento DPR
      canvas.style.width  = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
      canvas.width  = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);

      const ctx = canvas.getContext('2d', { alpha: false });
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);
      ctx.drawImage(bmp, 0, 0, cssW, cssH);

      currentPage = pageIndex;
    } catch (err){
      console.error(err);
    } finally {
      spin(false);
    }
  }

  // delegação para escolher página pelo clique na miniatura
  function bindStageSelection(){
    if (!previewEl) return;

    previewEl.addEventListener('click', (ev) => {
      const isControl = ev.target?.closest?.('.file-controls, .crop-page, [data-act]');
      if (isControl) return; // não roubar clique de botões

      const card = ev.target?.closest?.('.page-wrapper');
      if (!card) return;

      let idx = parseInt(card.dataset.page || '1', 10);
      if (!Number.isFinite(idx) || idx < 1) idx = 1;

      renderStageForPage(idx);
    });
  }

  // re-render no resize para manter centralização e escala
  function bindResizeRerender(){
    let raf = 0;
    window.addEventListener('resize', () => {
      if (!currentPage || !sessionId) return;
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => renderStageForPage(currentPage));
    });
  }

  // ================= Modos =================
  function bindModes(){
    const hints = {
      all:    'Use os controles nas miniaturas para arrastar, girar, excluir e abrir o ✎. Depois clique em Aplicar.',
      redact: 'Abra o ✎ na miniatura para desenhar caixas de ocultação e salve.',
      text:   'Abra o ✎ na miniatura para adicionar texto e salve.',
      ocr:    'Executa OCR no servidor.'
    };
    setHelp(hints[modeSelect?.value || 'all'] || '');
    modeSelect?.addEventListener('change', ()=> setHelp(hints[modeSelect.value] || ''));

    // Abrir o editor com imagem nítida do servidor, via clique no botão ✎
    previewEl?.addEventListener('click', async (ev) => {
      const card = ev.target?.closest?.('.page-wrapper');
      if (!card || !sessionId) return;

      const isPencil = ev.target?.closest?.('.crop-page');
      if (!isPencil) return;

      if (!openPageEditor) {
        alert('Editor indisponível neste build.');
        return;
      }

      let pageIndex = parseInt(card.dataset.page || '1', 10); // 1-based
      pageIndex = isFinite(pageIndex) && pageIndex > 0 ? pageIndex : 1;

      const W = parseFloat(card.dataset.pdfW || '0');
      const H = parseFloat(card.dataset.pdfH || '0');
      const pdfPageSize = (W && H) ? { width: W, height: H } : null;

      try{
        const baseScale = Math.min(2.5, (window.devicePixelRatio || 1.25) * 1.25);
        const imgBlob = await fetch(`/api/edit/page-image/${sessionId}/${pageIndex}?scale=${baseScale}`).then(r=>r.blob());

        await openPageEditor({
          bitmap: imgBlob,
          sessionId,
          pageIndex: pageIndex - 1,
          pdfPageSize,
          getBitmap: (needScale) =>
            fetch(`/api/edit/page-image/${sessionId}/${pageIndex}?scale=${Math.min(3.5, needScale).toFixed(2)}`).then(r=>r.blob())
        });

      } catch (err) {
        console.error(err);
        alert('Não foi possível abrir a página para edição.');
      }
    });
  }

  // ================= Aplicar =================
  async function applyChanges(){
    if (!sessionId) return;
    const mode = modeSelect?.value || 'all';

    spin(true);
    try {
      if (mode === 'all') {
        const payload = collectOrganizePayload(previewSel) || {};
        const org = {
          session_id: sessionId,
          order: payload.order || [],
          pages: payload.pages || [],
          delete: false,
          rotate: 0,
          rotations: payload.rotations || {}
        };

        const hasOrg =
          (org.order && org.order.length) ||
          (Object.keys(org.rotations || {}).length > 0);

        if (hasOrg) {
          const r = await fetch('/api/edit/apply/organize', {
            method:'POST',
            headers:{ 'Content-Type':'application/json', 'X-CSRFToken': getCSRFToken() },
            body: JSON.stringify(org)
          });
          const j = await r.json().catch(()=> ({}));
          if (!r.ok) throw new Error(j?.error || 'Falha no organize');
        }
      } else if (mode === 'ocr') {
        const r = await fetch('/api/edit/apply/ocr', {
          method:'POST',
          headers:{ 'Content-Type':'application/json', 'X-CSRFToken': getCSRFToken() },
          body: JSON.stringify({ session_id: sessionId })
        });
        const j = await r.json().catch(()=> ({})); if (j?.message) alert(j.message);
      }

      await renderPreview();
      // re-render a página atualmente selecionada (ou 1 se saiu do range)
      const totalCards = previewEl?.querySelectorAll('.page-wrapper')?.length || 0;
      if (!totalCards) {
        currentPage = 1;
      } else if (currentPage > totalCards) {
        currentPage = totalCards;
      }
      await renderStageForPage(currentPage);
      showDownload(`/api/edit/download/${sessionId}`);

    } catch(e){
      console.error(e);
      alert('Erro ao aplicar mudanças: ' + e.message);
    } finally {
      spin(false);
    }
  }

  // Bind inicial
  bindDropzone();
  bindModes();
  bindStageSelection();
  bindResizeRerender();
  btnApply?.addEventListener('click', applyChanges);
})();