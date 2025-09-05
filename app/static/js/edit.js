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

  // editor opcional: aceita export default OU named openPageEditor
  const openPageEditor = (PageEditorMod && (PageEditorMod.openPageEditor ?? PageEditorMod.default)) || null;

  let sessionId = null;

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
      await renderPreview();
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

  // ================= Preview =================
  async function renderPreview(){
    if (!sessionId) return;
    const url = pdfUrl();
    if (!url) return;
    await previewPDF(url, previewSel, spinnerSel);
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
  btnApply?.addEventListener('click', applyChanges);
})();