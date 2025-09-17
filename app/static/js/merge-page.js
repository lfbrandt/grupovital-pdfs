/* ============================================================================
   /merge — thumbs com rotate + fit sem corte + DnD + envio de `plan`
   - rotation ABSOLUTO = baseRotation(/Rotate do PDF) + rotação escolhida na UI
   - Lemos baseRotation de TODAS as páginas, mesmo sem renderizar thumbs
   - auto_orient sempre false quando usamos plan
   - FIX: ordem dos arquivos enviada ao backend segue a ordem VISUAL (grid)
          e o `plan.src` é REMAPEADO para os novos índices.
   - Escopo estrito (#preview-merge): nenhum handler “vaza” para outras rotas.
   - CSP ok: sem inline; usa X-CSRFToken real via utils quando disponível.
   - >>> Atualização: usa utils.getThumbWidth() e utils.fitRotateMedia()
   ============================================================================ */
(function () {
  'use strict';

  const els = {
    preview:  document.getElementById('preview-merge'),
    dz:       document.getElementById('dropzone-merge'),
    input:    document.getElementById('input-merge'),
    btnGo:    document.getElementById('btn-merge'),
    btnClear: document.getElementById('btn-clear-all'),
    spinner:  document.getElementById('spinner-merge'),
  };
  if (!els.preview || !els.dz || !els.input || !els.btnGo || !els.btnClear) return;

  /* ---- pdf.js guard ----------------------------------------------------- */
  const pdfjsLib = window.pdfjsLib;
  if (!pdfjsLib || !pdfjsLib.getDocument) {
    console.error('[merge] pdf.js não carregado (pdf.min.js + worker).');
    return;
  }

  /* ---- Utils centralizados ---------------------------------------------- */
  const U = (window.utils || {});
  const fitRotateMedia = U.fitRotateMedia || function noop(){};
  const getThumbWidth  = U.getThumbWidth  || function(){ return 180; };
  const rotationNormalize = (a)=>{ a = Number(a)||0; a%=360; if(a<0)a+=360; return a; };

  /* ---- Qualidade / limites --------------------------------------------- */
  const THUMB_QUALITY = 2.0;
  const DPR = Math.max(1, Math.min((window.devicePixelRatio || 1) * THUMB_QUALITY, 3));
  const MAX_THUMB_PIXELS = 2_800_000;
  const MAX_THUMB_SIDE   = 1600;

  function getCSRFToken() {
    try {
      if (typeof window.getCSRFToken === 'function') return window.getCSRFToken();
      const m = document.querySelector('meta[name="csrf-token"]');
      return m ? m.content : '';
    } catch { return ''; }
  }

  const state = {
    // { letter, name, file, pdfDoc, totalPages, srcIndex }
    sources: [],
    // { id, srcIndex, source, page, rotation(user), baseRotation, crop?, el }
    items:   [],
    rotate:  {},          // id -> user angle
    selection: new Set(), // ids selecionados (shift/ctrl)
    lastIndex: null,
    baseRotPromises: [],  // aguardamos todas antes de enviar o plan
    observers: []         // IntersectionObservers para limpar depois
  };

  // Apenas para ordenar visualmente por letra quando adiciona novas fontes
  const SOURCE_ORDER = ['A','C','B','D','E','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T','U','V','W','X','Y','Z'];
  const uuid = ()=>'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c=>{
    const r=Math.random()*16|0,v=c==='x'?r:(r&0x3|0x8);return v.toString(16);
  });
  const letterFor = (i)=> 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'[i] || ('Z'+(i - 25));

  function enableActions(){
    els.btnGo.disabled = state.items.length === 0 || state.sources.length < 2;
    els.btnClear.disabled = state.items.length === 0 && state.sources.length === 0;
  }

  /* ---- PDF → imagem (thumb) -------------------------------------------- */
  async function openPdfFromFile(file){
    const buf = await file.arrayBuffer();
    return await pdfjsLib.getDocument(new Uint8Array(buf)).promise;
  }

  async function renderPageToImg(pdfDoc, pageNum, targetImg, maxW){
    try{
      const page = await pdfDoc.getPage(pageNum);
      const baseRotation = (Number(page.rotate) || 0) % 360;

      const base = page.getViewport({ scale: 1 });
      let scale = (maxW * DPR) / base.width;

      // clamps
      let tw = base.width * scale;
      let th = base.height * scale;
      const side = Math.max(tw, th);
      const pix  = tw * th;
      if (side > MAX_THUMB_SIDE || pix > MAX_THUMB_PIXELS) {
        const kSide = MAX_THUMB_SIDE / side;
        const kPix  = Math.sqrt(MAX_THUMB_PIXELS / pix);
        scale *= Math.min(kSide, kPix);
      }

      const vp = page.getViewport({ scale, rotation: 0 });
      const c = document.createElement('canvas');
      c.width  = Math.round(vp.width);
      c.height = Math.round(vp.height);
      const ctx = c.getContext('2d', { alpha:false, desynchronized: true });
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      await page.render({ canvasContext: ctx, viewport: vp, intent: 'display' }).promise;

      let dataUrl;
      try { dataUrl = c.toDataURL('image/webp', 0.92); }
      catch { dataUrl = c.toDataURL('image/png', 0.9); }

      targetImg.src = dataUrl;
      targetImg.dataset.bmpW = String(c.width);
      targetImg.dataset.bmpH = String(c.height);
      targetImg.dataset.baseRotation = String(baseRotation);

      targetImg.addEventListener('load', ()=>{
        const card = targetImg.closest('.page-wrapper');
        if (!card) return;
        const frame = card.querySelector('.thumb-frame');
        const media = card.querySelector('.thumb-media');
        const ang   = Number(media?.dataset?.deg || 0);
        fitRotateMedia({ frameEl: frame, mediaEl: media, angle: ang });
      }, { once:true });

      return baseRotation;
    } catch {
      targetImg.alt = 'Prévia indisponível';
      return 0;
    }
  }

  const debounced = (fn, ms=120)=>{ let t=null; return (...a)=>{ clearTimeout(t); t=setTimeout(()=>fn(...a), ms); }; };
  const onResize = debounced(()=>{
    // Re-fit só dentro do #preview-merge
    els.preview.querySelectorAll('.page-wrapper').forEach(card => {
      const frame = card.querySelector('.thumb-frame');
      const media = card.querySelector('.thumb-media');
      const ang   = Number(media?.dataset?.deg || 0);
      fitRotateMedia({ frameEl: frame, mediaEl: media, angle: ang });
    });
  }, 120);
  window.addEventListener('resize', onResize, { passive:true });

  /* ---- Dropzone / input ------------------------------------------------- */
  function wireDropzone(){
    els.dz.addEventListener('click', (e)=>{
      if (e.target.tagName !== 'INPUT') els.input.click();
    });
    ['dragenter','dragover'].forEach(ev=>{
      els.dz.addEventListener(ev, (e)=>{
        e.preventDefault();
        e.dataTransfer.dropEffect = 'copy';
        els.dz.classList.add('is-hover');
      });
    });
    ['dragleave','drop'].forEach(ev=>{
      els.dz.addEventListener(ev, ()=> els.dz.classList.remove('is-hover'));
    });
    els.dz.addEventListener('drop', (e)=>{
      e.preventDefault();
      const files = Array.from(e.dataTransfer.files||[]).filter(isPdf);
      if (!files.length) { console.warn('Solte apenas arquivos PDF.'); return; }
      handleFiles(files);
    });
    els.input.addEventListener('change', ()=>{
      const files = Array.from(els.input.files||[]).filter(isPdf);
      if (!files.length) return;
      handleFiles(files);
    });
  }
  function isPdf(f){ const mt = (f.type||'').toLowerCase(); return mt === 'application/pdf' || /\.pdf$/i.test(f.name||''); }

  /* ---- Pipeline de arquivos -------------------------------------------- */
  async function handleFiles(files){
    const start = state.sources.length;
    for (let idx = 0; idx < files.length; idx++) {
      const file = files[idx];
      const letter = letterFor(start + idx);
      const srcIndex = start + idx;

      const src = { letter, file, name:file.name, pdfDoc:null, totalPages:0, srcIndex };
      state.sources.push(src);

      try{
        src.pdfDoc = await openPdfFromFile(file);
        src.totalPages = src.pdfDoc.numPages || 1;
        await buildThumbsForSource(src);
        applySourceOrder();
        enableActions();
      }catch{ console.error(`[merge] Falha ao ler ${file.name}`); }
    }
  }

  async function buildThumbsForSource(src){
    const frag = document.createDocumentFragment();
    const maxW = getThumbWidth();

    for (let p=1; p<=src.totalPages; p++){
      const item = {
        id: uuid(),
        srcIndex: src.srcIndex,
        source: src.letter,
        page: p,
        rotation: 0,         // rotação que o usuário pede
        baseRotation: 0,     // /Rotate interno do PDF
        crop: null,
        el: null
      };

      // ler baseRotation SEM depender do render da thumb
      const prom = src.pdfDoc.getPage(p).then(pg=>{
        const rot = (Number(pg.rotate)||0)%360;
        item.baseRotation = rot;
      }).catch(()=>{ item.baseRotation = 0; });
      state.baseRotPromises.push(prom);

      const card = document.createElement('div');
      card.className = 'page-wrapper page-thumb';
      card.dataset.itemId = item.id;
      card.dataset.source = src.letter;
      card.dataset.srcIndex = String(src.srcIndex);
      card.dataset.page   = String(p);
      card.dataset.rotation = '0';
      card.tabIndex = 0;
      card.setAttribute('role','option');
      card.setAttribute('draggable','true');

      // Controles
      const controls = document.createElement('div');
      controls.className = 'file-controls';
      controls.setAttribute('data-no-drag','');

      const btnRemove = document.createElement('button');
      btnRemove.type='button'; btnRemove.className='remove-file';
      btnRemove.title='Remover página'; btnRemove.setAttribute('aria-label','Remover');

      const btnRotate = document.createElement('button');
      btnRotate.type='button'; btnRotate.className='rotate-page';
      btnRotate.title='Girar 90°'; btnRotate.setAttribute('aria-label','Girar 90°');

      btnRemove.addEventListener('click', (e)=>{ e.stopPropagation(); removeItem(item); });
      btnRotate.addEventListener('click', (e)=>{
        e.stopPropagation();
        item.rotation = rotationNormalize((item.rotation||0) + 90);
        state.rotate[item.id] = item.rotation;
        card.dataset.rotation = String(item.rotation);
        const media = card.querySelector('.thumb-media');
        const frame = card.querySelector('.thumb-frame');
        if (media && frame) {
          media.dataset.deg = String(item.rotation);
          fitRotateMedia({ frameEl: frame, mediaEl: media, angle: item.rotation });
        }
      });

      controls.append(btnRemove, btnRotate);

      const pageBadge = document.createElement('div');
      pageBadge.className = 'page-badge';
      pageBadge.textContent = `Pg ${p}`;

      const srcBadge  = document.createElement('div');
      srcBadge.className  = 'source-badge';
      srcBadge.textContent = src.letter;
      srcBadge.title = src.name;

      const frame = document.createElement('div');
      frame.className = 'thumb-frame';

      const img   = document.createElement('img');
      img.className = 'thumb-media';
      img.alt = `Página ${p}`;
      img.decoding = 'async';
      img.loading = 'lazy';
      img.setAttribute('draggable','false');

      frame.appendChild(img);

      card.append(controls, pageBadge, srcBadge, frame);
      item.el = card;
      frag.appendChild(card);
      state.items.push(item);

      queueLazyRender(src, p, img, maxW, item);
      wireDnD(card);
      wireSelection(card);
    }
    els.preview.appendChild(frag);
  }

  function queueLazyRender(src, page, img, maxW, itemRef){
    const io = new IntersectionObserver(async (entries, observer)=>{
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        try {
          const base = await renderPageToImg(src.pdfDoc, page, img, maxW);
          itemRef.baseRotation = base; // redundante, mas mantém UI fiel
        } catch{}
      }
    }, { root: null, rootMargin: '600px 0px', threshold: 0.01 });
    io.observe(img);
    state.observers.push(io);
  }

  /* ---- DnD / seleção / limpeza --------------------------------- */
  function wireDnD(card){
    card.addEventListener('dragstart', (e)=>{
      e.dataTransfer.effectAllowed='move';
      e.dataTransfer.setData('text/plain', card.dataset.itemId);
      card.classList.add('is-dragging');
    });
    card.addEventListener('dragend', ()=>{
      card.classList.remove('is-dragging');
      els.preview.querySelectorAll('.is-drop-target').forEach(n=>n.classList.remove('is-drop-target'));
      syncStateFromDOM();
      // refit
      const frame = card.querySelector('.thumb-frame');
      const media = card.querySelector('.thumb-media');
      const ang   = Number(media?.dataset?.deg || 0);
      fitRotateMedia({ frameEl: frame, mediaEl: media, angle: ang });
    });
  }
  els.preview.addEventListener('dragover', (e)=>{
    const dragging = els.preview.querySelector('.page-wrapper.is-dragging');
    if (!dragging) return;
    e.preventDefault();
    const target = e.target.closest('.page-wrapper');
    if (!target || target === dragging) return;
    const rect = target.getBoundingClientRect();
    const before = e.clientX < rect.left + rect.width / 2;
    target.classList.add('is-drop-target');
    if (before) els.preview.insertBefore(dragging, target);
    else els.preview.insertBefore(dragging, target.nextSibling);
  });
  els.preview.addEventListener('dragleave', (e)=>{
    const t = e.target.closest?.('.page-wrapper');
    if (t) t.classList.remove('is-drop-target');
  });
  els.preview.addEventListener('drop', ()=>{
    els.preview.querySelectorAll('.is-drop-target').forEach(n=>n.classList.remove('is-drop-target'));
    syncStateFromDOM();
  });

  function syncStateFromDOM(){
    const map = new Map(state.items.map(i=>[i.id, i]));
    const order = Array.from(els.preview.querySelectorAll('.page-wrapper'))
      .map(w => map.get(w.dataset.itemId)).filter(Boolean);
    state.items = order;
    enableActions();
  }

  const stateSel = { selection: new Set(), lastIndex: null };
  function wireSelection(card){
    card.addEventListener('click', (e)=>{
      if (e.target.closest('[data-no-drag]') || e.target.closest('button')) return;
      const items = Array.from(els.preview.querySelectorAll('.page-wrapper'));
      const idx = items.indexOf(card);
      const id = card.dataset.itemId;

      if (e.ctrlKey || e.metaKey) {
        if (stateSel.selection.has(id)) stateSel.selection.delete(id);
        else stateSel.selection.add(id);
        stateSel.lastIndex = idx;
      } else if (e.shiftKey && stateSel.lastIndex != null) {
        stateSel.selection.clear();
        const [a,b] = [stateSel.lastIndex, idx].sort((x,y)=>x-y);
        items.slice(a, b+1).forEach(el => stateSel.selection.add(el.dataset.itemId));
      } else {
        stateSel.selection.clear();
        stateSel.selection.add(id);
        stateSel.lastIndex = idx;
      }
      applySelectionClasses();
    });
  }
  function applySelectionClasses() {
    els.preview.querySelectorAll('.page-wrapper').forEach(el => {
      const on = stateSel.selection.has(el.dataset.itemId);
      el.classList.toggle('selected', !!on);
      el.setAttribute('aria-selected', on ? 'true' : 'false');
    });
  }

  // Escopo: só reage a Delete/Backspace quando o foco/clique está dentro do #preview-merge
  document.addEventListener('keydown', (e)=>{
    if (!stateSel.selection.size) return;
    const within =
      els.preview.contains(document.activeElement) ||
      (e.target && els.preview.contains(e.target));
    if (!within) return;

    if ((e.key === 'Delete' || e.key === 'Backspace')) {
      e.preventDefault();
      const ids = new Set(stateSel.selection);
      stateSel.selection.clear();
      state.items.filter(i => ids.has(i.id)).forEach(removeItem);
      applySelectionClasses();
    }
  }, true);

  function removeItem(item){
    item.el?.remove();
    state.items = state.items.filter(i=>i.id !== item.id);
    delete state.rotate[item.id];
    enableActions();
  }

  function clearAll(){
    // observers
    state.observers.forEach(io => { try { io.disconnect(); } catch{} });
    state.observers = [];

    // pdf docs
    state.sources.forEach(s=>{ try { s.pdfDoc?.destroy(); } catch{} });

    state.sources = [];
    state.items = [];
    state.rotate = {};
    stateSel.selection.clear();
    stateSel.lastIndex = null;
    state.baseRotPromises = [];

    els.preview.innerHTML = '';
    els.input.value = '';
    enableActions();
  }

  function applySourceOrder() {
    const nodes = Array.from(els.preview.querySelectorAll('.page-wrapper'));
    nodes.sort((a, b) => {
      const ia = SOURCE_ORDER.indexOf(a.dataset.source || '');
      const ib = SOURCE_ORDER.indexOf(b.dataset.source || '');
      return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
    });
    nodes.forEach(n => els.preview.appendChild(n));
  }

  /* ---- Monta plan (remapeando src) ------------------------------------- */
  function buildPlanFromState(remapSrcIndex) {
    return state.items.map(it => {
      const base = rotationNormalize(it.baseRotation || 0);
      const user = rotationNormalize(it.rotation || 0);
      const finalRotation = rotationNormalize(base + user);
      const oldSrc = it.srcIndex;
      const src = remapSrcIndex && remapSrcIndex.has(oldSrc) ? remapSrcIndex.get(oldSrc) : oldSrc;
      return {
        src, page: it.page, rotation: finalRotation,
        ...(it.crop ? { crop: it.crop } : {}),
      };
    });
  }

  /* ---- Submit ----------------------------------------------------------- */
  async function submitMerge(){
    if (!state.items.length || state.sources.length < 2) {
      console.warn('[merge] Selecione pelo menos 2 PDFs e páginas.');
      return;
    }

    // aguardar leitura de baseRotation de TODAS as páginas
    try { await Promise.all(state.baseRotPromises); } catch {}

    const fd = new FormData();

    // (1) Colete srcIndex em ordem VISUAL e monte remap
    const orderedSrcIndexes = [];
    const seen = new Set();
    for (const it of state.items) {
      if (!seen.has(it.srcIndex)) { seen.add(it.srcIndex); orderedSrcIndexes.push(it.srcIndex); }
    }
    const remap = new Map();
    orderedSrcIndexes.forEach((oldIdx, newIdx) => remap.set(oldIdx, newIdx));

    // (2) Anexar arquivos seguindo a ordem VISUAL
    orderedSrcIndexes.forEach(oldIdx => {
      const src = state.sources.find(s => s.srcIndex === oldIdx);
      if (src) fd.append('files', src.file, src.name);
    });

    // (3) Plano linear com src já REMAPEADO
    const plan = buildPlanFromState(remap);
    fd.append('plan', JSON.stringify(plan));

    // (4) Flags — auto_orient off quando plan existe
    fd.append('auto_orient', 'false');
    fd.append('flatten', 'true');
    fd.append('pdf_settings', '/ebook');

    try{
      els.btnGo.disabled = true;
      els.spinner?.classList.remove('hidden');

      const resp = await fetch('/api/merge', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCSRFToken(), 'Accept': 'application/pdf' },
        body: fd,
        credentials: 'same-origin'
      });
      if (!resp.ok) {
        let msg = 'Falha ao juntar PDFs.';
        try { msg = await resp.text(); } catch {}
        throw new Error(msg || 'Erro ao juntar PDFs.');
      }

      const blob = await resp.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'merged.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
    }catch(e){
      console.error(e?.message || 'Erro ao juntar PDFs.');
    } finally {
      els.spinner?.classList.add('hidden');
      enableActions();
    }
  }

  /* ---- Bootstrap -------------------------------------------------------- */
  function bindUI(){
    wireDropzone();
    els.btnGo.addEventListener('click', submitMerge);
    els.btnClear.addEventListener('click', clearAll);
    enableActions();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bindUI, { once:true });
  else bindUI();

  // limpeza defensiva ao descarregar a página
  window.addEventListener('beforeunload', ()=> {
    try { state.observers.forEach(io => io.disconnect()); } catch {}
    try { state.sources.forEach(s=> s.pdfDoc?.destroy()); } catch {}
  }, { once:true });
})();