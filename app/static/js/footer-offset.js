// Ajusta --footer-h com a altura REAL do footer e aplica uma reserva lateral
// (--footer-fab-reserve) suficiente para um FAB/widget no canto direito.
// O tamanho alvo pode ser passado via data-fab-safe no <body>.

(function () {
  const root   = document.documentElement;
  const body   = document.body;
  const footer = document.querySelector('.site-footer');
  const inner  = footer ? footer.querySelector('.footer-inner') : null;

  const SAFE_DEFAULT = Number(body.getAttribute('data-fab-safe')) || 240; // px

  function isFixedFooter(el) {
    if (!el) return false;
    return !el.classList.contains('footer--static') && !el.classList.contains('footer--corner');
  }

  function applyFooterVars() {
    // Altura real -> --footer-h
    const h = (footer && isFixedFooter(footer)) ? Math.ceil(footer.getBoundingClientRect().height) : 0;
    root.style.setProperty('--footer-h', h + 'px');

    // Reserva lateral: mantém no mínimo SAFE_DEFAULT px livres no canto direito
    if (inner) {
      // largura útil do viewport - borda direita do conteúdo do footer
      const vw = Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0);
      const rect = inner.getBoundingClientRect();
      const rightGutter = Math.max(vw - rect.right, 0);
      const needed = Math.max(SAFE_DEFAULT - rightGutter, 0);

      const reserve = Math.max(24, Math.min(260, rightGutter + needed));
      root.style.setProperty('--footer-fab-reserve', reserve + 'px');
    }
  }

  // Observa alterações de tamanho/layout
  try {
    const ro = new ResizeObserver(applyFooterVars);
    footer && ro.observe(footer);
    inner && ro.observe(inner);
  } catch (_) { /* no-op */ }

  // Fallbacks e eventos úteis
  ['load', 'resize', 'orientationchange'].forEach(ev =>
    window.addEventListener(ev, applyFooterVars, { passive: true })
  );

  // Caso a fonte web mude a metragem do footer
  if (document.fonts && document.fonts.addEventListener) {
    document.fonts.addEventListener('loadingdone', applyFooterVars);
  }

  applyFooterVars();
})();