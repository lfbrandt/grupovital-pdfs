// app/static/js/merge-compact.js — v3.2
// Modo Compacto com botão por miniatura (⊕/⊖) ao lado do X/↻.
// - Garante APENAS 1 botão por card (remove duplicados).
// - Ícone vem do SCSS via ::before (sem textContent, evita “duplo”).
(function () {
  'use strict';
  const $  = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));

  const grid   = $('#preview-merge');
  const btnG   = $('#btn-toggle-compact');
  const hintEl = $('#compact-hint');
  if (!grid || !btnG) return;

  const compact = { on: true, expanded: new Set() };

  /* -------- helpers -------- */
  function groupBySource() {
    const map = new Map();
    $$('.page-wrapper[data-source]', grid).forEach(el => {
      const src = el.dataset.source || '?';
      if (!map.has(src)) map.set(src, []);
      map.get(src).push(el);
    });
    map.forEach(list => list.sort((a,b)=>(+a.dataset.page||0) - (+b.dataset.page||0)));
    return map;
  }

  function ensureToggleBtn(card, src) {
    let controls = card.querySelector('.file-controls');
    if (!controls) {
      controls = document.createElement('div');
      controls.className = 'file-controls';
      controls.setAttribute('data-no-drag','');
      card.appendChild(controls);
    }

    // pega apenas botões diretos; se houver mais de um, remove extras
    let btn = controls.querySelector(':scope > button.compact-toggle');
    if (!btn) {
      btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'compact-toggle';
      btn.setAttribute('data-no-drag','');
      btn.setAttribute('aria-label', 'Expandir/colapsar este PDF');
      btn.setAttribute('aria-pressed', 'false');
      // NÃO definir textContent (ícone vem do ::before no SCSS)
      controls.insertBefore(btn, controls.firstChild);
    }
    controls.querySelectorAll(':scope > button.compact-toggle').forEach((b, i) => {
      if (i > 0) b.remove();
    });

    btn.dataset.source = src;
    return btn;
  }

  function updateGlobalUI(groups) {
    const has = groups.size > 0;
    btnG.disabled = !has;
    btnG.setAttribute('aria-pressed', compact.on ? 'true' : 'false');
    btnG.textContent = compact.on ? 'Modo compacto: Ligado' : 'Modo compacto: Desligado';
    if (!hintEl) return;
    if (!has) {
      hintEl.textContent = 'Envie 2+ PDFs para habilitar o modo compacto.';
    } else if (compact.on) {
      hintEl.innerHTML = 'Mostrando apenas a <strong>1ª página</strong> de cada PDF. Use o botão no topo da miniatura (⊕/⊖) para abrir/fechar aquele PDF.';
    } else {
      hintEl.textContent = 'Modo compacto desativado — todas as páginas estão visíveis.';
    }
  }

  /* -------- aplicar estado visual -------- */
  function applyCompactUI() {
    const groups = groupBySource();

    groups.forEach((list, src) => {
      const isExpanded = compact.expanded.has(src);

      list.forEach((card, idx) => {
        const hide = compact.on && !isExpanded && idx > 0;

        if (hide) {
          if (!card.hasAttribute('hidden')) card.setAttribute('hidden','');
          card.setAttribute('aria-hidden','true');
          card.tabIndex = -1;
          card.setAttribute('draggable','false');
        } else {
          if (card.hasAttribute('hidden')) card.removeAttribute('hidden');
          card.setAttribute('aria-hidden','false');
          card.tabIndex = 0;
          card.setAttribute('draggable','true');
        }

        const btn = ensureToggleBtn(card, src);
        const showBtn = compact.on && (isExpanded || idx === 0);

        if (showBtn) {
          btn.removeAttribute('hidden');
          btn.setAttribute('aria-pressed', isExpanded ? 'true' : 'false');
          btn.title = isExpanded ? 'Colapsar PDF' : 'Expandir PDF';
          // sem textContent — evita símbolo duplicado
        } else {
          btn.setAttribute('hidden','');
        }
      });
    });

    grid.classList.toggle('is-compact', !!compact.on);
    updateGlobalUI(groups);
  }

  let raf = 0;
  function scheduleApply() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; applyCompactUI(); });
  }

  function toggleCompact(force) {
    compact.on = (typeof force === 'boolean') ? force : !compact.on;
    if (!compact.on) compact.expanded.clear();
    scheduleApply();
  }
  function toggleSource(src) {
    if (!src || !compact.on) return;
    compact.expanded.has(src) ? compact.expanded.delete(src) : compact.expanded.add(src);
    scheduleApply();
  }

  /* -------- eventos -------- */
  btnG.addEventListener('click', () => toggleCompact());

  grid.addEventListener('click', (e) => {
    const t = e.target.closest('button.compact-toggle');
    if (t) { e.preventDefault(); e.stopPropagation(); toggleSource(t.dataset.source); }
  });

  // atalho: clicar no selo A/B
  grid.addEventListener('click', (e) => {
    const badge = e.target.closest('.source-badge');
    if (!badge) return;
    const wrap = badge.closest('.page-wrapper');
    const src  = wrap?.dataset?.source;
    if (src) { e.preventDefault(); e.stopPropagation(); toggleSource(src); }
  });

  // Observa mudanças no subtree do grid
  const mo = new MutationObserver(() => scheduleApply());
  mo.observe(grid, { childList: true, subtree: true });

  // Gatilhos extras
  grid.addEventListener('merge:sync', scheduleApply);
  grid.addEventListener('merge:render', scheduleApply);
  document.addEventListener('merge:sync', scheduleApply);

  const btnClear = $('#btn-clear-all');
  if (btnClear) btnClear.addEventListener('click', () => {
    compact.expanded.clear();
    scheduleApply();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scheduleApply, { once: true });
  } else {
    scheduleApply();
  }
})();