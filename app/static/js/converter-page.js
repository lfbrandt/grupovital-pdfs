// app/static/js/converter-page.js
// Bloqueio de metas na /convert/select  +  DnD opcional do preview  +  Fluxo da tela /converter
// Regras: sem inline, 1 binding por listener, envia X-CSRFToken, respeita CSP.

'use strict';

(function () {
  // =========================
  // 1) /convert/select: bloquear metas
  // =========================
  const DISABLED_GOALS = new Set(['pdf-to-xlsx', 'pdf-to-csv', 'sheet-to-csv', 'sheet-to-xlsm']);

  function getGoal(el) {
    const dg = el.getAttribute && el.getAttribute('data-goal');
    if (dg) return dg.trim();
    const a = el.matches && el.matches('a[href]') ? el : el.querySelector && el.querySelector('a[href]');
    if (!a) return '';
    try {
      const url = new URL(a.getAttribute('href'), window.location.origin);
      return (url.searchParams.get('goal') || '').trim();
    } catch {
      return '';
    }
  }

  function disableCard(card) {
    if (!card) return;
    if (card.__disabled) return;
    card.__disabled = true;

    card.classList.add('is-disabled');
    card.setAttribute('aria-disabled', 'true');

    const a = card.matches && card.matches('a[href]') ? card : card.querySelector && card.querySelector('a[href]');
    if (a) {
      const href = a.getAttribute('href');
      if (href) {
        a.setAttribute('data-href', href);
        a.removeAttribute('href');
      }
      a.setAttribute('aria-disabled', 'true');
      a.setAttribute('tabindex', '-1');
      const prevent = (e) => e.preventDefault();
      a.addEventListener('click', prevent, { once: false });
      a.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') e.preventDefault();
      }, { once: false });
    }
  }

  function initGoalGridBlock() {
    const grid = document.querySelector('.goal-grid');
    if (!grid) return;
    grid.querySelectorAll('.goal-card, a.goal-card').forEach((card) => {
      const g = getGoal(card);
      if (g && DISABLED_GOALS.has(g)) disableCard(card);
    });
  }

  // =========================
  // 2) /converter: utilidades comuns
  // =========================
  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

  function csrfToken() {
    try {
      // projeto já expõe getCSRFToken()
      if (typeof getCSRFToken === 'function') return getCSRFToken();
    } catch {}
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? (meta.getAttribute('content') || '') : '';
  }

  function bytesToKB(size) {
    if (!size && size !== 0) return '';
    const kb = Math.max(1, Math.round(size / 1024));
    return `${kb} KB`;
  }

  function setStatus(el, msg, type = 'info') {
    if (!el) return;
    el.textContent = msg || '';
    el.className = `status ${type}`;
  }

  function toggleSpinner(spSel, on) {
    const sp = $(spSel);
    if (!sp) return;
    sp.classList.toggle('hidden', !on);
    sp.setAttribute('aria-hidden', on ? 'false' : 'true');
  }

  // =========================
  // 3) /converter: estado & render da grid de arquivos (ordem arrastável)
  // =========================
  // Mantemos a ordem em fileInput (FileList) usando DataTransfer a cada alteração.
  function renderFileGrid({ fileInput, gridEl, badgeEl }) {
    if (!fileInput || !gridEl) return;
    gridEl.innerHTML = '';

    const files = fileInput.files ? Array.from(fileInput.files) : [];
    const count = files.length;

    if (badgeEl) {
      const label = count === 1 ? '1 arquivo' : `${count} arquivos`;
      badgeEl.textContent = label;
    }

    if (!count) {
      const sideGrid = $('#side-grid');
      if (sideGrid) sideGrid.hidden = true;
      return;
    }

    const sideGrid = $('#side-grid');
    if (sideGrid) sideGrid.hidden = false;

    files.forEach((f, idx) => {
      const card = document.createElement('div');
      card.className = 'file-card';
      card.setAttribute('draggable', 'true');
      card.dataset.index = String(idx);

      // conteúdo simples (evita dependências externas)
      const name = document.createElement('div');
      name.className = 'file-card__name';
      name.textContent = f.name;

      const meta = document.createElement('div');
      meta.className = 'file-card__meta';
      meta.textContent = bytesToKB(f.size);

      card.appendChild(name);
      card.appendChild(meta);
      gridEl.appendChild(card);
    });

    bindFileGridDnD(gridEl, fileInput, badgeEl);
  }

  function updateInputOrderFromGrid(gridEl, fileInput) {
    // Reconstroi o FileList na ordem dos elementos (.file-card) da grid
    const current = fileInput.files ? Array.from(fileInput.files) : [];
    const byName = new Map(current.map((f) => [f.name + '::' + f.size + '::' + f.type, f]));
    const order = [];
    $$('.file-card', gridEl).forEach((el) => {
      const idx = Number(el.dataset.index || '0');
      const f = current[idx];
      if (f) {
        order.push(f);
      } else {
        // fallback por chave de nome/tamanho/tipo se índice mudar
        const key = el.dataset.key;
        if (key && byName.has(key)) order.push(byName.get(key));
      }
    });

    const dt = new DataTransfer();
    order.forEach((f) => dt.items.add(f));
    fileInput.files = dt.files;

    // Re-marca índices/keys
    $$('.file-card', gridEl).forEach((el, i) => {
      el.dataset.index = String(i);
      const f = fileInput.files[i];
      if (f) el.dataset.key = `${f.name}::${f.size}::${f.type}`;
    });
  }

  function bindFileGridDnD(gridEl, fileInput, badgeEl) {
    if (!gridEl || gridEl.__dndBound) return;
    gridEl.__dndBound = true;

    let dragged = null;

    gridEl.addEventListener('dragstart', (e) => {
      const item = e.target && e.target.closest('.file-card');
      if (!item) return;
      dragged = item;
      try { e.dataTransfer.setData('text/plain', ''); } catch {}
      e.dataTransfer.effectAllowed = 'move';
      item.classList.add('is-dragging');
    });

    gridEl.addEventListener('dragend', () => {
      if (!dragged) return;
      dragged.classList.remove('is-dragging');
      // ao soltar, sincroniza input com a nova ordem
      updateInputOrderFromGrid(gridEl, fileInput);
      renderFileGrid({ fileInput, gridEl, badgeEl }); // re-render para normalizar índices
      dragged = null;
    });

    gridEl.addEventListener('dragover', (e) => {
      if (!dragged) return;
      e.preventDefault();
      const t = e.target && e.target.closest('.file-card');
      if (!t || t === dragged) return;
      const r = t.getBoundingClientRect();
      const before = (e.clientY - r.top) < r.height / 2;
      gridEl.insertBefore(dragged, before ? t : t.nextSibling);
    });

    gridEl.addEventListener('drop', (e) => e.preventDefault());
  }

  function appendFilesToInput(fileInput, newFiles) {
    const dt = new DataTransfer();
    const current = fileInput.files ? Array.from(fileInput.files) : [];
    [...current, ...newFiles].forEach((f) => dt.items.add(f));
    fileInput.files = dt.files;
  }

  // =========================
  // 4) /converter: ações (merge 1 PDF  |  multi-PDF)
  // =========================
  async function doMergeOnePDF({ fileInput, spinnerSel, statusEl }) {
    if (!(fileInput.files && fileInput.files.length)) return;
    setStatus(statusEl, 'Convertendo e unindo…', 'info');
    toggleSpinner(spinnerSel, true);

    const fd = new FormData();
    Array.from(fileInput.files).forEach((f) => fd.append('files[]', f));

    let res;
    try {
      res = await fetch('/api/convert/to-pdf-merge', {
        method: 'POST',
        body: fd,
        headers: { 'X-CSRFToken': csrfToken(), 'Accept': 'application/pdf' },
        credentials: 'same-origin',
        cache: 'no-store'
      });

      const ctype = (res.headers.get('Content-Type') || '').toLowerCase();
      if (!res.ok) {
        if (ctype.includes('application/json')) {
          const j = await res.json().catch(() => ({}));
          throw new Error(j.error || `Erro (${res.status})`);
        }
        throw new Error(`Erro (${res.status})`);
      }

      const blob = await res.blob();
      if (!blob || !blob.size) throw new Error('Resposta vazia.');

      // nome do arquivo
      let filename = 'arquivos_unidos.pdf';
      const disp = res.headers.get('Content-Disposition') || '';
      const m = /filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i.exec(disp);
      if (m) filename = decodeURIComponent(m[1] || m[2] || filename);

      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);

      setStatus(statusEl, 'Pronto! Baixando 1 PDF.', 'success');
    } catch (err) {
      console.error(err);
      setStatus(statusEl, err && err.message ? err.message : 'Falha ao unir.', 'error');
    } finally {
      toggleSpinner(spinnerSel, false);
    }
  }

  async function doManyPDFs({ fileInput, spinnerSel, statusEl, resultListEl }) {
    if (!(fileInput.files && fileInput.files.length)) return;
    setStatus(statusEl, 'Convertendo arquivos…', 'info');
    toggleSpinner(spinnerSel, true);
    if (resultListEl) resultListEl.innerHTML = '';

    const fd = new FormData();
    Array.from(fileInput.files).forEach((f) => fd.append('files[]', f));

    let res;
    try {
      res = await fetch('/api/convert/to-pdf', {
        method: 'POST',
        body: fd,
        headers: { 'X-CSRFToken': csrfToken(), 'Accept': 'application/json' },
        credentials: 'same-origin',
        cache: 'no-store'
      });

      const ctype = (res.headers.get('Content-Type') || '').toLowerCase();
      if (!res.ok) {
        if (ctype.includes('application/json')) {
          const j = await res.json().catch(() => ({}));
          throw new Error(j.error || `Erro (${res.status})`);
        }
        throw new Error(`Erro (${res.status})`);
      }

      const data = await res.json();
      const items = Array.isArray(data.files) ? data.files : [];
      if (!items.length) throw new Error('Nenhum arquivo retornado.');

      // lista de resultados
      if (resultListEl) {
        resultListEl.innerHTML = '';
        items.forEach((it) => {
          const li = document.createElement('li');
          li.className = 'result-item';

          const a = document.createElement('a');
          a.href = it.download_url;
          a.textContent = it.name || 'baixar';
          if (it.name) a.setAttribute('download', it.name);
          a.rel = 'noopener';

          const meta = document.createElement('small');
          meta.textContent = it.size ? ` — ${(it.size/1024).toFixed(1)} KB` : '';

          li.appendChild(a);
          li.appendChild(meta);
          resultListEl.appendChild(li);
        });
      }

      const total = data.count || items.length;
      setStatus(statusEl, `Pronto! ${total} arquivo(s) disponível(is) para download.`, 'success');
    } catch (err) {
      console.error(err);
      setStatus(statusEl, err && err.message ? err.message : 'Falha ao converter.', 'error');
    } finally {
      toggleSpinner(spinnerSel, false);
    }
  }

  // =========================
  // 5) /converter: binding de UI
  // =========================
  function initConverterPage() {
    // elementos esperados pelo template converter.html
    const fileInput  = $('#input-convert');
    const dropzone   = $('#dropzone-convert');
    const gridEl     = $('#file-grid-convert');
    const badgeEl    = $('#badge-count-convert');

    const btnMerge   = $('#btn-merge-all');
    const btnConvert = $('#btn-convert-all');
    const btnClear   = $('#btn-clear-all');

    const statusEl   = $('#status');
    const spinnerSel = '#spinner-convert';
    const resultList = $('#result-list');

    if (!fileInput || !dropzone) return; // não está na tela /converter

    const syncButtons = () => {
      const hasFiles = !!(fileInput.files && fileInput.files.length);
      if (btnMerge) btnMerge.disabled = !hasFiles;
      if (btnConvert) btnConvert.disabled = !hasFiles;
    };

    const renderAll = () => {
      renderFileGrid({ fileInput, gridEl, badgeEl });
      // miniatura simples (se houver alvo)
      const firstThumb = $('#thumb-convert');
      if (firstThumb) {
        // mostra apenas se o primeiro arquivo for PDF; imagens e docs são convertidos no backend
        const f = fileInput.files && fileInput.files[0];
        if (f && /pdf$/i.test(f.name)) {
          firstThumb.hidden = false; // uma thumbnail real exigiria pdf.js + render; fora do escopo aqui
        } else {
          firstThumb.hidden = true;
        }
      }
      syncButtons();
    };

    // Clique para escolher
    const dzInput = dropzone.querySelector('input[type=file]');
    if (dzInput && !dzInput.__boundChange) {
      dzInput.__boundChange = true;
      dzInput.addEventListener('change', () => {
        // o input já é o próprio fileInput
        renderAll();
        if (resultList) resultList.innerHTML = '';
      });
    }

    // DnD para adicionar
    const setActive = (on) => dropzone.classList.toggle('is-active', !!on);
    ['dragenter', 'dragover'].forEach((evt) => {
      dropzone.addEventListener(evt, (e) => { e.preventDefault(); setActive(true); }, { passive: false });
    });
    ['dragleave', 'drop'].forEach((evt) => {
      dropzone.addEventListener(evt, (e) => { e.preventDefault(); setActive(false); }, { passive: false });
    });
    dropzone.addEventListener('drop', (e) => {
      const files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      appendFilesToInput(fileInput, Array.from(files));
      renderAll();
      if (resultList) resultList.innerHTML = '';
    });

    // Clear
    if (btnClear && !btnClear.__bound) {
      btnClear.__bound = true;
      btnClear.addEventListener('click', () => {
        const dt = new DataTransfer();
        fileInput.files = dt.files;
        renderAll();
        if (resultList) resultList.innerHTML = '';
        setStatus(statusEl, '', 'info');
      });
    }

    // Merge 1 PDF
    if (btnMerge && !btnMerge.__bound) {
      btnMerge.__bound = true;
      btnMerge.addEventListener('click', async () => {
        btnMerge.disabled = true; btnConvert && (btnConvert.disabled = true);
        await doMergeOnePDF({ fileInput, spinnerSel, statusEl });
        syncButtons();
      });
    }

    // Muitos PDFs
    if (btnConvert && !btnConvert.__bound) {
      btnConvert.__bound = true;
      btnConvert.addEventListener('click', async () => {
        btnMerge && (btnMerge.disabled = true); btnConvert.disabled = true;
        await doManyPDFs({ fileInput, spinnerSel, statusEl, resultListEl: resultList });
        syncButtons();
      });
    }

    // primeira renderização
    renderAll();
  }

  // =========================
  // 6) DnD opcional do preview de páginas (#preview-convert)
  //    (para quem usa o mesmo JS também em outra tela com preview de páginas)
  // =========================
  const PREVIEW_LIST_SELECTOR = '#preview-convert';
  const PREVIEW_ITEM_SELECTOR = '.page-thumb';

  function ensurePageIds(list) {
    list.querySelectorAll(PREVIEW_ITEM_SELECTOR).forEach((el, i) => {
      if (!el.dataset.pageId) el.dataset.pageId = String(i + 1);
    });
  }
  function markPageGrabbable(list) {
    list.querySelectorAll(PREVIEW_ITEM_SELECTOR).forEach((el) => {
      if (!el.hasAttribute('draggable')) el.setAttribute('draggable', 'true');
      el.classList.add('is-grabbable');
    });
  }
  function savePageOrder(list) {
    const order = [...list.querySelectorAll(PREVIEW_ITEM_SELECTOR)].map(el => el.dataset.pageId || '');
    list.dataset.order = order.join(',');
    list.dispatchEvent(new CustomEvent('orderchange', { detail: { order } }));
  }
  function bindPreviewDnD(list) {
    if (!list || list.__dndBound) return;
    list.__dndBound = true;

    let dragged = null;
    list.addEventListener('dragstart', (e) => {
      const item = e.target && e.target.closest(PREVIEW_ITEM_SELECTOR);
      if (!item) return;
      dragged = item;
      try { e.dataTransfer.setData('text/plain', ''); } catch {}
      e.dataTransfer.effectAllowed = 'move';
      item.classList.add('is-dragging');
      item.setAttribute('aria-grabbed', 'true');
    });
    list.addEventListener('dragend', () => {
      if (!dragged) return;
      dragged.classList.remove('is-dragging');
      dragged.removeAttribute('aria-grabbed');
      savePageOrder(list);
      dragged = null;
    });
    list.addEventListener('dragover', (e) => {
      if (!dragged) return; e.preventDefault();
      const t = e.target && e.target.closest(PREVIEW_ITEM_SELECTOR);
      if (!t || t === dragged) return;
      const r = t.getBoundingClientRect();
      const before = (e.clientY - r.top) < r.height / 2;
      list.insertBefore(dragged, before ? t : t.nextSibling);
    });
    list.addEventListener('drop', (e) => e.preventDefault());
  }
  function initPreviewDnDOptional() {
    const list = document.querySelector(PREVIEW_LIST_SELECTOR);
    if (!list) return;
    document.querySelectorAll('.drop-overlay, .drop-hint').forEach((el) => { el.style.pointerEvents = 'none'; });
    ensurePageIds(list); markPageGrabbable(list); bindPreviewDnD(list); savePageOrder(list);
  }

  // =========================
  // 7) Init + Mutations
  // =========================
  function init() {
    initGoalGridBlock();
    initConverterPage();
    initPreviewDnDOptional();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();

  const mo = new MutationObserver(() => {
    // apenas re-inicializa o DnD do preview, que é dinâmico
    initPreviewDnDOptional();
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
})();