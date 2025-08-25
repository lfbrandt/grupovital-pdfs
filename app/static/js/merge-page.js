// Habilita reordenação por Drag & Drop nas miniaturas da página "Juntar PDFs"
// Compatível com CSP rígida (sem inline styles). Usa classes utilitárias.
//
// Requisitos no HTML:
// - Um container por arquivo com atributo data-file-id (opcional, mas recomendado).
// - Dentro dele, uma lista com classe .pages-grid.
// - Cada miniatura deve ter classe .page-thumb e data-page-id.
//
// Opcional para persistir ordem por arquivo:
// - Um <input type="hidden" id="order-<fileId>"> para cada bloco.

(function () {
  const LIST_SELECTOR = ".pages-grid";    // container das thumbs (tem no merge.html)
  const ITEM_SELECTOR = ".page-thumb";    // cada miniatura

  // Classes utilitárias (defina no SCSS/CSS)
  const CLS_GRABBABLE = "dnd-grabbable";
  const CLS_DRAGGING  = "is-dragging";

  function markThumbsDraggable(list) {
    list.querySelectorAll(ITEM_SELECTOR).forEach(el => {
      if (!el.hasAttribute("draggable")) el.setAttribute("draggable", "true");
      el.classList.add(CLS_GRABBABLE);
      el.setAttribute("role", "option");
      el.setAttribute("aria-grabbed", "false");
    });
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-dropeffect", "move");
  }

  function bindDnd(list) {
    if (list.__dndBound) return; // evita duplicar listeners
    list.__dndBound = true;

    let dragged = null;

    list.addEventListener("dragstart", e => {
      const item = e.target.closest(ITEM_SELECTOR);
      if (!item || !list.contains(item)) return;
      dragged = item;
      // Necessário em alguns browsers para habilitar DnD
      try { e.dataTransfer.setData("text/plain", item.dataset.pageId || ""); } catch (_) {}
      e.dataTransfer.effectAllowed = "move";

      item.classList.add(CLS_DRAGGING);
      item.setAttribute("aria-grabbed", "true");
      document.body.classList.add("dnd-no-select");
    }, { passive: true });

    list.addEventListener("dragend", () => {
      if (!dragged) return;
      dragged.classList.remove(CLS_DRAGGING);
      dragged.setAttribute("aria-grabbed", "false");
      document.body.classList.remove("dnd-no-select");

      // Persiste a ordem deste bloco
      saveOrder(list);

      // Dispara evento customizado para outros scripts ouvirem
      list.dispatchEvent(new CustomEvent("reorder", {
        bubbles: true,
        detail: { order: getCurrentOrder(list) }
      }));

      dragged = null;
    }, { passive: true });

    // Inserção baseada no elemento mais próximo à posição do cursor
    list.addEventListener("dragover", e => {
      if (!dragged) return;
      e.preventDefault(); // permite o drop
      const ref = getDropReference(list, e.clientX, e.clientY, dragged);
      if (!ref || ref === dragged) return;

      // Se cursor está "antes" de ref, insere antes; senão, depois.
      const before = isBefore(e.clientX, e.clientY, ref);
      list.insertBefore(dragged, before ? ref : ref.nextSibling);
    }, { passive: false });

    // Evita que o navegador abra o item como link/arquivo
    list.addEventListener("drop", e => e.preventDefault(), { passive: false });
  }

  function getCurrentOrder(list) {
    return [...list.querySelectorAll(ITEM_SELECTOR)]
      .map(el => el.dataset.pageId || "");
  }

  function saveOrder(list) {
    const box = list.closest("[data-file-id]");
    const fileId = box?.getAttribute("data-file-id") || "";
    const order = getCurrentOrder(list);
    const hidden = fileId ? document.querySelector(`#order-${CSS.escape(fileId)}`) : null;
    if (hidden) hidden.value = order.join(",");
  }

  // Decide se o cursor está "antes" do centro do elemento de referência
  function isBefore(x, y, el) {
    const r = el.getBoundingClientRect();
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;

    // Heurística para grids: prioriza eixo vertical; em empate, eixo horizontal.
    if (Math.abs(y - cy) > r.height * 0.1) {
      return y < cy;
    }
    return x < cx;
  }

  // Encontra o melhor elemento de referência (o mais próximo do cursor) ignorando o que está sendo arrastado
  function getDropReference(list, x, y, dragged) {
    const candidates = [...list.querySelectorAll(ITEM_SELECTOR)]
      .filter(el => el !== dragged);

    if (candidates.length === 0) return null;

    let best = null;
    let bestScore = Infinity;

    for (const el of candidates) {
      const r = el.getBoundingClientRect();
      const dx = Math.max(r.left - x, 0, x - r.right);
      const dy = Math.max(r.top - y, 0, y - r.bottom);
      // Distância de ponto a retângulo (0 se cursor está sobre o elemento)
      const dist = Math.hypot(dx, dy);

      if (dist < bestScore) {
        bestScore = dist;
        best = el;
      }
    }
    return best;
  }

  function initAllLists(root = document) {
    root.querySelectorAll(LIST_SELECTOR).forEach(list => {
      markThumbsDraggable(list);
      bindDnd(list);
    });
  }

  document.addEventListener("DOMContentLoaded", () => initAllLists());

  // Se o preview for re-renderizado dinamicamente, esse observer garante que o DnD continue ativo
  const mo = new MutationObserver(muts => {
    // Re-inicializa apenas em nós adicionados que contenham listas de páginas
    muts.forEach(m => {
      m.addedNodes && m.addedNodes.forEach(node => {
        if (!(node instanceof Element)) return;
        if (node.matches && node.matches(LIST_SELECTOR)) {
          initAllLists(node.parentNode || node);
        } else if (node.querySelector) {
          const hasList = node.querySelector(LIST_SELECTOR);
          if (hasList) initAllLists(node);
        }
      });
    });
  });
  mo.observe(document.body, { childList: true, subtree: true });
})();