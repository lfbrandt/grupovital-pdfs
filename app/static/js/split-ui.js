// UI do /split: controles (X/↻), seleção persistente e integração com /split-page.
// - Cobre .page-wrapper.page-thumb e legados (.thumb-card/.page-thumb/.page)
// - Move rotação antiga do .thumb-frame p/ a mídia (normalização)
// - Evita duplicatas de controles; delegação em #preview-split
// - Emite 'split:removePage' / 'split:rotatePage'
// - Persiste seleção (memória + sessionStorage) com chave configurável
// - API pública: init({persistKey}), getSelectedPages(), clear(), selectAll(), setPersistKey()

(function () {
  "use strict";
  if (window.__SplitUI_Inited) return; // evita init duplo
  window.__SplitUI_Inited = true;

  const gridSel  = '#preview-split';
  const itemSel  = '.page-wrapper.page-thumb, .thumb-card, .page-thumb, .page';
  const mediaSel = '.thumb-media, .page-media, canvas, img';

  const $grid  = () => document.querySelector(gridSel);
  const $cards = () => Array.from($grid()?.querySelectorAll(itemSel) || []);

  // ------------------ SelectionStore (mem + sessionStorage) ------------------
  const SelectionStore = (() => {
    let persistKey = 'gv_split_selection_default';
    let set = new Set();

    function load() {
      try {
        const raw = sessionStorage.getItem(persistKey);
        if (raw) set = new Set(JSON.parse(raw));
      } catch (_) {}
    }
    function save() {
      try { sessionStorage.setItem(persistKey, JSON.stringify(Array.from(set))); } catch (_) {}
    }
    function setKey(key) {
      if (key && key !== persistKey) { persistKey = key; load(); }
    }
    function add(id) { if (id == null) return; set.add(String(id)); save(); }
    function del(id) { if (id == null) return; set.delete(String(id)); save(); }
    function has(id) { return set.has(String(id)); }
    function clearAll() { set.clear(); save(); }
    function all() { return Array.from(set).map(x => /^\d+$/.test(x) ? Number(x) : x); }
    // bootstrap inicial
    load();
    return { setKey, add, del, has, clearAll, all };
  })();

  // chave de persistência pode ser definida no init
  function setPersistKey(key) { SelectionStore.setKey(key); }

  // ------------------ helpers ------------------
  function pageId(el) {
    if (!el) return null;
    return el.getAttribute('data-page-id') ??
           (el.dataset ? (el.dataset.pageId ?? el.dataset.pageid) : undefined) ??
           pageNum(el); // fallback para número visual
  }
  function pageNum(el) {
    if (!el) return null;
    const raw =
      el.getAttribute('data-page') ??
      el.getAttribute('data-page-id') ??
      (el.dataset ? (el.dataset.page ?? el.dataset.pageId ?? el.dataset.pageid) : undefined);
    const n = parseInt(raw, 10);
    if (Number.isFinite(n) && n > 0) return n;
    const list = $cards(); const i = list.indexOf(el);
    return i >= 0 ? i + 1 : null;
  }

  // ------------------ seleção (API antiga + store) ------------------
  function mark(el, yes) {
    el.classList.toggle('is-selected', yes);
    el.setAttribute('aria-selected', yes ? 'true' : 'false');
    if (el.dataset) el.dataset.selected = yes ? 'true' : 'false';
  }
  function getSelectedPages() {
    // Retorna IDs se existirem; senão, números visuais
    const ids = SelectionStore.all();
    if (ids.length) {
      // ordenar por posição visual atual
      const order = $cards().map(c => String(pageId(c)));
      return ids.slice().sort((a, b) => order.indexOf(String(a)) - order.indexOf(String(b)));
    }
    // fallback: ler do DOM (se nada em store)
    const out = [];
    for (const el of $cards()) {
      const sel = el.classList.contains('is-selected') ||
                  el.getAttribute('aria-selected') === 'true' ||
                  (el.dataset && el.dataset.selected === 'true');
      if (sel) {
        const p = pageNum(el);
        if (p) out.push(p);
      }
    }
    return out;
  }
  function clear() {
    SelectionStore.clearAll();
    $cards().forEach(c => mark(c, false));
  }
  function selectAll() {
    $cards().forEach(c => {
      const id = pageId(c);
      SelectionStore.add(id ?? pageNum(c));
      mark(c, true);
    });
  }

  // ------------------ rotação ------------------
  function getRotation(card) {
    const base  = parseInt(card.getAttribute('data-base-rotation') || '0', 10);
    const delta = parseInt(card.getAttribute('data-rotation')      || '0', 10);
    return ((base + delta) % 360 + 360) % 360;
  }
  function setRotation(card, angle) {
    const norm = ((angle % 360) + 360) % 360;
    card.setAttribute('data-rotation', String(norm));
    const media = card.querySelector(mediaSel);
    if (media) {
      const hasTranslate = /\btranslate\(/.test(media.style.transform);
      const base = hasTranslate
        ? media.style.transform.replace(/rotate\([^)]+\)/, '').trim()
        : 'translate(-50%, -50%)';
      media.style.transform = `${base} rotate(${norm}deg)`.trim();
      media.style.transformOrigin = '50% 50%';
      media.style.filter = (norm % 180 === 90) ? 'blur(0.001px)' : '';
      media.style.backfaceVisibility = 'hidden';
    }
  }
  function parseRotateDeg(transformStr) {
    if (!transformStr) return null;
    const m = /rotate\(\s*(-?\d+(?:\.\d+)?)deg\)/i.exec(transformStr);
    return m ? parseFloat(m[1]) : null;
  }
  function normalizeTransforms(card) {
    const frame = card.querySelector('.thumb-frame');
    const media = card.querySelector(mediaSel);
    if (!frame || !media) return;
    const frameDeg = parseRotateDeg(frame.getAttribute('style') || frame.style.transform || '');
    if (frameDeg !== null) {
      setRotation(card, frameDeg);
      const noRotate = (frame.style.transform || '').replace(/rotate\([^)]+\)/, '').trim();
      frame.style.transform = noRotate;
    }
    if (!/\btranslate\(/.test(media.style.transform)) {
      const deg = getRotation(card);
      media.style.transform = `translate(-50%, -50%)${Number.isFinite(deg) ? ` rotate(${deg}deg)` : ''}`.trim();
      media.style.transformOrigin = '50% 50%';
    }
  }

  // ------------------ controles + header PgN ------------------
  function ensureControls(card) {
    if (!card) return;
    if (!card.dataset.controlsMounted) {
      normalizeTransforms(card);

      // dedup blocos de controle
      const allControls = Array.from(card.querySelectorAll(':scope > .file-controls, :scope > .thumb-actions'));
      if (allControls.length > 1) for (let i = 1; i < allControls.length; i++) allControls[i].remove();
      let controls = allControls[0] || null;

      if (!controls) {
        controls = document.createElement('div');
        controls.className = 'file-controls';
        controls.innerHTML = `
          <button class="remove-file" type="button" title="Remover página" data-no-drag="true" aria-label="Remover página">×</button>
          <button class="rotate-page"  type="button" title="Girar 90°"       data-no-drag="true" aria-label="Girar 90°">↻</button>
        `;
        card.appendChild(controls);
      } else {
        if (!controls.querySelector('.remove-file,[data-action="remove"],[data-action="delete"],[data-action="close"]')) {
          const b = document.createElement('button');
          b.className = 'remove-file'; b.type = 'button'; b.title = 'Remover página';
          b.setAttribute('data-no-drag','true'); b.setAttribute('aria-label','Remover página'); b.textContent = '×';
          controls.prepend(b);
        }
        if (!controls.querySelector('.rotate-page,[data-action="rot-right"],[data-action="rotate-right"]')) {
          const b = document.createElement('button');
          b.className = 'rotate-page'; b.type = 'button'; b.title = 'Girar 90°';
          b.setAttribute('data-no-drag','true'); b.setAttribute('aria-label','Girar 90°'); b.textContent = '↻';
          controls.append(b);
        }
      }

      // Header “Pg N”
      if (!card.querySelector(':scope > .page-header, :scope > .page-badge')) {
        const header = document.createElement('div');
        header.className = 'page-header';
        const n = pageNum(card);
        header.textContent = n ? `Pg ${n}` : 'Pg';
        header.setAttribute('aria-hidden', 'true');
        card.prepend(header);
      }

      card.dataset.controlsMounted = '1';
    }

    // Reaplicar seleção persistida
    const id = pageId(card);
    const selected = SelectionStore.has(id ?? pageNum(card));
    mark(card, selected);
  }

  function mountAll() { $cards().forEach(ensureControls); }

  // ------------------ delegação ------------------
  function onClick(ev) {
    // ignore click em botões
    const buttonTarget = ev.target.closest(
      '.file-controls > .remove-file, .file-controls > .rotate-page,' +
      '.thumb-actions > .remove-file, .thumb-actions > .rotate-page,' +
      '.file-controls [data-action], .thumb-actions [data-action]'
    );
    const card = ev.target.closest(itemSel);

    if (buttonTarget) {
      if (!card) return;
      ev.preventDefault();
      ev.stopPropagation();

      const btn = buttonTarget;
      const isRemove = btn.classList.contains('remove-file') ||
                       btn.matches('[data-action="remove"],[data-action="delete"],[data-action="close"]');
      const isRotate = btn.classList.contains('rotate-page') ||
                       btn.matches('[data-action="rot-right"],[data-action="rotate-right"]');

      if (isRemove) {
        const e = new CustomEvent('split:removePage', {
          bubbles: true,
          detail: { pageId: card.dataset.pageId ?? card.getAttribute('data-page-id') ?? pageNum(card) }
        });
        card.dispatchEvent(e);
        // manter store em sincronia
        SelectionStore.del(pageId(card) ?? pageNum(card));
        if (!e.defaultPrevented) card.remove();
      }
      if (isRotate) {
        const next = (getRotation(card) + 90) % 360;
        setRotation(card, next);
        card.dispatchEvent(new CustomEvent('split:rotatePage', {
          bubbles: true,
          detail: { pageId: card.dataset.pageId ?? card.getAttribute('data-page-id') ?? pageNum(card), rotation: next }
        }));
      }
      return;
    }

    // Clique em qualquer área da card = toggle seleção
    if (card) {
      ev.preventDefault();
      const id = pageId(card) ?? pageNum(card);
      const willSelect = !(
        card.classList.contains('is-selected') ||
        card.getAttribute('aria-selected') === 'true' ||
        (card.dataset && card.dataset.selected === 'true')
      );
      mark(card, willSelect);
      if (willSelect) SelectionStore.add(id); else SelectionStore.del(id);
    }
  }

  function observeGrid(g) {
    const mo = new MutationObserver(muts => {
      for (const m of muts) {
        m.addedNodes.forEach(n => {
          if (n.nodeType !== 1) return;
          if (n.matches && n.matches(itemSel)) ensureControls(n);
          n.querySelectorAll && n.querySelectorAll(itemSel).forEach(ensureControls);
        });
      }
    });
    mo.observe(g, { childList: true, subtree: true });
    return mo;
  }

  // ------------------ bootstrap ------------------
  function init(opts = {}) {
    if (opts.persistKey) setPersistKey(opts.persistKey);
    const g = $grid();
    if (!g) return { destroy() {} };
    mountAll();
    g.addEventListener('click', onClick, true);
    const mo = observeGrid(g);
    // evento opcional para reidratar após renders externos
    g.addEventListener('split:reapplySelection', mountAll);
    return { destroy() { g.removeEventListener('click', onClick, true); mo.disconnect(); } };
  }

  // API pública
  window.SplitUI = { init, setPersistKey, getSelectedPages, clear, selectAll };
})();