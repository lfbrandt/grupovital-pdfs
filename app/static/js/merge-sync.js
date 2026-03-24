/* ==========================================================================
   merge-sync.js — bridge de eventos para o /merge

   Responsabilidade única: escutar merge:sync em document e retransmitir
   se necessário. NÃO renderiza sidebar, NÃO observa mutações no DOM.

   Stage 1: removido MutationObserver que conflitava com merge-sidebar.js.
            Corrigido target de window → document (custom events despachados
            em document não propagam para window).
   ========================================================================== */
(function () {
  'use strict';

  function init() {
    if (!document.getElementById('preview-merge')) return;

    // merge:sync escutado em document — mesmo canal de merge-page.js
    document.addEventListener('merge:sync', function () {
      // bridge: lógica de sync que não toque DOM da sidebar pode ser adicionada aqui
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init, { once: true });
  } else {
    init();
  }

}());