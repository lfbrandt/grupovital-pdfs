/* global window, document, pdfjsLib */

import { createFileDropzone } from '../fileDropzone.js';
import {
  getCSRFToken, mostrarLoading, atualizarProgresso,
  resetarProgresso, mostrarMensagem
} from './utils.esm.js';

const ACCEPT_ALL_TO_PDF =
  '.csv,.doc,.docx,.odt,.rtf,.txt,.html,.htm,.xls,.xlsx,.ods,.ppt,.pptx,.odp,.jpg,.jpeg,.png,.bmp,.tiff,.tif,.pdf';

const GOALS = {
  'to-pdf':        { label: 'Arquivos → PDF',        endpoint: '/api/convert/to-pdf',   accept: ACCEPT_ALL_TO_PDF, convertBtnText: 'Vários PDFs (1 por arquivo)' },
  'pdf-to-docx':   { label: 'PDF → DOCX',            endpoint: '/api/convert/to-docx',  accept: '.pdf,application/pdf', convertBtnText: 'Converter para DOCX' },
  'pdf-to-csv':    { label: 'PDF → CSV',             endpoint: '/api/convert/to-csv',   accept: '.pdf,application/pdf', convertBtnText: 'Converter para CSV' },
  'pdf-to-xlsx':   { label: 'PDF → XLSX',            endpoint: '/api/convert/to-xlsx',  accept: '.pdf,application/pdf', convertBtnText: 'Converter para XLSX' },
  'sheet-to-csv':  { label: 'Planilha → CSV',        endpoint: '/api/convert/to-csv',   accept: '.xls,.xlsx,.ods,.csv', convertBtnText: 'Converter para CSV' },
  'sheet-to-xlsm': { label: 'Planilha → XLSM',       endpoint: '/api/convert/to-xlsm',  accept: '.xls,.xlsx,.ods,.csv', convertBtnText: 'Converter para XLSM' }
};

