// app/static/js/split-ui.js
(function(){
  "use strict";
  const gridSel = '#preview-split';
  const itemSel = '.thumb-card';

  function cards(){
    const g = document.querySelector(gridSel);
    return Array.from(g?.querySelectorAll(itemSel) || []);
  }
  function pageNum(el){
    const ds = el?.dataset || {};
    const raw = ds.page ?? ds.pageId ?? ds.pageid ?? el.getAttribute?.('data-page');
    const n = parseInt(raw, 10);
    if (Number.isFinite(n) && n > 0) return n;
    const list = cards();
    const i = list.indexOf(el);
    return i >= 0 ? i + 1 : null;
  }

  function getSelectedPages(){
    const list = cards();
    const out = [];
    for (const el of list){
      if (el.classList.contains('is-selected') ||
          el.getAttribute('aria-selected') === 'true' ||
          el.dataset.selected === 'true'){
        const p = pageNum(el);
        if (p) out.push(p);
      }
    }
    return out;
  }
  function clear(){
    cards().forEach(c => {
      c.classList.remove('is-selected');
      c.setAttribute('aria-selected', 'false');
      c.dataset.selected = 'false';
    });
  }
  function selectAll(){
    cards().forEach(c => {
      c.classList.add('is-selected');
      c.setAttribute('aria-selected', 'true');
      c.dataset.selected = 'true';
    });
  }

  window.SplitUI = { getSelectedPages, clear, selectAll };
})();