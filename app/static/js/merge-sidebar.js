// Sidebar de organização do /merge (2 colunas, 10 itens/col, SWAP 1↔1, remover, reset)
// Compact mode + altura adaptativa (A+B). Ellipsis inteligente e expansão no hover.
(function () {
  'use strict';

  const $  = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));

  const preview   = $("#preview-merge");
  const listLeft  = $("#file-list-left");
  const listRight = $("#file-list-right");
  const btnReset  = $("#btn-reset-files");
  const btnApply  = $("#btn-organize-apply");
  const hintEl    = document.querySelector("#file-list-hint, .file-list-hint");
  const sidebar   = $("#merge-page #sidebar.tool__sidebar");

  if (!preview || !listLeft || !listRight || !sidebar) return;

  const MAX_PER_COL = 10;
  let initialOrder = [];
  let currentOrder = [];
  let bySource = new Map();

  const unique = (arr) => Array.from(new Set(arr || []));

  // ========= Nome do arquivo (descoberta + formatação) ===================
  function deriveNameFromCard(card) {
    if (!card) return "";
    const ds =
      card.dataset.fileName || card.dataset.filename ||
      card.getAttribute("data-file-name") || card.getAttribute("data-filename");
    if (ds && String(ds).trim()) return String(ds).trim();

    const badgeTitle = card.querySelector('.source-badge')?.getAttribute('title');
    if (badgeTitle && badgeTitle.trim()) return badgeTitle.trim();

    const cap = card.querySelector('.thumb-caption')?.textContent;
    if (cap && cap.trim()) return cap.trim();

    const alt = card.querySelector('.thumb-media img')?.getAttribute('alt');
    if (alt && alt.trim()) return alt.trim();

    return "";
  }

  function groupDisplayName(src) {
    const firstEl = (bySource.get(src) || [])[0];
    const guess = deriveNameFromCard(firstEl);
    return guess || `Arquivo ${src}`;
  }

  // Ellipsis no MEIO preservando extensão
  function smartEllipsize(name, max = 44) {
    const n = String(name || "");
    if (n.length <= max) return n;

    const dot = n.lastIndexOf(".");
    const ext = dot > 0 && dot < n.length - 1 ? n.slice(dot) : "";
    const core = ext ? n.slice(0, dot) : n;

    const room = max - ext.length - 1; // 1 é o "…"
    if (room <= 0) return n.slice(0, max - 1) + "…";

    const head = Math.ceil(room * 0.6);
    const tail = room - head;
    return core.slice(0, head).trimEnd() + "…" + core.slice(-tail).trimStart() + ext;
  }

  function sanitizeText(t) {
    try { return String(t || '').replace(/[<>&]/g, c => ({'<':'&lt;','>':'&gt;','&':'&amp;'}[c])); }
    catch { return String(t || ''); }
  }

  // ================== Leitura do grid e render da lista ===================
  function scan() {
    const thumbs = $$(".page-wrapper[data-source], .page-wrapper[data-file], .page-wrapper", preview);
    const order = [];
    const map = new Map();
    for (const el of thumbs) {
      const src = el.dataset.source || el.dataset.file || el.getAttribute("data-group");
      if (!src) continue;
      if (!map.has(src)) { map.set(src, []); order.push(src); }
      map.get(src).push(el);
    }
    return { order: unique(order), map };
  }

  function renderList(order, map) {
    const ord = unique(order);
    bySource = map;

    listLeft.innerHTML = "";
    listRight.innerHTML = "";

    const left  = ord.slice(0, MAX_PER_COL);
    const right = ord.slice(MAX_PER_COL, MAX_PER_COL * 2);
    const overflow = ord.slice(MAX_PER_COL * 2);

    left.forEach(s  => listLeft.appendChild(makeItem(s, map)));
    right.forEach(s => listRight.appendChild(makeItem(s, map)));

    if (hintEl) {
      hintEl.innerHTML = overflow.length > 0
        ? `Exibindo <strong>${left.length + right.length}</strong> de <strong>${ord.length}</strong>. Máx. <strong>10</strong> por coluna.`
        : `Máx. <strong>10</strong> por coluna (até <strong>20</strong> visíveis).`;
    }

    bindDnD();
    fitRows(); // <<< ajusta linhas visíveis após render
  }

  function makeItem(s, map) {
    const groupEls = map.get(s) || [];
    const count = groupEls.length;

    const fullName = groupDisplayName(s);
    const visName  = smartEllipsize(fullName, 44);

    const li = document.createElement("li");
    li.className = "file-item";
    li.setAttribute("role", "option");
    li.setAttribute("draggable", "true");
    li.dataset.source = s;

    li.innerHTML = `
      <span class="handle" aria-hidden="true">⋮⋮</span>
      <span class="tag">${s}</span>
      <span class="name" title="${sanitizeText(fullName)}" data-full="${sanitizeText(fullName)}">
        ${sanitizeText(visName)}
      </span>
      <span class="meta">${count} pág.</span>
    `;

    const btnX = document.createElement("button");
    btnX.type = "button";
    btnX.className = "remove-file";
    btnX.title = `Remover arquivo ${s}`;
    btnX.setAttribute("aria-label", `Remover arquivo ${s}`);
    btnX.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      removeSource(s);
    });
    li.appendChild(btnX);

    return li;
  }

  function applyOrder(order) {
    const ord = unique(order);
    const frag = document.createDocumentFragment();
    for (const s of ord) {
      const group = bySource.get(s) || [];
      for (const el of group) frag.appendChild(el);
    }
    preview.appendChild(frag);
    document.dispatchEvent(new CustomEvent("merge:sync"));
  }

  // ============================ DnD =======================================
  function bindDnD() {
    let dragItem = null;

    function onDragStart(e) {
      const li = e.currentTarget;
      if (!(li instanceof HTMLElement)) return;
      if (e.target && e.target.closest(".remove-file")) { e.preventDefault(); return; }
      dragItem = li;
      dragItem.setAttribute("aria-grabbed", "true");
      dragItem.classList.add("is-dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", dragItem.dataset.source || "");
    }

    function onDragOver(e) {
      if (!dragItem) return;
      const target = e.currentTarget;
      if (!target || target === dragItem) return;
      e.preventDefault();
      $$(".drop-highlight", dragItem.parentElement || document).forEach(el => el.classList.remove("drop-highlight"));
      target.classList.add("drop-highlight");
    }

    function onDrop(e) {
      const target = e.currentTarget;
      [listLeft, listRight].forEach(ul => $$(".drop-highlight", ul).forEach(el => el.classList.remove("drop-highlight")));
      if (!dragItem || !target || target === dragItem) return;
      e.preventDefault();

      if (dragItem.parentNode !== target.parentNode) {
        target.parentNode.insertBefore(dragItem, target);
      } else {
        const parent = target.parentNode;
        const marker = document.createComment("swap");
        parent.replaceChild(marker, dragItem);
        parent.replaceChild(dragItem, target);
        parent.replaceChild(target, marker);
      }

      dragItem.removeAttribute("aria-grabbed");
      dragItem.classList.remove("is-dragging");
      dragItem = null;

      const leftOrder  = Array.from(listLeft.children).map(li => li.dataset.source);
      const rightOrder = Array.from(listRight.children).map(li => li.dataset.source);
      const head = unique([...leftOrder, ...rightOrder]);
      const tail = currentOrder.filter(s => !head.includes(s));
      currentOrder = unique([...head, ...tail]);

      applyOrder(currentOrder);
      renderList(currentOrder, bySource);
    }

    function onDragEnd(e) {
      e.currentTarget.removeAttribute("aria-grabbed");
      e.currentTarget.classList.remove("is-dragging");
      [listLeft, listRight].forEach(ul => $$(".drop-highlight", ul).forEach(el => el.classList.remove("drop-highlight")));
    }

    [listLeft, listRight].forEach(ul => {
      $$(".file-item", ul).forEach(li => {
        li.removeEventListener("dragstart", onDragStart);
        li.removeEventListener("dragover", onDragOver);
        li.removeEventListener("drop", onDrop);
        li.removeEventListener("dragend", onDragEnd);

        li.addEventListener("dragstart", onDragStart);
        li.addEventListener("dragover", onDragOver);
        li.addEventListener("drop", onDrop);
        li.addEventListener("dragend", onDragEnd);
      });
    });
  }

  // ===================== Remover e bootstrap =============================
  function removeSource(letter) {
    if (!letter) return;
    const grp = bySource.get(letter) || [];
    grp.forEach(card => card.querySelector(".remove-file")?.click());
    bySource.delete(letter);
    currentOrder = unique(currentOrder.filter(s => s !== letter));
    initialOrder = unique(initialOrder.filter(s => s !== letter));
    renderList(currentOrder, bySource);
    document.dispatchEvent(new CustomEvent("merge:removeSource", { detail: { source: letter }}));
    document.dispatchEvent(new CustomEvent("merge:sync"));
  }

  [listLeft, listRight].forEach(ul => {
    ul.addEventListener("click", (e) => {
      const btn = e.target.closest(".remove-file");
      if (!btn) return;
      const li = btn.closest(".file-item");
      if (!li) return;
      removeSource(li.dataset.source);
    });
  });

  // ===================== Altura adaptativa (A+B) ==========================
  function fitRows() {
    try {
      const anyList = sidebar.querySelector('.file-list--col');
      const styles  = anyList ? getComputedStyle(anyList) : null;

      const rowH  = styles ? parseFloat(styles.getPropertyValue('--file-row-h'))  : 56;
      const rowG  = styles ? parseFloat(styles.getPropertyValue('--file-row-gap')): 4;
      const ROW   = (isNaN(rowH) ? 56 : rowH) + (isNaN(rowG) ? 4 : rowG);

      const headH = sidebar.querySelector('.tool__sidebar__header')?.offsetHeight || 48;
      const footH = sidebar.querySelector('.tool__sidebar__footer')?.offsetHeight || 56;

      const root = getComputedStyle(document.documentElement);
      const siteFooter = parseInt(root.getPropertyValue('--footer-h')) || 56;

      // altura útil do viewport até o topo do aside, menos o footer global
      const top = sidebar.getBoundingClientRect().top;
      const usable = window.innerHeight - top - siteFooter - 8;

      const rows = Math.max(6, Math.floor((usable - headH - footH - 16) / ROW));
      sidebar.style.setProperty('--rows-visible', String(rows));
    } catch { /* no-op */ }
  }

  function bootstrap() {
    const { order, map } = scan();
    if (!order.length) return;
    bySource     = map;
    initialOrder = unique(order);
    currentOrder = unique(order);
    renderList(currentOrder, bySource);
    fitRows();
  }

  const mo = new MutationObserver(() => {
    const { order, map } = scan();
    if (!order.length) { listLeft.innerHTML = ""; listRight.innerHTML = ""; fitRows(); return; }

    const prev      = unique(currentOrder);
    const known     = prev.filter(s => order.includes(s));
    const newcomers = order.filter(s => !known.includes(s));
    currentOrder    = known.length ? unique([...known, ...newcomers]) : unique(order);

    bySource = map;
    renderList(currentOrder, bySource);
    fitRows();
  });
  mo.observe(preview, { childList: true });

  if (btnReset) {
    btnReset.addEventListener("click", (e) => {
      e.preventDefault();
      const base = initialOrder?.length ? initialOrder.slice()
                                        : Array.from(bySource.keys()).sort();
      currentOrder = unique(base);
      renderList(currentOrder, bySource);
      applyOrder(currentOrder);
      fitRows();
    });
  }

  if (btnApply) {
    btnApply.addEventListener("click", () => {
      currentOrder = unique(currentOrder);
      applyOrder(currentOrder);
      const mergeBtn = document.getElementById("btn-merge");
      if (mergeBtn) mergeBtn.click();
    });
  }

  // Recalcula em mudanças de viewport
  addEventListener('resize', fitRows, { passive: true });
  addEventListener('orientationchange', fitRows, { passive: true });

  bootstrap();
})();