(function () {
  'use strict';

  const prefix    = 'convert';
  const $drop     = document.getElementById(`dropzone-${prefix}`);
  const $file     = document.getElementById(`input-${prefix}`);
  const $btnConv  = document.getElementById('btn-convert-all');
  const $btnMerge = document.getElementById('btn-merge-all');
  const $btnClear = document.getElementById('btn-clear-all');
  const $spin         = document.getElementById(`spinner-${prefix}`);
  const $list         = document.getElementById('result-list');
  const $resultsBlock = document.getElementById('cv-results-section');
  const $goalLab      = document.getElementById('goal-label');
  const $actionsBlock = document.querySelector('.cv-actions');

  if (!$drop || !$file || !$btnConv) return;

  if (window.pdfjsLib && !window.__pdfWorkerSet) {
    if (!pdfjsLib.GlobalWorkerOptions.workerSrc) {
      pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/pdfjs/pdf.worker.min.js';
    }
    window.__pdfWorkerSet = true;
  }

  const params = new URLSearchParams(window.location.search);
  const urlGoal = params.get('goal');

  const state = {
    goal: (urlGoal && GOALS[urlGoal]) ? urlGoal : 'to-pdf',
    files: []
  };

  let isBusy = false;

  // ---------------- Utils ----------------
  const ext = (n) => {
    const i = (n || '').lastIndexOf('.');
    return i >= 0 ? n.slice(i + 1).toLowerCase() : '';
  };

  const isImg = (e) => ['jpg', 'jpeg', 'png', 'bmp', 'tif', 'tiff', 'gif', 'webp'].includes(e);

  const fmtSize = (bytes) => {
    if (typeof bytes !== 'number') return '';

    const kb = bytes / 1024;
    const mb = kb / 1024;

    return mb >= 1 ? `${mb.toFixed(2)} MB` : `${kb.toFixed(1)} KB`;
  };

  const fileKey = (f) => [f.name, f.size, f.lastModified].join('::');

  function isPdfFile(file) {
    const name = String(file?.name || '').toLowerCase();
    const type = String(file?.type || '').toLowerCase();

    return type === 'application/pdf' || name.endsWith('.pdf');
  }

  // ========== Grid de seleção ==========
  let $grid, $badge, $gridWrapper;

  function ensureGrid() {
    if ($grid) return;

    const sideMode = document.querySelector('main.with-preview')?.classList.contains('thumbs-side');

    if (sideMode) {
      const sideWrap = document.getElementById('side-grid');

      if (sideWrap) {
        $gridWrapper = sideWrap;

        $grid = document.getElementById('file-grid-convert')
          || sideWrap.appendChild(Object.assign(document.createElement('div'), {
            id: 'file-grid-convert',
            className: 'file-grid'
          }));

        $badge = document.getElementById('badge-count-convert') || sideWrap.querySelector('.badge');

        try {
          $gridWrapper.setAttribute('role', 'region');
          $gridWrapper.setAttribute('aria-live', 'polite');
          $gridWrapper.setAttribute('aria-label', '0 arquivos selecionados');
        } catch (_) {}

        const legacy = document.getElementById('first-pdf-thumb');
        if (legacy) legacy.hidden = true;

        sideWrap.hidden = false;
        return;
      }
    }

    const $container = $drop.closest('.dropzone-container') || $drop.parentElement;

    $gridWrapper = document.createElement('div');
    $gridWrapper.className = 'preview-container';

    const header = document.createElement('div');
    header.className = 'preview-header';

    const h3 = document.createElement('h3');
    h3.className = 'preview-title';
    h3.textContent = 'Arquivos selecionados';

    $badge = document.createElement('span');
    $badge.className = 'badge badge--success';
    $badge.textContent = 'Arquivo 0';

    try {
      $gridWrapper.setAttribute('role', 'region');
      $gridWrapper.setAttribute('aria-live', 'polite');
      $gridWrapper.setAttribute('aria-label', '0 arquivos selecionados');
    } catch (_) {}

    header.appendChild(h3);
    header.appendChild($badge);

    $grid = document.createElement('div');
    $grid.id = 'file-grid-convert';
    $grid.className = 'file-grid';

    $gridWrapper.appendChild(header);
    $gridWrapper.appendChild($grid);

    const $results = $container.querySelector('.preview-container--result');

    if ($results) {
      $container.insertBefore($gridWrapper, $results);
    } else {
      $container.appendChild($gridWrapper);
    }
  }

  function updateBadge() {
    const n = state.files.length;

    if ($badge) {
      $badge.textContent = n === 1 ? 'Arquivo 1' : `Arquivo ${n}`;
    }

    try {
      if ($gridWrapper) {
        $gridWrapper.setAttribute(
          'aria-label',
          `${n} arquivo${n === 1 ? '' : 's'} selecionado${n === 1 ? '' : 's'}`
        );
      }
    } catch (_) {}
  }

  function setButtonState(button, disabled) {
    if (!button) return;

    button.disabled = disabled;

    if (disabled) {
      button.setAttribute('disabled', 'disabled');
      button.setAttribute('aria-disabled', 'true');
    } else {
      button.removeAttribute('disabled');
      button.setAttribute('aria-disabled', 'false');
    }
  }

  function updateActionsBlockState(hasFiles, locked) {
    if (!$actionsBlock) return;

    const isEmpty = !hasFiles;
    const isReady = hasFiles && !locked;

    $actionsBlock.classList.toggle('cv-block--empty', isEmpty);
    $actionsBlock.classList.toggle('cv-block--ready', isReady);
    $actionsBlock.classList.toggle('cv-block--busy', locked);

    $actionsBlock.setAttribute('aria-disabled', locked ? 'true' : 'false');
  }

  function toggleButtons(forceDisabled = null) {
    const hasFiles = Array.isArray(state.files) && state.files.length > 0;
    const locked = forceDisabled === true || (forceDisabled === null && isBusy);
    const shouldDisableMain = locked || !hasFiles;

    setButtonState($btnConv, shouldDisableMain);
    setButtonState($btnMerge, shouldDisableMain);
    setButtonState($btnClear, locked);

    updateActionsBlockState(hasFiles, locked);

    $file.disabled = locked;

    console.log('[convert] toggleButtons', {
      goal: state.goal,
      files: state.files.length,
      locked,
      shouldDisableMain,
      buttonDisabled: $btnConv.disabled,
      actionsEmpty: $actionsBlock?.classList.contains('cv-block--empty'),
      actionsReady: $actionsBlock?.classList.contains('cv-block--ready'),
      actionsBusy: $actionsBlock?.classList.contains('cv-block--busy')
    });

    updateBadge();
  }

  function removeAt(idx) {
    state.files.splice(idx, 1);
    renderGrid();
  }

  // ---------- Thumbs locais ----------
  async function makePdfThumb(file) {
    if (!window.pdfjsLib) return null;

    try {
      const ab = await file.arrayBuffer();
      const pdf = await pdfjsLib.getDocument({ data: new Uint8Array(ab) }).promise;
      const page = await pdf.getPage(1);
      const viewport = page.getViewport({ scale: 0.35 });
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');

      canvas.width = viewport.width;
      canvas.height = viewport.height;

      await page.render({ canvasContext: ctx, viewport }).promise;

      return canvas.toDataURL('image/png');
    } catch {
      return null;
    }
  }

  function makeIndexPill(n) {
    const pill = document.createElement('span');
    pill.className = 'file-card__index';
    pill.textContent = String(n);
    return pill;
  }

  function renderCard(file, idx) {
    const e = ext(file.name);

    const card = document.createElement('div');
    card.className = 'file-card';
    card.setAttribute('draggable', 'true');
    card.dataset.index = String(idx);

    const pill = makeIndexPill(idx + 1);

    const img = document.createElement('img');
    img.className = 'file-card__thumb';
    img.alt = '';

    const name = document.createElement('div');
    name.className = 'file-card__name';
    name.textContent = file.name;

    const meta = document.createElement('div');
    meta.className = 'file-card__meta';
    meta.textContent = `${e.toUpperCase()} ${file.size ? '• ' + fmtSize(file.size) : ''}`;

    const rm = document.createElement('button');
    rm.className = 'btn btn--sm file-card__remove';
    rm.type = 'button';
    rm.textContent = 'Remover';
    rm.addEventListener('click', () => removeAt(idx));

    (async () => {
      if (isImg(e)) {
        const fr = new FileReader();

        fr.onload = () => {
          img.src = fr.result;
        };

        fr.readAsDataURL(file);
      } else if (e === 'pdf') {
        const dataUrl = await makePdfThumb(file);

        if (dataUrl) {
          img.src = dataUrl;
        } else {
          img.remove();
        }
      } else {
        img.remove();
      }
    })();

    card.addEventListener('dragstart', (ev) => {
      card.classList.add('dragging');
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', String(idx));
    });

    card.addEventListener('dragend', () => {
      card.classList.remove('dragging');
    });

    card.addEventListener('dragover', (ev) => {
      ev.preventDefault();
      card.classList.add('drop-target');
    });

    card.addEventListener('dragleave', () => {
      card.classList.remove('drop-target');
    });

    card.addEventListener('drop', (ev) => {
      ev.preventDefault();
      card.classList.remove('drop-target');

      const from = Number(ev.dataTransfer.getData('text/plain'));
      const to = Number(card.dataset.index || '0');

      if (Number.isNaN(from) || Number.isNaN(to) || from === to) return;

      const [moved] = state.files.splice(from, 1);
      state.files.splice(to, 0, moved);

      renderGrid();
    });

    card.appendChild(pill);
    card.appendChild(img);
    card.appendChild(name);
    card.appendChild(meta);
    card.appendChild(rm);

    return card;
  }

  function renderGrid() {
    ensureGrid();

    $grid.innerHTML = '';

    state.files.forEach((f, i) => {
      $grid.appendChild(renderCard(f, i));
    });

    [...$grid.children].forEach((el, i) => {
      el.dataset.index = String(i);
    });

    console.log('[convert] renderGrid', {
      goal: state.goal,
      files: state.files.length,
      cards: $grid.children.length
    });

    toggleButtons();

    window.requestAnimationFrame(() => {
      toggleButtons();
    });
  }

  function applyGoal() {
    const meta = GOALS[state.goal] || GOALS['to-pdf'];

    $file.setAttribute('accept', meta.accept);
    $drop.setAttribute('data-extensions', meta.accept);

    if ($goalLab) {
      $goalLab.textContent = meta.label;
    }

    if ($btnConv) {
      $btnConv.textContent = meta.convertBtnText || 'Converter';
    }

    if ($btnMerge) {
      if (state.goal === 'to-pdf') {
        $btnMerge.style.display = '';
      } else {
        $btnMerge.style.display = 'none';
      }
    }

    console.log('[convert] applyGoal', {
      goal: state.goal,
      endpoint: meta.endpoint,
      accept: meta.accept
    });
  }
  // -------- Dedup --------
  function addFilesDedup(list) {
    const seen = new Set(state.files.map(fileKey));

    for (const f of list) {
      const k = fileKey(f);

      if (!seen.has(k)) {
        state.files.push(f);
        seen.add(k);
      }
    }
  }

  // -------- Entrada centralizada de arquivos --------
  // Único ponto de entrada para arquivos vindos de qualquer fonte
  // (dropzone click, drag-and-drop). Nunca limpar $file.value aqui.
  function handleSelectedFiles(files, source) {
    const list = Array.from(files || []);
    if (!list.length) return;

    const before = state.files.length;
    addFilesDedup(list);
    renderGrid();

    console.log('[convert] arquivos adicionados', {
      source,
      received: list.length,
      before,
      after: state.files.length
    });
  }  // ---------- Dropzone ----------
  $drop.addEventListener('click', (e) => {
    if (e.target === $file) return;
    e.preventDefault();
  }, true /* capture: impede duplo diálogo do <label for="…"> */);

  $file.addEventListener('click', (e) => {
    e.stopPropagation(); // impede re-bubble que reabre o diálogo
  });

  createFileDropzone($drop, {
    multiple: true,

    onAdd: (file) => {
      // fileDropzone.js já validou o accept — sem re-verificação aqui
      handleSelectedFiles([file], 'dropzone');
    },

    onClear: () => {
      if (isBusy) return;

      state.files = [];

      if ($grid) {
        $grid.innerHTML = '';
      }

      if ($list) {
        $list.innerHTML = '';
      }

      if ($prog) {
        resetarProgresso($prog);
      }

      toggleButtons();
    }
  });
  if ($btnClear) {
    $btnClear.addEventListener('click', () => {
      if (isBusy) return;

      state.files = [];

      renderGrid();

      if ($prog) {
        resetarProgresso($prog);
      }

      if ($list) {
        $list.innerHTML = '';
      }
    });
  }

  // ---------- Barra de progresso ----------
  const $prog = document.getElementById(`progress-${prefix}`) || (function () {
    if (!$drop?.parentElement) return null;

    const barContainer = document.createElement('div');
    barContainer.className = 'progress';

    const bar = document.createElement('div');
    bar.className = 'progress-bar';
    bar.style.width = '0%';
    bar.id = `progress-${prefix}`;

    barContainer.appendChild(bar);
    $drop.parentElement.appendChild(barContainer);

    return bar;
  })();

  applyGoal();
  ensureGrid();
  toggleButtons();

  (async function loadGoalFromSession() {
    if (urlGoal && GOALS[urlGoal]) return;

    try {
      const r = await fetch('/api/convert/goal', {
        credentials: 'same-origin'
      });

      if (!r.ok) return;

      const data = await r.json();

      if (data && data.goal && GOALS[data.goal]) {
        state.goal = data.goal;
        applyGoal();
        toggleButtons();

        console.log('[convert] goal carregado da sessão', {
          goal: state.goal,
          files: state.files.length
        });
      }
    } catch (_) {}
  })();
  // ---------- Estado do bloco de resultados ----------
  function updateResultsBlockState(hasResults) {
    if (!$resultsBlock) return;
    $resultsBlock.classList.toggle('cv-block--empty', !hasResults);
    $resultsBlock.classList.toggle('cv-block--ready', hasResults);
  }
  // ---------- Render dos resultados ----------

  /**
   * Garante que uma URL local de resultado force o download adicionando ?download=1.
   * URLs externas ou inválidas são devolvidas sem alteração.
   */
  function forceDownloadUrl(rawUrl) {
    if (!rawUrl || rawUrl.startsWith('#')) return rawUrl || '#';
    try {
      const u = new URL(rawUrl, window.location.origin);
      // Só modifica URLs do próprio servidor
      if (u.origin === window.location.origin) {
        u.searchParams.set('download', '1');
        return u.pathname + u.search + u.hash;
      }
    } catch (_) { /* URL inválida — devolve como está */ }
    return rawUrl;
  }

  function renderAPIResults(items) {
    if (!$list) return;

    $list.innerHTML = '';

    if (!items || !items.length) {
      $list.innerHTML = '<li class="muted">Nada para baixar.</li>';
      updateResultsBlockState(false);
      return;
    }

    updateResultsBlockState(true);

    for (const it of items) {
      const li = document.createElement('li');
      li.className = 'result-item';

      const rawUrl = it.download_url || it.url || it.download || '';

      if (rawUrl) {
        const a = document.createElement('a');
        a.href = forceDownloadUrl(rawUrl);
        // sem target="_blank" — download direto na mesma janela
        a.download = it.name || '';
        a.textContent = it.name || 'arquivo';

        li.appendChild(a);
      } else {
        const span = document.createElement('span');
        span.className = 'result-link result-link--disabled';
        span.textContent = `${it.name || 'arquivo'} — link de download não retornado pelo servidor`;

        li.appendChild(span);

        console.warn('[convert] resultado sem URL de download', it);
      }

      if (typeof it.size === 'number') {
        const size = document.createElement('span');
        size.className = 'file-size';
        size.textContent = ` · ${fmtSize(it.size)}`;
        li.appendChild(size);
      }

      $list.appendChild(li);
    }
  }

  // ---------- Helpers de lock ----------
  function lockUI() {
    isBusy = true;
    toggleButtons(true);
    mostrarLoading($spin, true);
  }

  function unlockUI() {
    isBusy = false;
    toggleButtons(false);
    mostrarLoading($spin, false);
  }

  // ======================
  // Converter N -> N (JSON)
  // ======================
  async function convertAll() {
    console.log('[convert] clique converter', {
      goal: state.goal,
      endpoint: GOALS[state.goal]?.endpoint,
      files: state.files.length,
      isBusy,
      buttonDisabled: $btnConv.disabled,
      actionsEmpty: $actionsBlock?.classList.contains('cv-block--empty'),
      actionsReady: $actionsBlock?.classList.contains('cv-block--ready')
    });

    if (isBusy) return;

    const selectedFiles = Array.isArray(state.files) ? state.files : [];

    const hasValidFiles =
      state.goal === 'pdf-to-xlsx'
        ? selectedFiles.some(isPdfFile)
        : selectedFiles.length > 0;

    if (!hasValidFiles) {
      mostrarMensagem('Adicione um arquivo válido antes de converter.', 'warning');

      console.warn('[convert] conversão bloqueada: nenhum arquivo válido', {
        goal: state.goal,
        files: selectedFiles.map((f) => ({
          name: f?.name,
          type: f?.type,
          size: f?.size
        }))
      });

      return;
    }

    if ($prog) {
      resetarProgresso($prog);
    }

    if ($list) {
      $list.innerHTML = '';
    }

    lockUI();

    try {
      const fd = new FormData();

      for (const f of selectedFiles) {
        fd.append('files[]', f, f.name);
        fd.append('files', f, f.name);
      }

      const csrf = getCSRFToken();

      const meta = GOALS[state.goal] || GOALS['to-pdf'];

      if (!meta?.endpoint) {
        throw new Error(`Objetivo de conversão inválido: ${state.goal}`);
      }

      console.log('[convert] enviando requisição', {
        goal: state.goal,
        endpoint: meta.endpoint,
        files: selectedFiles.length
      });

      const res = await fetch(meta.endpoint, {
        method: 'POST',
        body: fd,
        headers: csrf
          ? { 'X-CSRFToken': csrf, 'Accept': 'application/json' }
          : { 'Accept': 'application/json' },
        credentials: 'same-origin'
      });

      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        throw new Error(txt || `HTTP ${res.status}`);
      }

      const data = await res.json().catch(() => null);

      if (!data || !Array.isArray(data.files)) {
        throw new Error('Resposta inesperada do servidor.');
      }

      renderAPIResults(data.files);

      if ($prog) {
        atualizarProgresso($prog, 100);
      }

      mostrarMensagem(
        `Conversão concluída (${data.count || data.files.length} arquivo(s)).`,
        'success'
      );
    } catch (err) {
      console.error(err);
      mostrarMensagem(`Falha: ${err.message || err}`, 'error');
    } finally {
      unlockUI();
    }
  }

  // ======================
  // Unir em 1 PDF (JSON) — só visível na meta "Arquivos → PDF"
  // ======================
  async function mergeAll() {
    if (state.goal !== 'to-pdf') return;
    if (isBusy) return;

    if (!state.files.length) {
      return mostrarMensagem('Adicione arquivos antes de unir.', 'warning');
    }

    if ($prog) {
      resetarProgresso($prog);
    }

    lockUI();

    try {
      const fd = new FormData();

      for (const f of state.files) {
        fd.append('files[]', f, f.name);
      }

      const csrf = getCSRFToken();

      const res = await fetch('/api/convert/merge-a4', {
        method: 'POST',
        body: fd,
        headers: csrf
          ? { 'X-CSRFToken': csrf, 'Accept': 'application/json' }
          : { 'Accept': 'application/json' },
        credentials: 'same-origin'
      });

      if (!res.ok) {
        const txt = await res.text().catch(() => '');
        throw new Error(txt || `HTTP ${res.status}`);
      }

      const data = await res.json().catch(() => null);

      if (!data || !Array.isArray(data.files) || !data.files.length) {
        throw new Error('Não foi possível gerar o PDF único.');
      }

      renderAPIResults(data.files);

      const unico = data.files[0];

      if (unico.download_url) {
        const a = document.createElement('a');
        a.href = unico.download_url;
        a.target = '_blank';
        a.rel = 'noopener';
        a.click();
      } else if (unico.url) {
        const a = document.createElement('a');
        a.href = unico.url;
        a.target = '_blank';
        a.rel = 'noopener';
        a.click();
      }

      if ($prog) {
        atualizarProgresso($prog, 100);
      }

      mostrarMensagem('PDF único gerado com sucesso.', 'success');
    } catch (err) {
      console.error(err);
      mostrarMensagem(`Falha ao unir: ${err.message || err}`, 'error');
    } finally {
      unlockUI();
    }
  }

  $btnConv.addEventListener('click', (e) => {
    e.preventDefault();
    convertAll();
  });

  if ($btnMerge) {
    $btnMerge.addEventListener('click', (e) => {
      e.preventDefault();
      mergeAll();
    });
  }

  (function tryShowThumb() {
    const img = document.getElementById('thumb-convert');

    if (img) {
      img.hidden = true;
    }
  })();
})();