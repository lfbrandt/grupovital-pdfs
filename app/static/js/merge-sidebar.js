// Sidebar de organização do /merge (SWAP 1↔1 + remover arquivo + reset confiável)
(function () {
  const $  = (s, c = document) => c.querySelector(s);
  const $$ = (s, c = document) => Array.from(c.querySelectorAll(s));

  const preview  = $("#preview-merge");
  const fileList = $("#file-list");
  const btnReset = $("#btn-reset-files");
  const btnApply = $("#btn-organize-apply");

  if (!preview || !fileList) return;

  let initialOrder = [];
  let currentOrder = [];
  let bySource = new Map();

  const unique = (arr) => Array.from(new Set(arr || []));

  function scan() {
    // Agrupa thumbs por data-source (A, B, C, …) na ordem atual do grid
    const thumbs = $$(".page-wrapper[data-source], .page-wrapper[data-file], .page-wrapper", preview);
    const order = [];
    const map = new Map();

    for (const el of thumbs) {
      const src = el.dataset.source || el.dataset.file || el.getAttribute("data-group");
      if (!src) continue;
      if (!map.has(src)) {
        map.set(src, []);
        order.push(src);
      }
      map.get(src).push(el);
    }
    return { order: unique(order), map };
  }

  function renderList(order, map) {
    const ord = unique(order);
    fileList.innerHTML = "";
    for (const s of ord) {
      const count = (map.get(s) || []).length;

      const li = document.createElement("li");
      li.className = "file-item";
      li.setAttribute("role", "option");
      li.setAttribute("draggable", "true");
      li.dataset.source = s;

      li.innerHTML = `
        <span class="handle" aria-hidden="true">⋮⋮</span>
        <span class="tag">${s}</span>
        <span class="name">Arquivo ${s}</span>
        <span class="meta">${count} pág.</span>
      `;

      // Botão X com a MESMA classe das thumbs (reaproveita o ícone/estilo)
      const btnX = document.createElement("button");
      btnX.type = "button";
      btnX.className = "remove-file";
      btnX.title = `Remover arquivo ${s}`;
      btnX.setAttribute("aria-label", `Remover arquivo ${s}`);
      li.appendChild(btnX);

      // Remoção do grupo (dispara o estado do merge-page via click de cada X de página)
      btnX.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        removeSource(s);
      });

      fileList.appendChild(li);
    }
    bindDnD();
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

  // Troca elementos irmãos no mesmo UL
  function swapSiblings(a, b) {
    if (!a || !b || !a.parentNode || a.parentNode !== b.parentNode) return;
    const parent = a.parentNode;
    const marker = document.createComment("swap");
    parent.replaceChild(marker, a);
    parent.replaceChild(a, b);
    parent.replaceChild(b, marker);
  }

  function bindDnD() {
    let dragItem = null;

    function onDragStart(e) {
      const li = e.currentTarget;
      if (!(li instanceof HTMLElement)) return;
      // Evita iniciar drag clicando no X
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
      $$(".drop-highlight", fileList).forEach(el => el.classList.remove("drop-highlight"));
      target.classList.add("drop-highlight");
    }

    function onDrop(e) {
      const target = e.currentTarget;
      $$(".drop-highlight", fileList).forEach(el => el.classList.remove("drop-highlight"));
      if (!dragItem || !target || dragItem === target) return;
      e.preventDefault();

      // SWAP 1↔1
      swapSiblings(dragItem, target);
      dragItem.removeAttribute("aria-grabbed");
      dragItem.classList.remove("is-dragging");
      dragItem = null;

      currentOrder = unique(Array.from(fileList.children).map(li => li.dataset.source));
      applyOrder(currentOrder);
    }

    function onDragEnd(e) {
      e.currentTarget.removeAttribute("aria-grabbed");
      e.currentTarget.classList.remove("is-dragging");
      $$(".drop-highlight", fileList).forEach(el => el.classList.remove("drop-highlight"));
      dragItem = null;
    }

    $$(".file-item", fileList).forEach(li => {
      li.removeEventListener("dragstart", onDragStart);
      li.removeEventListener("dragover", onDragOver);
      li.removeEventListener("drop", onDrop);
      li.removeEventListener("dragend", onDragEnd);

      li.addEventListener("dragstart", onDragStart);
      li.addEventListener("dragover", onDragOver);
      li.addEventListener("drop", onDrop);
      li.addEventListener("dragend", onDragEnd);
    });
  }

  // Remove um arquivo (todas as páginas do grupo A/B/C…)
  function removeSource(letter) {
    if (!letter) return;

    // 1) dispara o X de cada página (garante atualização do estado interno do merge)
    const grp = bySource.get(letter) || [];
    grp.forEach(card => card.querySelector(".remove-file")?.click());

    // 2) atualiza estruturas locais
    bySource.delete(letter);
    currentOrder = unique(currentOrder.filter(s => s !== letter));
    initialOrder = unique(initialOrder.filter(s => s !== letter));

    // 3) re-render da lista
    renderList(currentOrder, bySource);

    // 4) notifica interessados
    document.dispatchEvent(new CustomEvent("merge:removeSource", { detail: { source: letter }}));
    document.dispatchEvent(new CustomEvent("merge:sync"));
  }

  // Delegação extra (caso o botão seja recriado via MutationObserver)
  fileList.addEventListener("click", (e) => {
    const btn = e.target.closest(".remove-file");
    if (!btn) return;
    const li = btn.closest(".file-item");
    if (!li) return;
    removeSource(li.dataset.source);
  });

  function bootstrap() {
    const { order, map } = scan();
    if (!order.length) return;
    bySource     = map;
    initialOrder = unique(order);
    currentOrder = unique(order);
    renderList(currentOrder, bySource);
  }

  // Reage a mudanças no grid (novos arquivos/remoções)
  const mo = new MutationObserver(() => {
    const { order, map } = scan();
    if (!order.length) { fileList.innerHTML = ""; return; }

    const prev      = unique(currentOrder);
    const known     = prev.filter(s => order.includes(s));
    const newcomers = order.filter(s => !known.includes(s));
    currentOrder    = known.length ? unique([...known, ...newcomers]) : unique(order);

    bySource = map;
    renderList(currentOrder, bySource);
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

  bootstrap();
})();