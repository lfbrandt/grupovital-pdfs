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

  // editor opcional: export default OU named openPageEditor
  const openPageEditor = (PageEditorMod && (PageEditorMod.openPageEditor ?? PageEditorMod.default)) || null;

  let sessionId = null;

  /* =================== Helpers de rotação da thumb =================== */
  function normalizeThumbMedia(card) {
    // container que envolve a mídia (thumb-frame se existir)
    const frame = card.querySelector('.thumb-frame') || card;

    // mídia real (canvas/img/video)
    const media =
      frame.querySelector?.('canvas, img, video') ||
      card.querySelector?.('.thumb-media, canvas, img, video');

    if (frame) {
      // deixa o frame ocupar o card sem deslocar
      frame.style.position = 'absolute';
      frame.style.inset = '0';
      frame.style.transformOrigin = '50% 50%';
      frame.style.willChange = 'transform';
      frame.style.pointerEvents = 'none';
      frame.style.overflow = 'hidden';
    }
    if (media) {
      media.style.display = 'block';
      media.style.width = '100%';
      media.style.height = '100%';
      media.style.maxWidth = '100%';
      media.style.maxHeight = '100%';
      media.style.objectFit = 'contain';
      media.style.background = '#fff';
      media.style.transformOrigin = '50% 50%';
      media.style.willChange = 'transform';
      media.style.pointerEvents = 'none';
    }
    return { frame, media };
  }

  // aplica a rotação na MÍDIA (canvas/img), não no card
  function adjustThumbRotation(card) {
    const { media } = normalizeThumbMedia(card);
    const deg = (parseInt(card.dataset.rotation || '0', 10) || 0) % 360;
    if (media) media.style.transform = `rotate(${deg}deg)`;
  }

  function adjustAllThumbs() {
    document.querySelectorAll('#preview-edit .page-wrapper.page-thumb')
      .forEach(adjustThumbRotation);
  }

  // --- CONTROLE DO MODO PREVIEW NO <main> ---
  const mainEl = document.querySelector('main');
  const setPreviewMode = (on) => {
    if (!mainEl) return;
    mainEl.classList.toggle('with-preview', !!on);
    mainEl.classList.toggle('has-preview', !!on);
  };
  // Quando o preview terminar de montar, garante o modo ligado.
  document.addEventListener('preview:ready', () => { setPreviewMode(true); ensureEditThumbControls(); adjustAllThumbs(); });
  // Quando o preview for esvaziado, desliga.
  document.addEventListener('gv:preview:empty', () => setPreviewMode(false));
  // reajusta em resize (mantém transform correto)
  window.addEventListener('resize', () => { adjustAllThumbs(); }, { passive: true });

  // --- SINCRONIZA OFFSET DO HEADER PARA O MODAL ---
  function syncHeaderOffsetVar() {
    try {
      const h = document.querySelector('.gv-header');
      const px = h ? Math.max(48, Math.min(120, h.offsetHeight || 64)) : 64;
      document.documentElement.style.setProperty('--gv-header-h', px + 'px');
    } catch (_) {}
  }
  syncHeaderOffsetVar();
  window.addEventListener('resize', syncHeaderOffsetVar);

  // ================= Helpers UI =================
  const setHelp = (t) => { if (help) help.textContent = t || ''; };
  const enableApply = (on) => { if (btnApply) btnApply.disabled = !on; };
  const showDownload = (url) => { if (btnDownload && url) { btnDownload.href = url; btnDownload.classList.remove('is-hidden'); } };
  const hideDownload = () => { btnDownload?.classList.add('is-hidden'); btnDownload?.removeAttribute('href'); };
  const pdfUrl = () => (sessionId ? `/api/edit/file/${sessionId}?t=${Date.now()}` : null);
  const spin = (on) => {
    if (!spinnerEl) return;
    spinnerEl.classList.toggle('hidden', !on);
    spinnerEl.setAttribute('aria-hidden', on ? 'false' : 'true');
  };

  // ======== Injeta botões nas miniaturas do /edit ========
  function ensureEditThumbControls() {
    const root = document.querySelector('#preview-edit');
    if (!root) return;

    const isEditGrid = root.classList.contains('preview') && root.classList.contains('preview--grid');

    root.querySelectorAll('.page-wrapper.page-thumb').forEach((card) => {
      // container dos botões
      let fc = card.querySelector('.file-controls');
      if (!fc) {
        fc = document.createElement('div');
        fc.className = 'file-controls';
        card.appendChild(fc);
      }

      const addBtn = (cls, action, title) => {
        if (fc.querySelector(`[data-action="${action}"]`)) return; // evita duplicar
        const b = document.createElement('button');
        b.type = 'button';
        b.className = cls;                 // ex.: remove-file | rotate-page | crop-page
        b.dataset.action = action;         // ex.: remove | rotate | open-editor
        b.title = title;
        b.setAttribute('aria-label', title);
        fc.appendChild(b);
      };

      // Remover e girar
      addBtn('remove-file', 'remove', 'Excluir página');
      addBtn('rotate-page', 'rotate', 'Girar 90°');

      // ✎ só no /edit
      if (isEditGrid) addBtn('crop-page', 'open-editor', 'Editar (✎)');

      // normaliza mídia e aplica rotação atual (se houver)
      adjustThumbRotation(card);
    });
  }

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
      alert('Envie um PDF.'); return;
    }
    const fd = new FormData();
    // >>> GARANTE NOME COM EXTENSÃO (compat backend) <<<
    fd.append('file', file, file.name || 'input.pdf');

    const csrf = getCSRFToken();
    if (csrf) fd.append('csrf_token', csrf); // Flask-WTF costuma validar no corpo

    spin(true);
    try{
      const resp = await fetch('/api/edit/upload', {
        method: 'POST',
        headers: {
          'X-CSRFToken': csrf || '',
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json, */*;q=0.1'
        },
        body: fd,
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'follow'
      });

      const data = await resp.json().catch(()=> ({}));
      if(!resp.ok) throw new Error(data?.error || `Falha no upload (HTTP ${resp.status})`);
      sessionId = data.session_id;

      setPreviewSessionId(sessionId, previewSel);
      await renderPreview();
      setPreviewMode(true);
      enableApply(true);
      setHelp('PDF carregado. Reordene, gire, exclua ou abra a página para tapar/texto (✎).');
      hideDownload();
    } catch(e){
      console.error(e); alert('Erro no upload: ' + e.message);
    } finally { spin(false); }
  }

  // ================= Preview =================
  async function renderPreview(){
    if (!sessionId) return;
    const url = pdfUrl(); if (!url) return;
    await previewPDF(url, previewSel, spinnerSel);
    ensureEditThumbControls();
    adjustAllThumbs();
  }

  // ================= Modos / Ações =================
  function bindModes(){
    const hints = {
      all: 'Use os controles nas miniaturas para arrastar, girar, excluir e abrir o ✎. Depois clique em Aplicar.',
      redact: 'Abra o ✎ na miniatura para desenhar caixas de ocultação e salve.',
      text: 'Abra o ✎ na miniatura para adicionar texto e salve.',
      ocr: 'Executa OCR no servidor.'
    };
    setHelp(hints[modeSelect?.value || 'all'] || '');
    modeSelect?.addEventListener('change', ()=> setHelp(hints[modeSelect.value] || ''));

    // Bloqueia double-click nas miniaturas (evita abrir editor por engano)
    previewEl?.addEventListener('dblclick', (e) => {
      if (e.target?.closest?.('.page-wrapper.page-thumb')) {
        e.preventDefault(); e.stopPropagation();
      }
    }, true);

    // Delegação de cliques: rotate, remove, abrir editor
    previewEl?.addEventListener('click', async (ev) => {
      const card = ev.target?.closest?.('.page-wrapper.page-thumb');
      if (!card) return;

      // --- ROTATE 90° (apenas na mídia) ---
      const rotateBtn = ev.target?.closest?.('.rotate-page, [data-action="rotate"]');
      if (rotateBtn) {
        ev.preventDefault();
        const cur  = parseInt(card.dataset.rotation || card.getAttribute('data-rotation') || '0', 10) || 0;
        const next = (cur + 90) % 360;

        // persiste rotação no card (o collectOrganizePayload usa esse atributo)
        card.dataset.rotation = String(next);

        // aplica rotação visual na mídia
        adjustThumbRotation(card);
        return;
      }

      // --- REMOVER ---
      const removeBtn = ev.target?.closest?.('.remove-file, [data-action="remove"]');
      if (removeBtn) {
        ev.preventDefault();
        card.remove();
        const grid = document.querySelector('#preview-edit');
        if (grid && !grid.querySelector('.page-wrapper.page-thumb')) {
          try { document.dispatchEvent(new CustomEvent('gv:preview:empty')); } catch {}
        }
        return;
      }

      // --- ABRIR EDITOR (✎) ---
      const pencilBtn = ev.target?.closest?.('.open-editor, [data-action="open-editor"], [data-action="crop"], .crop-page');
      if (!pencilBtn) return;

      if (!openPageEditor || !sessionId) { alert('Editor indisponível neste build.'); return; }

      // tenta várias origens: data-page (1-based), data-src-page (0-based), fallback posição visual
      let pageIndex = NaN;
      if (card.dataset.page) pageIndex = parseInt(card.dataset.page, 10);
      if (!Number.isFinite(pageIndex) || pageIndex <= 0) {
        const raw0 = card.dataset.srcPage ?? card.getAttribute('data-src-page');
        const n0 = parseInt(raw0, 10);
        if (Number.isFinite(n0) && n0 >= 0) pageIndex = n0 + 1;
      }
      if (!Number.isFinite(pageIndex) || pageIndex <= 0) {
        const all = Array.from(document.querySelectorAll('#preview-edit .page-wrapper.page-thumb'));
        const idx = all.indexOf(card);
        pageIndex = idx >= 0 ? idx + 1 : 1;
      }

      const W = parseFloat(card.dataset.pdfW || '0');
      const H = parseFloat(card.dataset.pdfH || '0');
      const pdfPageSize = (W && H) ? { width: W, height: H } : null;

      // rotação base do PDF (se existir) — melhora o mapeamento no editor
      const viewRotation =
        parseInt(card.getAttribute('data-base-rotation') || card.dataset.baseRotation || '0', 10) || 0;

      try{
        const baseScale = Math.min(2.5, (window.devicePixelRatio || 1.25) * 1.25);
        const imgBlob = await fetch(`/api/edit/page-image/${sessionId}/${pageIndex}?scale=${baseScale}`, {
          credentials: 'same-origin',
          cache: 'no-store'
        }).then(r=>r.blob());

        openPageEditor({
          bitmap: imgBlob,
          sessionId,
          pageIndex: pageIndex - 1,
          pdfPageSize,
          viewRotation, // <<< passa a rotação base
          getBitmap: (needScale) =>
            fetch(`/api/edit/page-image/${sessionId}/${pageIndex}?scale=${Math.min(3.5, needScale).toFixed(2)}`, {
              credentials: 'same-origin',
              cache: 'no-store'
            }).then(r=>r.blob())
        });

        // força overlay/modal no topo e zera quaisquer scrolls
        const ensureTop = () => {
          const overlay = document.querySelector('.pe-overlay.modal-overlay, .pe-root, .pe-overlay-backdrop, .pemodal');
          const modal   = overlay && overlay.querySelector('.pe-modal.modal, .pe-dialog, .pe-modal');
          const bodyEl  = modal && modal.querySelector('.pe-body.modal-body');
          try { overlay && (overlay.scrollTop = 0); } catch(_) {}
          try { modal   && (modal.scrollTop   = 0); } catch(_) {}
          try { bodyEl  && (bodyEl.scrollTop  = 0); } catch(_) {}
        };
        requestAnimationFrame(() => { ensureTop(); requestAnimationFrame(ensureTop); });

      } catch (err) {
        console.error(err); alert('Não foi possível abrir a página para edição.');
      }
    });
  }

  // ================= Aplicar =================
  async function applyChanges(){
    if (!sessionId) return;
    const mode = modeSelect?.value || 'all';
    const csrf = getCSRFToken();

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
          rotations: payload.rotations || {},
          ...(csrf ? { csrf_token: csrf } : {})
        };

        const hasOrg = (org.order && org.order.length) || (Object.keys(org.rotations || {}).length > 0);
        if (hasOrg) {
          const r = await fetch('/api/edit/apply/organize', {
            method:'POST',
            headers:{
              'Content-Type':'application/json',
              'X-CSRFToken': csrf || '',
              'X-Requested-With': 'XMLHttpRequest',
              'Accept': 'application/json, */*;q=0.1'
            },
            body: JSON.stringify(org),
            credentials: 'same-origin',
            cache: 'no-store',
            redirect: 'follow'
          });
          const j = await r.json().catch(()=> ({}));
          if (!r.ok) throw new Error(j?.error || 'Falha no organize');
        }
      } else if (mode === 'ocr') {
        const r = await fetch('/api/edit/apply/ocr', {
          method:'POST',
          headers:{
            'Content-Type':'application/json',
            'X-CSRFToken': csrf || '',
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, */*;q=0.1'
          },
          body: JSON.stringify({ session_id: sessionId, ...(csrf ? { csrf_token: csrf } : {}) }),
          credentials: 'same-origin',
          cache: 'no-store',
          redirect: 'follow'
        });
        const j = await r.json().catch(()=> ({}));
        if (!r.ok) throw new Error(j?.error || `OCR falhou (HTTP ${r.status})`);
        if (j?.message) alert(j.message);
      }

      await renderPreview();
      setPreviewMode(true);
      showDownload(`/api/edit/download/${sessionId}`);

    } catch(e){
      console.error(e); alert('Erro ao aplicar mudanças: ' + e.message);
    } finally { spin(false); }
  }

  // Bind inicial
  (function init(){
    bindDropzone();
    bindModes();
    btnApply?.addEventListener('click', applyChanges);
    enableApply(false);
    hideDownload();
  })();
})();