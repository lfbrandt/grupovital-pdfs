/* ============================================================================
   SES Guard
   - Silencia ruídos do lockdown/SES sem mascarar erros reais
   - Resolve GRID de preview por seletor explícito (quando exposto pela página)
   - Fallback por índice apenas para retrocompat, sem spam de warnings
   - Warnings desativados por padrão; ative com window.__GV_DEBUG_GRID = true
   ============================================================================ */
(function () {
  'use strict';

  /* ====================== 1) Filtro de ruídos do SES ====================== */
  const orig = {
    error: console.error.bind(console),
    warn:  console.warn.bind(console),
    log:   console.log.bind(console),
  };

  function isSesNoise(args) {
    try {
      const [first] = args || [];
      const s = String(first || '');
      if (s.startsWith('SES_UNCAUGHT_EXCEPTION')) return true;
      if (s.includes('Removing unpermitted intrinsics')) return true;
      return false;
    } catch (_) { return false; }
  }

  console.error = function (...args) { if (isSesNoise(args)) return; return orig.error(...args); };
  console.warn  = function (...args) { if (isSesNoise(args)) return; return orig.warn(...args);  };
  console.log   = function (...args) { if (isSesNoise(args)) return; return orig.log(...args);   };

  window.addEventListener('error', (e) => {
    if (e && (e.error === null || e.error === undefined)) e.preventDefault();
  }, true);
  window.addEventListener('unhandledrejection', (e) => {
    if (e && (e.reason === null || e.reason === undefined)) e.preventDefault();
  }, true);

  /* ====================== 2) Utilitários de GRID ====================== */

  function getExplicitSelector(prefix) {
    try {
      const key = `__GV_${String(prefix).toUpperCase()}_GRID_SELECTOR`;
      const sel = window[key];
      return (typeof sel === 'string' && sel.trim()) ? sel.trim() : null;
    } catch (_) { return null; }
  }

  function resolveGrid(prefix, index) {
    // 1) Preferir seletor explícito publicado pela página (ex.: '#preview-compress')
    const explicit = getExplicitSelector(prefix);
    if (explicit) {
      const el = document.querySelector(explicit);
      if (el) return el;
    }
    // 2) Fallback: heurística antiga por índice (retrocompat)
    const grids = document.querySelectorAll('.preview-grid, .thumb-grid, [data-grid="preview"]');
    return grids[index] || null;
  }

  const __warnOnce = new Set();
  function warnOnce(prefix, index, msg) {
    if (!window.__GV_DEBUG_GRID) return; // warnings desligados por padrão
    const key = `${prefix}:${index}:${msg}`;
    if (__warnOnce.has(key)) return;
    __warnOnce.add(key);
    orig.warn(msg);
  }

  function getPreviewGrid(prefix, index = 0, { quiet = true } = {}) {
    const grid = resolveGrid(prefix, index);
    if (!grid && !quiet) {
      warnOnce(prefix, index, `[${prefix}] grid não encontrado (seletor explícito ausente e index ${index} sem match)`);
    }
    return grid;
  }

  function initByIndex(prefix, index = 0, initFn) {
    const grid = getPreviewGrid(prefix, index, { quiet: true });
    if (!grid) return;
    try { typeof initFn === 'function' && initFn(grid); } catch (_) {}
  }

  try { window.__SES_GUARD__ = { getPreviewGrid, initByIndex }; } catch(_) {}
})();