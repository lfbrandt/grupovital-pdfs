/* global window, document, pdfjsLib */

import { createFileDropzone } from '../fileDropzone.js';
import {
  getCSRFToken, mostrarLoading, atualizarProgresso,
  resetarProgresso, mostrarMensagem
} from './utils.js';

const ACCEPT_ALL_TO_PDF =
  '.csv,.doc,.docx,.odt,.rtf,.txt,.html,.htm,.xls,.xlsx,.ods,.ppt,.pptx,.odp,.jpg,.jpeg,.png,.bmp,.tiff,.tif,.pdf';

const GOALS = {
  'to-pdf':        { label: 'Arquivos → PDF', endpoint: '/api/convert/to-pdf',        accept: ACCEPT_ALL_TO_PDF },
  'pdf-to-docx':   { label: 'PDF → DOCX',     endpoint: '/api/convert/to-docx',      accept: '.pdf,application/pdf' },
  'pdf-to-csv':    { label: 'PDF → CSV',      endpoint: '/api/convert/to-csv',       accept: '.pdf,application/pdf' },
  'pdf-to-xlsx':   { label: 'PDF → XLSX',     endpoint: '/api/convert/to-xlsx',      accept: '.pdf,application/pdf' },
  'sheet-to-csv':  { label: 'Planilha → CSV', endpoint: '/api/convert/to-csv',       accept: '.xls,.xlsx,.ods,.csv' },
  'sheet-to-xlsm': { label: 'Planilha → XLSM',endpoint: '/api/convert/to-xlsm',      accept: '.xls,.xlsx,.ods,.csv' }
};

