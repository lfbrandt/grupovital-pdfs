// static/js/merge-page.js
// Habilita reordenação por DnD nas miniaturas da página "Juntar PDFs"

(function () {
  const LIST_SELECTOR = '.pages-grid';       // container das thumbs (tem no merge.html)
  const ITEM_SELECTOR = '.page-thumb';       // cada miniatura

  function markThumbsDraggable(list) {
    list.querySelectorAll(ITEM_SELECTOR).forEach(el => {
      if (!el.hasAttribute('draggable')) el.setAttribute('draggable', 'true');
      el.style.userSelect = 'none';
      el.style.cursor = 'grab';
    });
  }

  function bindDnd(list) {
    if (list.__dndBound) return; // evita duplicar listeners
    list.__dndBound = true;

    let dragged = null;

    list.addEventListener('dragstart', e => {
      const item = e.target.closest(ITEM_SELECTOR);
      if (!item) return;
      dragged = item;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', ''); // necessário em alguns browsers
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
      e.preventDefault(); // permite o drop
      const target = e.target.closest(ITEM_SELECTOR);
      if (!target || target === dragged) return;

      const rect = target.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      list.insertBefore(dragged, before ? target : target.nextSibling);
    });

    list.addEventListener('drop', e => e.preventDefault());
  }

  function saveOrder(list) {
    // Se cada bloco de arquivo tiver um hidden tipo #order-<fileId>, atualize:
    const box = list.closest('[data-file-id]');
    const fileId = box?.getAttribute('data-file-id') || '';
    const order = [...list.querySelectorAll(ITEM_SELECTOR)].map(el => el.dataset.pageId || '');
    const hidden = fileId ? document.querySelector(`#order-${fileId}`) : null;
    if (hidden) hidden.value = order.join(',');
    // Se você envia tudo via fetch depois, a ordem já está aqui.
    // console.debug('Nova ordem', fileId, order);
  }

  function initAllLists() {
    document.querySelectorAll(LIST_SELECTOR).forEach(list => {
      markThumbsDraggable(list);
      bindDnd(list);
    });
  }

  document.addEventListener('DOMContentLoaded', initAllLists);

  // Se o preview for re-renderizado dinamicamente, esse observer garante que o DnD continue ativo
  const mo = new MutationObserver(() => initAllLists());
  mo.observe(document.body, { childList: true, subtree: true });
})();