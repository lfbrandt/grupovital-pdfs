// Sincroniza a lista "Organizar pdf" com a ordem atual do grid de thumbs no /merge.
// - Observa #preview-merge (reordenações, inserções, remoções)
// - Lê data-source (A..Z) e tenta obter o nome do arquivo (data-file-name ou figcaption)
// - Recria as duas colunas (10 + 10) na mesma ordem do grid
// - Zero inline (CSP ok). Evita binds duplicados.

(function () {
  'use strict';

  const $  = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));

  const shell    = $("#merge-page");
  if (!shell || shell.dataset.mergeSyncInstalled === "1") return;
  shell.dataset.mergeSyncInstalled = "1";

  const preview  = $("#preview-merge");
  const listLeft = $("#file-list-left");
  const listRight= $("#file-list-right");

  if (!preview || !listLeft || !listRight) return;

  const MAX_PER_COL = 10;

  const debounce = (fn, ms = 80) => {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  };

  function scanGrid() {
    const thumbs = $$(".page-wrapper[data-source]", preview);
    const order  = [];
    const names  = {};
    const counts = {};

    for (const el of thumbs) {
      const src = el.dataset.source;
      if (!src) continue;

      counts[src] = (counts[src] || 0) + 1;

      if (!order.includes(src)) {
        order.push(src);

        // tenta achar o nome do arquivo
        let name = el.dataset.fileName || el.getAttribute("data-file-name");
        if (!name) {
          const cap = el.querySelector("figcaption");
          if (cap) name = cap.textContent.trim();
        }
        names[src] = (name && name.length ? name : src);
      }
    }

    return { order, names, counts };
  }

  function ensureItem(src, pool) {
    let li = pool.get(src);
    if (li) return li;

    li = document.createElement("li");
    li.className = "file-item";
    li.setAttribute("data-source", src);
    // Estrutura minimalista, usa suas classes utilitárias sem inline
    li.innerHTML = [
      `<span class="file-badge" aria-hidden="true">${src}</span>`,
      `<span class="file-name"></span>`,
      `<span class="file-pages"></span>`,
      `<button type="button" class="file-remove" data-action="remove" aria-label="Remover">×</button>`
    ].join("");

    return li;
  }

  function renderSidebar(payload) {
    if (!payload) return;
    const { order, names, counts } = payload;

    // pega todos os itens existentes numa “pool” pra reusar DOM
    const pool = new Map();
    [...listLeft.children, ...listRight.children].forEach(li => {
      const s = li.getAttribute("data-source") || li.dataset.source;
      if (s) pool.set(s, li);
    });

    // limpa as colunas e remonta na nova ordem
    listLeft.innerHTML = "";
    listRight.innerHTML = "";

    order.forEach((src, idx) => {
      const li = ensureItem(src, pool);

      const nameEl  = $(".file-name", li);
      const pagesEl = $(".file-pages", li);

      if (nameEl)  nameEl.textContent  = (names[src] || src);
      if (pagesEl) pagesEl.textContent = `${counts[src] || 0} pág.`;

      if (idx < MAX_PER_COL) listLeft.appendChild(li);
      else listRight.appendChild(li);
    });
  }

  const doSync = debounce(() => renderSidebar(scanGrid()), 60);

  // Observa mudanças estruturais no grid (adição/remoção/movimentação)
  const mo = new MutationObserver(doSync);
  mo.observe(preview, { childList: true });

  // Gatilhos comuns de DnD
  preview.addEventListener("drop", doSync, true);
  preview.addEventListener("dragend", doSync, true);

  // Se outro módulo emitir um sync explícito, respeitamos
  window.addEventListener("merge:sync", (e) => renderSidebar(e.detail));

  // Primeira sincronização
  doSync();
})();