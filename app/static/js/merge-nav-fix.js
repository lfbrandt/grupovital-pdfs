// merge-nav-fix.js — só para a página /merge
(function () {
  'use strict';
  const shell  = document.getElementById('merge-page');
  const aside  = document.getElementById('sidebar');
  const grid   = document.getElementById('preview-merge') || document.querySelector('#preview-merge, #preview-merge-page, [id^="preview-merge"]');

  if (!shell || !aside) return;

  function sidebarVisible() {
    // visível se NÃO tem atributo hidden e não está display:none
    return !aside.hasAttribute('hidden') &&
           getComputedStyle(aside).display !== 'none' &&
           aside.offsetParent !== null; // evita casos de visibility/overflow
  }

  function syncFlag() {
    if (sidebarVisible()) {
      shell.setAttribute('data-sidebar', 'visible');
    } else {
      shell.removeAttribute('data-sidebar');
    }
  }

  // Observa mudanças na sidebar (hidden/style/class)
  const obsAside = new MutationObserver(syncFlag);
  obsAside.observe(aside, { attributes: true, attributeFilter: ['hidden', 'style', 'class'] });

  // Observa mudanças no grid (entra/saem thumbs → sidebar costuma abrir/fechar)
  if (grid) {
    const obsGrid = new MutationObserver(syncFlag);
    obsGrid.observe(grid, { childList: true });
  }

  // Eventos já emitidos pelo seu código
  document.addEventListener('merge:sync', syncFlag);
  document.addEventListener('merge:removeSource', syncFlag);

  // Ajusta em resize/orientation (altura útil muda)
  addEventListener('resize', syncFlag, { passive: true });
  addEventListener('orientationchange', syncFlag, { passive: true });

  // Primeira sincronização
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', syncFlag, { once: true });
  } else {
    syncFlag();
    // defesa: roda de novo no próximo tick
    setTimeout(syncFlag, 0);
  }
})();