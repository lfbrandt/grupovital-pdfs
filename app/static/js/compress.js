/* ========================================================================
   compress.js — interface de tela única com blocos de estado
   Compatível com utils.js UMD (window.getCSRFToken exposto globalmente).
   NÃO usa ES Module import — carregado como <script defer> clássico.
   ======================================================================== */
'use strict';

function readCSRFToken() {
  if (typeof window.getCSRFToken === 'function') return window.getCSRFToken();
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

console.debug('[compress] módulo carregado');

const __GV_COMPRESS = (window.__GV_COMPRESS = window.__GV_COMPRESS || {});
const _AState = { analyseId: null, pages: [], filter: 'all', inflight: false };

/* ── Tempo de auto-reset após resultado ─────────────────────────────────────
 * Valor único e centralizado — evita múltiplos valores dispersos no arquivo.
 * 9 s dá tempo suficiente para o utilizador ler o resultado antes de resetar.
 * ─────────────────────────────────────────────────────────────────────────── */
const _RESET_DELAY_MS = 9000;

/* ── Blocos de estado ───────────────────────────────────────────────────── */
function _setBlockState(id, state) {
  const el = document.getElementById(id); if (!el) return;
  el.classList.remove('cz-block--empty', 'cz-block--loading', 'cz-block--ready');
  el.classList.add(`cz-block--${state}`);
}

function _resetAllBlocks() {
  _setBlockState('cz-summary',         'empty');
  _setBlockState('cz-controls',        'empty');
  _setBlockState('page-analysis-grid', 'empty');
  ['global-quality', 'global-dpi', 'filter-all', 'filter-large'].forEach(id => {
    const el = document.getElementById(id); if (el) el.disabled = true;
  });
  ['filter-all-count', 'filter-large-count', 'selected-count', 'total-count'].forEach(id => {
    const el = document.getElementById(id); if (el) el.textContent = '—';
  });
  ['cz-filename', 'cz-pages', 'cs-original-val', 'cs-adjusted-val'].forEach(id => {
    const el = document.getElementById(id); if (el) el.textContent = '—';
  });
  const adjLabelEl = document.getElementById('cs-adjusted-label');
  if (adjLabelEl) adjLabelEl.textContent = 'Prévia';
  const badge = document.getElementById('cs-badge');
  if (badge) { badge.textContent = ''; badge.className = 'cs-badge'; }
  const grid = document.getElementById('page-analysis-grid');
  if (grid) {
    grid.innerHTML = `
      <div class="cz-skeleton-grid" aria-hidden="true">
        <div class="cz-skeleton-card"></div>
        <div class="cz-skeleton-card"></div>
        <div class="cz-skeleton-card"></div>
      </div>`;
    delete grid.__analysisEventsBound;
  }
}

/* ── Feedback / progresso ───────────────────────────────────────────────── */
function _setFeedback(msg, type) {
  const el = document.getElementById('mensagem-feedback'); if (!el) return;
  el.textContent = msg;
  // Remove apenas os modificadores de estado anteriores — preserva classes estruturais
  // que possam estar no HTML (ex.: 'hidden', classes de layout).
  // Antes: el.className = ... sobrescrevia tudo, incluindo classes do HTML.
  el.classList.remove('feedback--success', 'feedback--error', 'feedback--info');
  if (type) el.classList.add(`feedback--${type}`);
  el.classList.remove('hidden');
}
function _clearFeedback() {
  const el = document.getElementById('mensagem-feedback');
  if (el) { el.textContent = ''; el.classList.add('hidden'); }
}
function _setProgress(pct) {
  const c = document.getElementById('progress-container');
  const b = document.getElementById('progress-bar');
  if (!c || !b) return;
  c.classList.remove('hidden');
  b.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  c.setAttribute('aria-valuenow', String(Math.round(pct)));
}
function _resetProgress() {
  const c = document.getElementById('progress-container');
  const b = document.getElementById('progress-bar');
  if (c) c.classList.add('hidden');
  if (b) b.style.width = '0%';
}
function _setSpinner(on) {
  const s = document.getElementById('spinner-compress'); if (!s) return;
  s.classList.toggle('hidden', !on);
}
function _setSteps(steps) {
  const el = document.getElementById('loading-steps'); if (!el) return;
  if (!steps || !steps.length) { el.innerHTML = ''; el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  el.innerHTML = steps.map(s => {
    const icon = s.status === 'done' ? '✓' : s.status === 'current' ? '→' : '○';
    return `<li class="ls-step ls-step--${s.status || 'pending'}">${icon} ${s.text}</li>`;
  }).join('');
}

/* ── Botão Limpar ───────────────────────────────────────────────────────── */
function bindClearButton() {
  if (document.__compressClearBound) return;
  document.__compressClearBound = true;
  document.addEventListener('click', ev => {
    if (!ev.target.closest('#btn-clear-all')) return;
    _AState.analyseId = null; _AState.pages = []; _AState.filter = 'all';
    __GV_COMPRESS.inputBound = false;
    const input = document.getElementById('input-compress'); if (input) input.value = '';
    _resetAllBlocks();
    _clearFeedback(); _resetProgress(); _setSteps([]);
    bindUploadOnce();
  });
}

/* ── Estimativa de tamanho ──────────────────────────────────────────────── */
function _fmtKB(kb) {
  return kb >= 1024 ? `${(kb / 1024).toFixed(2)} MB` : `${kb.toFixed(1)} KB`;
}
function _estimateSize(page) {
  if (!page.include) return 0;
  if (page.keep_original) return parseFloat(page.estimated_size_kb);

  const orig = parseFloat(page.estimated_size_kb);
  const q    = page.quality;
  const dpi  = page.dpi;

  // size_factor vem do backend (enrich_page_analysis).
  // sf > 1 → página maior que a média → menos ganho marginal esperado.
  const sf  = parseFloat(page.size_factor || 1.0);
  const sfF = 1 / Math.pow(sf, 0.20);   // sf=1→1.0  sf=2→0.87  sf=3→0.80

  // quality → fator calibrado com ponto real observado.
  // Calibração: orig=5.81 MB, q=77, dpi=100 → resultado real=4.01 MB (−31%).
  // q=20→0.734  q=60→0.842  q=77→0.888  q=80→0.896  q=100→0.950
  const qF = 0.68 + (q / 100) * 0.27;

  // dpi → âncora deslocada para 120 (era 150), expoente 1.3.
  // Âncora 150 projetava dpiF(100)=0.59 (−41%), enquanto o backend real
  // produz apenas −31% com dpi=100. Âncora 120 corrige essa superestimativa:
  // dpi=100→0.793 (−21%)  dpi=120→1.00  dpi=150→1.34 → clampado em orig.
  // Zona de variação visual útil: dpi 50→130. Acima de ~135, o arquivo
  // mal se comprime e o clamp Math.min(orig,...) trava corretamente.
  const dpiF = Math.pow(dpi / 120, 1.3);

  // resize_to_a4 — só reduz, nunca infla
  let rzF = 1;
  if (page.resize_to_a4) {
    const a = page.width * page.height;
    rzF = a > 0 ? Math.min(1, (595 * 842) / a) : 1;
  }

  const estimated = orig * qF * dpiF * sfF * rzF;

  // Clamp: nunca infla acima do original, mínimo absoluto de 10 KB.
  return Math.min(orig, Math.max(10, estimated));
}

/* ── Resumo e contadores ────────────────────────────────────────────────── */
function _updateSummary() {
  const pages    = _AState.pages;
  const totOrig  = pages.reduce((s, p) => s + (p.include ? parseFloat(p.estimated_size_kb) : 0), 0);
  const totAdj   = pages.reduce((s, p) => s + _estimateSize(p), 0);
  const pct      = totOrig > 0 ? (((totOrig - totAdj) / totOrig) * 100).toFixed(1) : 0;
  const selCount = pages.filter(p => p.include).length;

  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('cs-original-val', _fmtKB(totOrig));
  set('cs-adjusted-val', totAdj > 0 ? `~${_fmtKB(totAdj)}` : '—');
  set('selected-count',  selCount);
  set('total-count',     pages.length);
  const badge = document.getElementById('cs-badge');
  if (badge) {
    // Badge textual removido do analyze — não classificamos "leve/moderada/forte"
    // porque a prévia não controla fallback real, grupos ou comportamento do GS.
    // O badge só é usado após o processamento real (resultado exato do backend).
    badge.textContent = '';
    badge.className   = 'cs-badge';
  }

  const btnP = document.getElementById('btn-process-with-settings');
  if (btnP) btnP.disabled = selCount === 0 || _AState.inflight;

  document.querySelectorAll('.pac-size-adjusted').forEach(el => {
    const pn   = parseInt(el.closest('[data-page-number]')?.dataset?.pageNumber || '0', 10);
    const page = pages.find(p => p.page_number === pn); if (!page) return;
    const adj  = _estimateSize(page);
    el.textContent = `≈ ${_fmtKB(adj)}`;
    const bdg = el.closest('.pac-sizes')?.querySelector('.pac-reduction');
    if (bdg && page.include && !page.keep_original) {
      const r = (parseFloat(page.estimated_size_kb) - adj) / parseFloat(page.estimated_size_kb) * 100;
      bdg.textContent   = `${r.toFixed(0)}% menor`;
      bdg.style.display = r > 0 ? '' : 'none';
    }
  });
}

/* ── Cards de página ────────────────────────────────────────────────────── */
function _buildPageCard(page) {
  const card = document.createElement('article');
  card.className = `page-analysis-card${page.is_large ? ' pac--large' : ''}${!page.include ? ' pac--excluded' : ''}`;
  card.setAttribute('data-page-number', String(page.page_number));
  card.setAttribute('role', 'listitem');

  const adjKB  = _estimateSize(page);
  const origKB = parseFloat(page.estimated_size_kb);
  const redPct = origKB > 0 ? (((origKB - adjKB) / origKB) * 100).toFixed(0) : 0;
  const dis    = cond => cond ? 'disabled' : '';

  card.innerHTML = `
    <div class="pac-card-actions">
      <button type="button" class="pac-btn-toggle" data-action="toggle-include"
              title="${page.include ? 'Excluir página' : 'Incluir página'}"
              aria-label="${page.include ? 'Excluir página' : 'Incluir página'}">
        ${page.include
          ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
          : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
        }
      </button>
      <button type="button" class="pac-btn-rotate" data-action="rotate-cw"
              title="Girar 90° (horário)" aria-label="Girar página 90° no sentido horário">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 2v6h-6"/><path d="M21 13a9 9 0 1 1-3-7.7L21 8"/>
        </svg>
      </button>
    </div>
    <div class="pac-thumb" draggable="true">
      <img src="${page.thumbnail}" alt="Página ${page.page_number}" loading="lazy"
           decoding="async" data-rotation="0"
           width="240" height="338"
           style="transform-origin:center">
      ${page.is_large ? '<span class="pac-badge pac-badge--large">⚠ GRANDE</span>' : ''}
    </div>
    <div class="pac-info">
      <h4 class="pac-title">Pág. ${page.page_number}</h4>
      <p class="pac-dims">${page.width} × ${page.height} pt</p>
      <div class="pac-sizes">
        <span class="pac-size-original">≈ ${_fmtKB(origKB)}</span>
        <span class="pac-arrow" aria-hidden="true">→</span>
        <span class="pac-size-adjusted">≈ ${_fmtKB(adjKB)}</span>
        <span class="pac-reduction" style="${parseFloat(redPct) > 0 ? '' : 'display:none'}">${redPct}% menor</span>
      </div>
    </div>
    <div class="pac-controls">
      <label class="pac-check">
        <input type="checkbox" data-field="resize_to_a4" ${page.resize_to_a4 ? 'checked' : ''} ${dis(!page.include || page.keep_original)}>
        <span>📐 A4</span>
      </label>
      <label class="pac-check">
        <input type="checkbox" data-field="keep_original" ${page.keep_original ? 'checked' : ''} ${dis(!page.include)}>
        <span>🔒 Manter original</span>
      </label>
      <label class="pac-range">
        <span>Qualidade: <strong class="pac-quality-val">${page.quality}%</strong></span>
        <input type="range" data-field="quality" min="20" max="100" value="${page.quality}" ${dis(!page.include || page.keep_original)}>
      </label>
      <label class="pac-range">
        <span>DPI: <strong class="pac-dpi-val">${page.dpi}</strong></span>
        <input type="range" data-field="dpi" min="50" max="300" value="${page.dpi}" ${dis(!page.include || page.keep_original)}>
      </label>
    </div>`;
  return card;
}

function _refreshCardControls(card, page) {
  card.classList.toggle('pac--excluded', !page.include);
  const toggleBtn = card.querySelector('[data-action="toggle-include"]');
  if (toggleBtn) {
    toggleBtn.title     = page.include ? 'Excluir página' : 'Incluir página';
    toggleBtn.ariaLabel = toggleBtn.title;
    toggleBtn.innerHTML = page.include
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  }
  card.querySelectorAll('[data-field="resize_to_a4"]').forEach(el => { el.disabled = !page.include || page.keep_original; });
  card.querySelectorAll('[data-field="keep_original"]').forEach(el => { el.disabled = !page.include; });
  card.querySelectorAll('[data-field="quality"], [data-field="dpi"]').forEach(el => { el.disabled = !page.include || page.keep_original; });
}

function _bindCardEvents(grid) {
  if (grid.__analysisEventsBound) return;
  grid.__analysisEventsBound = true;

  let _dragSrc = null;

  grid.addEventListener('dragstart', ev => {
    if (!ev.target.closest('.pac-thumb')) { ev.preventDefault(); return; }
    const card = ev.target.closest('[data-page-number]'); if (!card) return;
    _dragSrc = card;
    card.classList.add('pac--dragging');
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('text/plain', card.dataset.pageNumber);
  });

  grid.addEventListener('dragend', ev => {
    const card = ev.target.closest('[data-page-number]'); if (!card) return;
    card.classList.remove('pac--dragging');
    grid.querySelectorAll('.pac--dragover').forEach(el => el.classList.remove('pac--dragover'));
    _dragSrc = null;
  });

  grid.addEventListener('dragover', ev => {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    const card = ev.target.closest('[data-page-number]');
    if (!card || card === _dragSrc) return;
    grid.querySelectorAll('.pac--dragover').forEach(el => el.classList.remove('pac--dragover'));
    card.classList.add('pac--dragover');
  });

  grid.addEventListener('dragleave', ev => {
    const card = ev.target.closest('[data-page-number]');
    if (card && !card.contains(ev.relatedTarget)) card.classList.remove('pac--dragover');
  });

  grid.addEventListener('drop', ev => {
    ev.preventDefault();
    const target = ev.target.closest('[data-page-number]');
    if (!target || !_dragSrc || target === _dragSrc) return;
    target.classList.remove('pac--dragover');
    const allCards = [...grid.querySelectorAll('[data-page-number]')];
    const srcIdx   = allCards.indexOf(_dragSrc);
    const tgtIdx   = allCards.indexOf(target);
    if (srcIdx < tgtIdx) grid.insertBefore(_dragSrc, target.nextSibling);
    else                 grid.insertBefore(_dragSrc, target);
    const srcPN = parseInt(_dragSrc.dataset.pageNumber, 10);
    const tgtPN = parseInt(target.dataset.pageNumber,   10);
    const pages = _AState.pages;
    const si    = pages.findIndex(p => p.page_number === srcPN);
    const ti    = pages.findIndex(p => p.page_number === tgtPN);
    if (si !== -1 && ti !== -1) { const [moved] = pages.splice(si, 1); pages.splice(ti, 0, moved); }
  });

  grid.addEventListener('click', ev => {
    const btn  = ev.target.closest('[data-action]'); if (!btn) return;
    const card = btn.closest('[data-page-number]');  if (!card) return;
    const pn   = parseInt(card.dataset.pageNumber, 10);
    const page = _AState.pages.find(p => p.page_number === pn); if (!page) return;
    if (btn.dataset.action === 'toggle-include') {
      page.include = !page.include;
      _refreshCardControls(card, page);
      _updateSummary();
    }
    if (btn.dataset.action === 'rotate-cw') {
      const img = card.querySelector('.pac-thumb img');
      if (img) {
        const cur  = parseInt(img.dataset.rotation || '0', 10);
        const next = (cur + 90) % 360;
        img.dataset.rotation = String(next);
        img.style.transform  = `rotate(${next}deg)`;
      }
    }
  });
  // 'input' dispara continuamente durante o arraste do range (tempo real).
  // 'change' só disparava ao soltar — causava UI congelada ao mover o slider de quality/dpi.
  grid.addEventListener('input', ev => {
    const input = ev.target; if (!input.matches('input[type="range"]')) return;
    const card  = input.closest('[data-page-number]'); if (!card) return;
    const pn    = parseInt(card.dataset.pageNumber, 10);
    const page  = _AState.pages.find(p => p.page_number === pn); if (!page) return;
    const field = input.dataset.field;
    const val   = parseInt(input.value, 10);
    page[field] = val;
    if (field === 'quality') card.querySelector('.pac-quality-val').textContent = `${val}%`;
    if (field === 'dpi')     card.querySelector('.pac-dpi-val').textContent     = String(val);
    _updateSummary();
  }, { passive: true });

  // 'change' mantido exclusivamente para checkboxes (resize_to_a4 / keep_original / include).
  grid.addEventListener('change', ev => {
    const input = ev.target; if (!input.matches('input[type="checkbox"]')) return;
    const card  = input.closest('[data-page-number]'); if (!card) return;
    const pn    = parseInt(card.dataset.pageNumber, 10);
    const page  = _AState.pages.find(p => p.page_number === pn); if (!page) return;
    const field = input.dataset.field;
    page[field] = input.checked;
    if (field === 'keep_original' && input.checked) page.resize_to_a4 = false;
    _refreshCardControls(card, page);
    _updateSummary();
  }, { passive: true });
}

function _renderPageGrid() {
  const grid  = document.getElementById('page-analysis-grid'); if (!grid) return;
  const pages = _AState.filter === 'large'
    ? _AState.pages.filter(p => p.is_large)
    : _AState.pages;
  grid.innerHTML = '';
  delete grid.__analysisEventsBound;
  pages.forEach(p => grid.appendChild(_buildPageCard(p)));
  _bindCardEvents(grid);
  _updateSummary();
}

/* ── Analyze ────────────────────────────────────────────────────────────── */
async function _runAnalyze(file) {
  if (_AState.inflight) return;
  _AState.inflight = true;
  _clearFeedback(); _resetProgress(); _setSpinner(true); _setProgress(5);

  _setBlockState('cz-summary',         'loading');
  _setBlockState('cz-controls',        'loading');
  _setBlockState('page-analysis-grid', 'loading');
  ['global-quality', 'global-dpi', 'filter-all', 'filter-large'].forEach(id => {
    const el = document.getElementById(id); if (el) el.disabled = true;
  });

  const fileMB   = file.size / 1048576;
  const estPages = Math.max(3, Math.round(fileMB * 10));
  const steps = [
    { text: 'Enviando arquivo…',                       status: 'current' },
    { text: 'Extraindo metadados…',                    status: '' },
    { text: `Gerando miniaturas (≈${estPages} pág.)…`, status: '' },
    { text: 'Pronto!',                                 status: '' },
  ];
  _setSteps(steps);

  let _tickPct = 15;
  const _ticker = setInterval(() => {
    if (_tickPct < 88) {
      _tickPct += _tickPct < 50 ? 3 : _tickPct < 75 ? 1.5 : 0.5;
      _setProgress(_tickPct);
    }
  }, 400);

  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    _setProgress(15);
    steps[0].status = 'done'; steps[1].status = 'current'; _setSteps(steps);

    const resp = await fetch('/api/compress/analyze', {
      method: 'POST',
      headers: { 'X-CSRFToken': readCSRFToken() },
      body: fd,
    });

    steps[1].status = 'done'; steps[2].status = 'current'; _setSteps(steps);

    if (!resp.ok) {
      let msg = `Erro ${resp.status}`;
      try { msg = (await resp.json()).error || msg; } catch (_) {}
      if (resp.status === 413) msg = 'Arquivo muito grande.';
      if (resp.status === 429) msg = 'Muitas requisições.';
      throw new Error(msg);
    }

    const analysis = await resp.json();
    clearInterval(_ticker);
    _setProgress(95);

    steps[2].status = 'done';
    steps[3].status = 'done';
    steps[3].text   = `✅ ${analysis.total_pages} páginas analisadas!`;
    _setSteps(steps);

    _AState.analyseId = analysis.analyse_id;
    _AState.pages     = analysis.pages.slice();
    _AState.filter    = 'all';

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('cz-filename', analysis.filename);
    set('cz-pages', `${analysis.total_pages} páginas · ${analysis.total_size_mb} MB` +
      (analysis.has_large_pages ? ' · ⚠ páginas grandes' : ''));

    const largeCnt = analysis.pages.filter(p => p.is_large).length;
    set('filter-all-count',   String(analysis.total_pages));
    set('filter-large-count', String(largeCnt));
    ['global-quality', 'global-dpi', 'filter-all', 'filter-large'].forEach(id => {
      const el = document.getElementById(id); if (el) el.disabled = false;
    });

    _setProgress(100);
    _renderPageGrid();

    _setBlockState('cz-summary',         'ready');
    _setBlockState('cz-controls',        'ready');
    _setBlockState('page-analysis-grid', 'ready');

  } catch (err) {
    clearInterval(_ticker);
    console.error('[compress] erro na análise:', err);
    _setFeedback('Erro ao analisar: ' + err.message, 'error');
    _setSteps([]);
    _setBlockState('cz-summary',         'empty');
    _setBlockState('cz-controls',        'empty');
    _setBlockState('page-analysis-grid', 'empty');
  } finally {
    clearInterval(_ticker);
    _AState.inflight = false;
    _setSpinner(false);
    setTimeout(() => { _resetProgress(); _setSteps([]); }, 2000);
  }
}

/* ── Upload ─────────────────────────────────────────────────────────────── */
function bindUploadOnce() {
  const input = document.getElementById('input-compress');
  if (!input || input.__czBound) return;
  input.__czBound = true;
  __GV_COMPRESS.inputBound = true;
  input.addEventListener('change', async () => {
    const file = input.files?.[0]; if (!file) return;
    _clearFeedback(); _resetProgress();
    await _runAnalyze(file);
  }, { passive: true });
}

/* ── Controles globais ──────────────────────────────────────────────────── */
function bindGlobalControls() {
  if (document.__gvGlobalCtrlBound) return;
  document.__gvGlobalCtrlBound = true;
  document.addEventListener('input', ev => {
    const el = ev.target;
    if (el.id === 'global-quality') {
      const v = parseInt(el.value, 10);
      const lbl = document.getElementById('global-quality-val');
      if (lbl) lbl.textContent = `${v}%`;
      _AState.pages.forEach(p => {
        if (p.keep_original) return;
        p.quality = v;
        const card  = document.querySelector(`#page-analysis-grid [data-page-number="${p.page_number}"]`);
        if (!card) return;
        const range = card.querySelector('[data-field="quality"]');
        const label = card.querySelector('.pac-quality-val');
        if (range) range.value       = v;
        if (label) label.textContent = `${v}%`;
      });
      _updateSummary();
    }
    if (el.id === 'global-dpi') {
      const v = parseInt(el.value, 10);
      const lbl = document.getElementById('global-dpi-val');
      if (lbl) lbl.textContent = String(v);
      _AState.pages.forEach(p => {
        if (p.keep_original) return;
        p.dpi = v;
        const card  = document.querySelector(`#page-analysis-grid [data-page-number="${p.page_number}"]`);
        if (!card) return;
        const range = card.querySelector('[data-field="dpi"]');
        const label = card.querySelector('.pac-dpi-val');
        if (range) range.value       = v;
        if (label) label.textContent = String(v);
      });
      _updateSummary();
    }
  }, { passive: true });
}

/* ── Filtros ────────────────────────────────────────────────────────────── */
function bindFilterButtons() {
  if (document.__gvFilterBound) return;
  document.__gvFilterBound = true;
  document.addEventListener('click', ev => {
    const btn = ev.target.closest('[data-filter]'); if (!btn) return;
    _AState.filter = btn.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active', b === btn));
    _renderPageGrid();
  });
}

/* ── Processar com configurações ────────────────────────────────────────── */
function bindProcessWithSettings() {
  if (document.__gvProcessSettingsBound) return;
  document.__gvProcessSettingsBound = true;

  document.addEventListener('click', async ev => {
    if (!ev.target.closest('#btn-process-with-settings')) return;
    if (_AState.inflight) return;
    if (!_AState.analyseId) { _setFeedback('Sessão perdida. Faça upload novamente.', 'error'); return; }
    const included = _AState.pages.filter(p => p.include);
    if (!included.length) { _setFeedback('Selecione ao menos uma página.', 'error'); return; }

    _AState.inflight = true;
    _clearFeedback(); _resetProgress(); _setProgress(5);
    const btnP = document.getElementById('btn-process-with-settings');
    if (btnP) btnP.disabled = true;

    const steps = [
      { text: `Preparando ${included.length} página(s)…`, status: 'current' },
      { text: 'Enviando configurações…',                   status: '' },
      { text: 'Comprimindo com Ghostscript…',              status: '' },
      { text: 'Montando PDF final…',                       status: '' },
      { text: '✅ Download pronto!',                        status: '' },
    ];
    _setSteps(steps);

    try {
      steps[0].status = 'done'; steps[1].status = 'current'; _setSteps(steps); _setProgress(20);

      const rotMap = {};
      _AState.pages.forEach(p => {
        const cardEl = document.querySelector(`#page-analysis-grid [data-page-number="${p.page_number}"]`);
        const imgEl  = cardEl?.querySelector('.pac-thumb img');
        const deg    = parseInt(imgEl?.dataset?.rotation || '0', 10) || 0;
        if (deg) rotMap[String(p.page_number)] = deg;
      });

      const payload = {
        analyse_id:    _AState.analyseId,
        page_settings: _AState.pages,
        rotations:     Object.keys(rotMap).length ? rotMap : undefined,
      };

      steps[1].status = 'done'; steps[2].status = 'current'; _setSteps(steps); _setProgress(35);

      const resp = await fetch('/api/compress/process-with-settings', {
        method:  'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken':  readCSRFToken(),
          'Accept':       'application/pdf',
        },
        body: JSON.stringify(payload),
      });

      steps[2].status = 'done'; steps[3].status = 'current'; _setSteps(steps); _setProgress(70);

      if (!resp.ok) {
        let msg = `Erro ${resp.status}`;
        try { msg = (await resp.json()).error || msg; } catch (_) {}
        if (resp.status === 404) msg = 'Sessão expirada. Faça upload novamente.';
        if (resp.status === 429) msg = 'Muitas requisições.';
        throw new Error(msg);
      }

      // Headers ANTES de resp.blob() — ordem obrigatória
      const sizeOrigKB  = parseFloat(resp.headers.get('X-Size-Original-KB') || '0');
      const sizeFinalKB = parseFloat(resp.headers.get('X-Size-Final-KB')    || '0');
      const redPct      = parseFloat(resp.headers.get('X-Reduction-Pct')    || '0');
      const fallback    = (resp.headers.get('X-Fallback') || 'none').trim();

      const blob = await resp.blob();
      steps[3].status = 'done'; steps[4].status = 'done'; _setSteps(steps); _setProgress(95);
      if (!blob?.size) throw new Error('Servidor retornou arquivo vazio.');

      // Se os headers X-* foram bloqueados pelo proxy (todos chegam como 0),
      // usa o tamanho real do blob como fallback para o campo "Resultado".
      const blobKB          = blob.size / 1024;
      const effectiveFinalKB  = sizeFinalKB  > 0 ? sizeFinalKB  : blobKB;
      // Tamanho original: usa header se disponível, senão usa soma das estimated_size_kb das páginas
      const estimatedOrigKB = _AState.pages.reduce(
        (s, p) => s + (p.include ? parseFloat(p.estimated_size_kb) : 0), 0
      );
      const effectiveOrigKB   = sizeOrigKB   > 0 ? sizeOrigKB   : estimatedOrigKB;
      const effectiveRedPct   = redPct       > 0 ? redPct
        : (effectiveOrigKB > 0 && effectiveFinalKB < effectiveOrigKB
            ? parseFloat(((1 - effectiveFinalKB / effectiveOrigKB) * 100).toFixed(1))
            : 0);

      const url = URL.createObjectURL(blob);
      const a   = document.createElement('a');
      a.href = url; a.download = 'comprimido.pdf'; a.style.display = 'none';
      document.body.appendChild(a); a.click();
      setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 10000);

      _setProgress(100);

      // ── Feedback textual ──────────────────────────────────────────────────
      let feedbackMsg  = '';
      let feedbackType = 'success';
      if (fallback === 'final_original') {
        feedbackMsg  = `✅ Download concluído — nenhuma redução obtida, arquivo original mantido`
                     + (effectiveOrigKB ? ` (${_fmtKB(effectiveOrigKB)})` : '') + `.`;
        feedbackType = 'info';
      } else if (fallback === 'partial') {
        feedbackMsg  = `✅ Comprimido parcialmente — algumas páginas mantidas no original.`
                     + (effectiveFinalKB ? ` Tamanho final: ${_fmtKB(effectiveFinalKB)}` : '');
        feedbackType = 'info';
      } else if (effectiveRedPct > 0 && effectiveFinalKB > 0) {
        feedbackMsg  = `✅ Comprimido com sucesso! `
                     + (effectiveOrigKB ? `${_fmtKB(effectiveOrigKB)} → ` : '')
                     + `${_fmtKB(effectiveFinalKB)} (−${effectiveRedPct}%)`;
        feedbackType = 'success';
      } else {
        feedbackMsg  = `✅ PDF (${_fmtKB(blobKB)}) baixado com sucesso!`;
        feedbackType = 'success';
      }
      _setFeedback(feedbackMsg, feedbackType);

      // ── Card superior → resultado REAL (ou melhor estimativa disponível) ─
      const origEl = document.getElementById('cs-original-val');
      if (origEl) origEl.textContent = _fmtKB(effectiveOrigKB);
      const adjLabelEl = document.getElementById('cs-adjusted-label');
      if (adjLabelEl) adjLabelEl.textContent = 'Resultado';
      const adjEl = document.getElementById('cs-adjusted-val');
      if (adjEl) adjEl.textContent = _fmtKB(effectiveFinalKB);
      const badge = document.getElementById('cs-badge');
      if (badge) {
        if (fallback === 'final_original') {
          badge.textContent = 'Sem ganho';
          badge.className   = 'cs-badge';
        } else if (fallback === 'partial') {
          badge.textContent = 'Parcial';
          badge.className   = 'cs-badge';
        } else if (effectiveRedPct > 0) {
          // Indica se o valor vem dos headers reais ou do cálculo de fallback
          const suffix = sizeFinalKB > 0 ? 'real' : 'aprox.';
          badge.textContent = `−${effectiveRedPct}% ${suffix}`;
          badge.className   = 'cs-badge cs-badge--good';
        }
      }

      // ── Auto-reset — usa _RESET_DELAY_MS (constante única, 9 s) ─────────
      // Cancela qualquer timer anterior antes de criar um novo,
      // evitando resets duplos se o utilizador processar rapidamente.
      clearTimeout(__GV_COMPRESS._resetTimer);
      __GV_COMPRESS._resetTimer = setTimeout(() => {
        _AState.analyseId = null; _AState.pages = []; _AState.filter = 'all';
        __GV_COMPRESS.inputBound = false;
        _resetAllBlocks(); _clearFeedback(); _resetProgress(); _setSteps([]);
        const inp = document.getElementById('input-compress'); if (inp) inp.value = '';
        bindUploadOnce();
      }, _RESET_DELAY_MS);

    } catch (err) {
      console.error('[compress] erro ao processar:', err);
      _setFeedback('Erro: ' + err.message, 'error');
      _setSteps([]);
    } finally {
      _AState.inflight = false;
      if (btnP) btnP.disabled = _AState.pages.filter(p => p.include).length === 0;
      setTimeout(_resetProgress, 3000);
    }
  });
}

/* ── Init ───────────────────────────────────────────────────────────────── */
function init() {
  bindClearButton();
  bindUploadOnce();
  bindGlobalControls();
  bindFilterButtons();
  bindProcessWithSettings();
}

document.addEventListener('DOMContentLoaded', () => {
  init();
  console.debug('[compress] DOMContentLoaded — init completo');
});

try { window.GV_COMPRESS_GET_STATE = () => ({ ..._AState }); } catch (_) {}
