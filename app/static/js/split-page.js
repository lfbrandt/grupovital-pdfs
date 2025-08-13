// app/static/js/split-page.js
// DnD para ordenar miniaturas na página "Dividir PDF"

(function () {
  const LIST_SELECTOR = '#preview-split';
  const ITEM_SELECTOR = '.page-thumb';

  function markDraggable(list) {
    list.querySelectorAll(ITEM_SELECTOR).forEach(el => {
      if (!el.hasAttribute('draggable')) el.setAttribute('draggable', 'true');
      el.style.userSelect = 'none';
      el.style.cursor = 'grab';
    });
  }

  function saveOrder(list) {
    // dica: muitas rotinas leem a DOM em ordem visual; então só mover no DOM já resolve.
    // se você quiser, armazene a ordem em data-order para consumo futuro:
    const order = [...list.querySelectorAll(ITEM_SELECTOR)].map(el => el.dataset.pageId || '');
    list.dataset.order = order.join(',');
  }

  function bindDnd(list) {
    if (!list || list.__dndBound) return;
    list.__dndBound = true;

    let dragged = null;

    list.addEventListener('dragstart', e => {
      const item = e.target.closest(ITEM_SELECTOR);
      if (!item) return;
      dragged = item;
      e.dataTransfer.effectAllowed = 'move';
      // necessário em alguns navegadores para habilitar drop
      try { e.dataTransfer.setData('text/plain', ''); } catch {}
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
      e.preventDefault();
      const target = e.target.closest(ITEM_SELECTOR);
      if (!target || target === dragged) return;

      const rect = target.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      list.insertBefore(dragged, before ? target : target.nextSibling);
    });

    list.addEventListener('drop', e => e.preventDefault());
  }

  function init() {
    const list = document.querySelector(LIST_SELECTOR);
    if (!list) return;
    // proteção contra overlays bloqueando clique
    document.querySelectorAll('.drop-overlay,.drop-hint').forEach(el => {
      el.style.pointerEvents = 'none';
    });
    markDraggable(list);
    bindDnd(list);
  }

  document.addEventListener('DOMContentLoaded', init);

  // Reanexa ao mudar o preview
  const mo = new MutationObserver(() => init());
  mo.observe(document.documentElement, { childList: true, subtree: true });
})();