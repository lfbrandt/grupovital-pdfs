// app/static/js/converter-page.js
// Reordenação por arrastar & soltar no preview da página "Converter"

(function () {
  const LIST_SELECTOR = '#preview-convert';
  const ITEM_SELECTOR = '.page-thumb';

  function ensurePageIds(list) {
    // Se o preview não atribuiu data-page-id, cria um sequencial
    [...list.querySelectorAll(ITEM_SELECTOR)].forEach((el, i) => {
      if (!el.dataset.pageId) el.dataset.pageId = String(i + 1);
    });
  }

  function markDraggable(list) {
    list.querySelectorAll(ITEM_SELECTOR).forEach(el => {
      if (!el.hasAttribute('draggable')) el.setAttribute('draggable', 'true');
      el.style.userSelect = 'none';
      el.style.cursor = 'grab';
    });
  }

  function saveOrder(list) {
    const order = [...list.querySelectorAll(ITEM_SELECTOR)].map(el => el.dataset.pageId || '');
    list.dataset.order = order.join(',');
    // dispara evento p/ quem quiser ouvir (ex.: botão "Exportar" usar essa ordem)
    list.dispatchEvent(new CustomEvent('orderchange', { detail: { order } }));
  }

  function bindDnd(list) {
    if (!list || list.__dndBound) return;
    list.__dndBound = true;

    let dragged = null;

    list.addEventListener('dragstart', e => {
      const item = e.target.closest(ITEM_SELECTOR);
      if (!item) return;
      dragged = item;
      // necessário em alguns navegadores para permitir drop
      try { e.dataTransfer.setData('text/plain', ''); } catch {}
      e.dataTransfer.effectAllowed = 'move';
      item.classList.add('is-dragging');
      item.setAttribute('aria-grabbed', 'true');
    });

    list.addEventListener('dragend', () => {
      if (!dragged) return;
      dragged.classList.remove('is-dragging');
      dragged.removeAttribute('aria-grabbed');
      saveOrder(list);
      dragged = null;
    });

    list.addEventListener('dragover', e => {
      if (!dragged) return;
      e.preventDefault(); // habilita drop
      const target = e.target.closest(ITEM_SELECTOR);
      if (!target || target === dragged) return;

      const rect = target.getBoundingClientRect();
      // Decide inserir antes/depois pela metade da altura
      const before = (e.clientY - rect.top) < rect.height / 2;
      list.insertBefore(dragged, before ? target : target.nextSibling);
    });

    list.addEventListener('drop', e => e.preventDefault());
  }

  function init() {
    const list = document.querySelector(LIST_SELECTOR);
    if (!list) return;

    // Evita overlays bloqueando clique/drag
    document.querySelectorAll('.drop-overlay, .drop-hint').forEach(el => {
      el.style.pointerEvents = 'none';
    });

    ensurePageIds(list);
    markDraggable(list);
    bindDnd(list);
    saveOrder(list); // salva ordem inicial
  }

  document.addEventListener('DOMContentLoaded', init);

  // Reaplica quando o preview é re-renderizado (ex.: após converter outro arquivo)
  const mo = new MutationObserver(() => init());
  mo.observe(document.documentElement, { childList: true, subtree: true });
})();