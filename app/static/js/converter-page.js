// Bloqueio de metas indisponíveis na tela /convert/select (goal-grid)
// + (segue suportando DnD do preview se você usar esse arquivo também no /convert)

'use strict';

(function () {
  /* metas a bloquear */
  const DISABLED = new Set(['pdf-to-xlsx', 'pdf-to-csv', 'sheet-to-csv', 'sheet-to-xlsm']);

  function getGoal(el) {
    // 1) data-goal explícito
    const dg = el.getAttribute && el.getAttribute('data-goal');
    if (dg) return dg.trim();

    // 2) extrai de ?goal=... no href
    const a = el.matches('a[href]') ? el : el.querySelector('a[href]');
    if (!a) return '';
    try {
      const url = new URL(a.getAttribute('href'), window.location.origin);
      return (url.searchParams.get('goal') || '').trim();
    } catch { return ''; }
  }

  function disableCard(card) {
    if (!card) return;
    card.classList.add('is-disabled');
    card.setAttribute('aria-disabled', 'true');

    const a = card.matches('a[href]') ? card : card.querySelector('a[href]');
    if (a) {
      const href = a.getAttribute('href');
      if (href) { a.setAttribute('data-href', href); a.removeAttribute('href'); }
      a.setAttribute('aria-disabled', 'true');
      a.setAttribute('tabindex', '-1');
      a.addEventListener('click', (e) => e.preventDefault());
      a.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') e.preventDefault();
      });
    }
  }

  function initWizardBlock() {
    const grid = document.querySelector('.goal-grid');
    if (!grid) return;
    grid.querySelectorAll('.goal-card, a.goal-card').forEach((card) => {
      const g = getGoal(card);
      if (g && DISABLED.has(g)) disableCard(card);
    });
  }

  // ---- DnD do preview (opcional; só se #preview-convert existir) ----
  const LIST_SELECTOR = '#preview-convert';
  const ITEM_SELECTOR = '.page-thumb';

  function ensureIds(list) {
    list.querySelectorAll(ITEM_SELECTOR).forEach((el, i) => {
      if (!el.dataset.pageId) el.dataset.pageId = String(i + 1);
    });
  }
  function markGrabbable(list) {
    list.querySelectorAll(ITEM_SELECTOR).forEach(el => {
      if (!el.hasAttribute('draggable')) el.setAttribute('draggable', 'true');
      el.classList.add('is-grabbable');
    });
  }
  function saveOrder(list) {
    const order = [...list.querySelectorAll(ITEM_SELECTOR)].map(el => el.dataset.pageId || '');
    list.dataset.order = order.join(',');
    list.dispatchEvent(new CustomEvent('orderchange', { detail: { order } }));
  }
  function bindDnd(list) {
    if (!list || list.__dndBound) return; list.__dndBound = true;
    let dragged = null;
    list.addEventListener('dragstart', e => {
      const item = e.target.closest(ITEM_SELECTOR);
      if (!item) return;
      dragged = item;
      try { e.dataTransfer.setData('text/plain', ''); } catch {}
      e.dataTransfer.effectAllowed = 'move';
      item.classList.add('is-dragging');
      item.setAttribute('aria-grabbed', 'true');
    });
    list.addEventListener('dragend', () => {
      if (!dragged) return;
      dragged.classList.remove('is-dragging');
      dragged.removeAttribute('aria-grabbed');
      saveOrder(list); dragged = null;
    });
    list.addEventListener('dragover', e => {
      if (!dragged) return; e.preventDefault();
      const t = e.target.closest(ITEM_SELECTOR);
      if (!t || t === dragged) return;
      const r = t.getBoundingClientRect();
      const before = (e.clientY - r.top) < r.height / 2;
      list.insertBefore(dragged, before ? t : t.nextSibling);
    });
    list.addEventListener('drop', e => e.preventDefault());
  }
  function initPreviewDnd() {
    const list = document.querySelector(LIST_SELECTOR);
    if (!list) return;
    document.querySelectorAll('.drop-overlay, .drop-hint').forEach(el => { el.style.pointerEvents = 'none'; });
    ensureIds(list); markGrabbable(list); bindDnd(list); saveOrder(list);
  }

  function init() { initWizardBlock(); initPreviewDnd(); }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  const mo = new MutationObserver(() => initPreviewDnd());
  mo.observe(document.documentElement, { childList: true, subtree: true });
})();