(function () {
  const prefix    = 'convert';
  const $drop     = document.getElementById(`dropzone-${prefix}`);
  const $file     = document.getElementById(`input-${prefix}`);
  const $btnConv  = document.getElementById('btn-convert-all');
  const $btnMerge = document.getElementById('btn-merge-all');
  const $btnClear = document.getElementById('btn-clear-all');
  const $spin     = document.getElementById(`spinner-${prefix}`);
  const $list     = document.getElementById('result-list');
  const $goalLab  = document.getElementById('goal-label');

  if (!$drop || !$file || !$btnConv) return;

  if (window.pdfjsLib && !pdfjsLib.GlobalWorkerOptions.workerSrc) {
    pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/pdfjs/pdf.worker.min.js';
  }

  const params = new URLSearchParams(window.location.search);
  const urlGoal = params.get('goal');
  const state = {
    goal: (urlGoal && GOALS[urlGoal]) ? urlGoal : 'to-pdf',
    files: []
  };

  const ext = (n) => {
    const i = (n || '').lastIndexOf('.');
    return i >= 0 ? n.slice(i + 1).toLowerCase() : '';
  };
  const isImg = (e) => ['jpg','jpeg','png','bmp','tif','tiff','gif','webp'].includes(e);
  const fmtSize = (bytes) => {
    if (typeof bytes !== 'number') return '';
    const kb = bytes/1024, mb = kb/1024;
    return mb >= 1 ? `${mb.toFixed(2)} MB` : `${kb.toFixed(1)} KB`;
  };

  // ===== Grid (agora com suporte à coluna lateral) =====
  let $grid, $badge, $gridWrapper;

  function ensureGrid() {
    if ($grid) return;

    const sideMode = document.querySelector('main.with-preview')?.classList.contains('thumbs-side');

    if (sideMode) {
      const sideWrap = document.getElementById('side-grid');
      if (sideWrap) {
        // usa a estrutura já presente no HTML
        $gridWrapper = sideWrap;
        $grid  = document.getElementById('file-grid-convert') || sideWrap.appendChild(Object.assign(document.createElement('div'), { id: 'file-grid-convert', className: 'file-grid' }));
        $badge = document.getElementById('badge-count-convert') || sideWrap.querySelector('.badge');

        // esconde miniatura “legada”
        const legacy = document.getElementById('first-pdf-thumb');
        if (legacy) legacy.hidden = true;

        sideWrap.hidden = false;
        return;
      }
    }

    // fallback: grid acima da área de resultados
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

    header.appendChild(h3);
    header.appendChild($badge);

    $grid = document.createElement('div');
    $grid.id = 'file-grid-convert';
    $grid.className = 'file-grid';

    $gridWrapper.appendChild(header);
    $gridWrapper.appendChild($grid);

    const $results = $container.querySelector('.preview-container--result');
    if ($results) $container.insertBefore($gridWrapper, $results);
    else $container.appendChild($gridWrapper);
  }

  function updateBadge() {
    const n = state.files.length;
    if ($badge) $badge.textContent = n === 1 ? 'Arquivo 1' : `Arquivo ${n}`;
  }

  function toggleButtons() {
    const ena = state.files.length > 0;
    $btnConv.disabled  = !ena;
    if ($btnMerge) $btnMerge.disabled = !ena;
    updateBadge();
  }

  function removeAt(idx) {
    state.files.splice(idx, 1);
    renderGrid();
  }

  async function makePdfThumb(file) {
    if (!window.pdfjsLib) return null;
    try {
      const ab = await file.arrayBuffer();
      const pdf = await pdfjsLib.getDocument({ data: new Uint8Array(ab) }).promise;
      const page = await pdf.getPage(1);
      const viewport = page.getViewport({ scale: 0.35 });
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      canvas.width = viewport.width; canvas.height = viewport.height;
      await page.render({ canvasContext: ctx, viewport }).promise;
      return canvas.toDataURL('image/png');
    } catch { return null; }
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
        fr.onload = () => { img.src = fr.result; };
        fr.readAsDataURL(file);
      } else if (e === 'pdf') {
        const dataUrl = await makePdfThumb(file);
        if (dataUrl) img.src = dataUrl; else img.remove();
      } else {
        img.remove();
      }
    })();

    // DnD para reordenar
    card.addEventListener('dragstart', (ev) => {
      card.classList.add('dragging');
      ev.dataTransfer.effectAllowed = 'move';
      ev.dataTransfer.setData('text/plain', String(idx));
    });
    card.addEventListener('dragend', () => { card.classList.remove('dragging'); });
    card.addEventListener('dragover', (ev) => { ev.preventDefault(); card.classList.add('drop-target'); });
    card.addEventListener('dragleave', () => { card.classList.remove('drop-target'); });
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
    state.files.forEach((f, i) => $grid.appendChild(renderCard(f, i)));
    [...$grid.children].forEach((el, i) => { el.dataset.index = String(i); });
    toggleButtons();
  }

  function applyGoal() {
    const meta = GOALS[state.goal];
    $file.setAttribute('accept', meta.accept);
    $drop.setAttribute('data-extensions', meta.accept);
    if ($goalLab) $goalLab.textContent = meta.label;
  }

  createFileDropzone($drop, {
    multiple: true,
    onAdd: (file) => {
      const a = ($file.getAttribute('accept') || '').toLowerCase();
      const ok = !a || a.split(',').some(s => {
        s = s.trim();
        return (s === 'application/pdf' && ext(file.name) === 'pdf') || s === '.' + ext(file.name);
      });
      if (!ok) return;
      state.files.push(file);
      renderGrid();
    },
    onClear: () => {
      state.files = [];
      if ($grid) $grid.innerHTML = '';
      if ($list) $list.innerHTML = '';
      if ($prog) resetarProgresso($prog);
      toggleButtons();
    },
  });

  if ($btnClear) {
    $btnClear.addEventListener('click', () => {
      state.files = [];
      renderGrid();
      if ($prog) resetarProgresso($prog);
      if ($list) $list.innerHTML = '';
    });
  }

  const $prog = document.getElementById(`progress-${prefix}`) || (function(){
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

  applyGoal(); ensureGrid(); toggleButtons();

  (async function loadGoalFromSession() {
    try {
      const r = await fetch('/api/convert/goal', { credentials: 'same-origin' });
      if (!r.ok) return;
      const data = await r.json();
      if (data && data.goal && GOALS[data.goal]) {
        state.goal = data.goal; applyGoal();
      }
    } catch {}
  })();

  async function convertAll() {
    if (!state.files.length) return mostrarMensagem('Adicione arquivos antes de converter.', 'warning');

    const meta = GOALS[state.goal];
    const fd = new FormData();
    for (const f of state.files) fd.append('files[]', f, f.name);

    const csrf = getCSRFToken();
    if ($prog) resetarProgresso($prog);
    mostrarLoading($spin, true);

    try {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', meta.endpoint, true);
      if (csrf) xhr.setRequestHeader('X-CSRFToken', csrf);

      xhr.upload.onprogress = (e) => {
        if (!e.lengthComputable || !$prog) return;
        atualizarProgresso($prog, Math.round((e.loaded / e.total) * 100));
      };

      const resp = await new Promise((resolve, reject) => {
        xhr.onreadystatechange = () => {
          if (xhr.readyState !== 4) return;
          if (xhr.status >= 200 && xhr.status < 300) {
            try { resolve(JSON.parse(xhr.responseText)); }
            catch (err) { reject(err); }
          } else {
            reject(new Error(xhr.responseText || `Erro HTTP ${xhr.status}`));
          }
        };
        xhr.send(fd);
      });

      if ($list) {
        $list.innerHTML = '';
        (resp.files || []).forEach((f) => {
          const li = document.createElement('li');
          li.className = 'result-item';
          li.innerHTML = `
            <span class="result-name">${f.name}</span>
            <a class="btn btn-small" href="${f.download_url}">Baixar</a>
            ${typeof f.size === 'number' ? `<small class="muted">${(f.size/1024).toFixed(1)} KB</small>` : ''}
          `;
          $list.appendChild(li);
        });
      }

      if ($prog) atualizarProgresso($prog, 100);
      mostrarMensagem(`Convertidos: ${resp.count}`, 'success');

    } catch (err) {
      console.error(err);
      mostrarMensagem(`Falha: ${err.message || err}`, 'error');
    } finally {
      mostrarLoading($spin, false);
    }
  }

  async function mergeAll() {
    if (!state.files.length) return mostrarMensagem('Adicione arquivos antes de unir.', 'warning');

    const fd = new FormData();
    for (const f of state.files) fd.append('files[]', f, f.name);

    const csrf = getCSRFToken();
    if ($prog) resetarProgresso($prog);
    mostrarLoading($spin, true);

    try {
      const res = await fetch('/api/convert/to-pdf-merge', {
        method: 'POST',
        body: fd,
        headers: csrf ? { 'X-CSRFToken': csrf } : undefined,
        credentials: 'same-origin'
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const blob = await res.blob();
      const a = document.createElement('a');
      const url = URL.createObjectURL(blob);
      a.href = url; a.download = 'arquivos_unidos.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1500);

      if ($prog) atualizarProgresso($prog, 100);
      mostrarMensagem('PDF único gerado com sucesso.', 'success');

    } catch (err) {
      console.error(err);
      mostrarMensagem(`Falha ao unir: ${err.message || err}`, 'error');
    } finally {
      mostrarLoading($spin, false);
    }
  }

  $btnConv.addEventListener('click', (e) => { e.preventDefault(); convertAll(); });
  if ($btnMerge) $btnMerge.addEventListener('click', (e) => { e.preventDefault(); mergeAll(); });

  function tryShowThumb() {
    const img = document.getElementById('thumb-convert');
    if (img) img.hidden = true;
  }
})();