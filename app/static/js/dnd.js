// app/static/js/dnd.js

/**
 * Drag & Drop de grid com dois modos:
 *  - mode: "swap"   → troca 1↔1 (estável, sem efeito dominó)
 *  - mode: "insert" → insere antes/depois (estilo lista)
 *
 * Emite "reorder" no container ao finalizar:
 *   container.addEventListener("reorder", (e) => console.log(e.detail));
 *
 * Opções:
 *   { mode: "swap" | "insert", dragHandle?: string, scrollEdge?: number, scrollSpeed?: number }
 */

function getItems(container, selector) {
  return Array.from(container.querySelectorAll(selector));
}

function clamp(n, min, max) {
  return Math.max(min, Math.min(max, n));
}

function swapInParent(a, b) {
  // Troca nós a↔b no mesmo parent usando um marcador
  if (!a || !b || !a.parentNode || a.parentNode !== b.parentNode) return;
  const parent = a.parentNode;
  const marker = document.createComment('dnd-swap');
  parent.replaceChild(marker, a);
  parent.replaceChild(a, b);
  parent.replaceChild(b, marker);
}

function emitReorder(container, selector) {
  const order = getItems(container, selector).map((el, index) => ({
    index,
    id: el.dataset.uid || el.dataset.fileId || el.id || null,
    el
  }));
  container.dispatchEvent(new CustomEvent('reorder', { detail: { order } }));
}

function autoScrollViewport(clientX, clientY, { edge = 30, speed = 16 }) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  let dx = 0;
  let dy = 0;
  if (clientX < edge) dx = -speed;
  else if (clientX > vw - edge) dx = speed;

  if (clientY < edge) dy = -speed;
  else if (clientY > vh - edge) dy = speed;

  if (dx || dy) window.scrollBy(dx, dy);
}

export function makeSortableGrid(container, itemSelector, opts = {}) {
  const {
    mode = 'swap',             // 'swap' | 'insert'
    dragHandle = null,         // seletor dentro do item (opcional)
    scrollEdge = 30,
    scrollSpeed = 16,
    hoverDelayMs = 80,         // evita jitter; só troca após ~80ms de hover
  } = opts;

  if (!container) return;

  // Se SortableJS existir e o modo for "insert", use-o (mantemos swap no nativo)
  const Sortable = window.Sortable;
  if (Sortable && mode === 'insert') {
    const sortable = Sortable.create(container, {
      animation: 150,
      ghostClass: 'sortable-ghost',
      dragClass: 'dnd-dragging',
      chosenClass: 'dnd-chosen',
      handle: dragHandle || undefined,
      forceFallback: true, // evita dragImage nativo bugado em alguns browsers
      onEnd() {
        emitReorder(container, itemSelector);
      },
    });
    return sortable;
  }

  // ===== Nativo via Pointer Events (funciona desktop e mobile) =====
  let dragging = null;
  let placeholder = null;
  let startRect = null;
  let startX = 0;
  let startY = 0;
  let offsetX = 0;
  let offsetY = 0;
  let lastTarget = null;
  let hoverTimer = null;
  let pointerId = null;

  function isLeftButton(e) {
    // touch/pointer não tem buttons = 1; pointerType === 'touch' sempre ok
    return e.pointerType === 'touch' || e.buttons === 1 || e.button === 0;
  }

  function pickItemFromEvent(e) {
    const fromHandle = dragHandle ? e.target.closest(dragHandle) : null;
    const candidate = fromHandle ? fromHandle.closest(itemSelector) : e.target.closest(itemSelector);
    if (!candidate || !container.contains(candidate)) return null;
    return candidate;
  }

  function onPointerDown(e) {
    if (!isLeftButton(e)) return;
    const item = pickItemFromEvent(e);
    if (!item) return;

    dragging = item;
    pointerId = e.pointerId;

    const rect = item.getBoundingClientRect();
    startRect = rect;
    startX = e.clientX;
    startY = e.clientY;
    offsetX = startX - rect.left;
    offsetY = startY - rect.top;

    // placeholder do tamanho do card
    placeholder = document.createElement('div');
    placeholder.className = 'file-placeholder';
    placeholder.style.width = `${rect.width}px`;
    placeholder.style.height = `${rect.height}px`;

    // fixa o espaço e “destaca” o arrasto
    item.parentNode.insertBefore(placeholder, item);
    item.classList.add('dnd-dragging');
    item.style.width = `${rect.width}px`;
    item.style.height = `${rect.height}px`;
    item.style.position = 'fixed';
    item.style.left = `${rect.left}px`;
    item.style.top = `${rect.top}px`;
    item.style.transform = `translate(0px, 0px)`;
    item.style.zIndex = '9999';
    item.style.pointerEvents = 'none'; // evitar capturar eventos durante o arrasto

    document.body.classList.add('dnd-no-select');

    try { item.setPointerCapture(pointerId); } catch {}

    item.addEventListener('pointermove', onPointerMove);
    item.addEventListener('pointerup', onPointerUp, { once: true });
    item.addEventListener('pointercancel', onPointerUp, { once: true });
  }

  function moveDragged(item, clientX, clientY) {
    const x = clientX - offsetX;
    const y = clientY - offsetY;
    item.style.transform = `translate(${x - startRect.left}px, ${y - startRect.top}px)`;
  }

  function clearHoverTimer() {
    if (hoverTimer) {
      clearTimeout(hoverTimer);
      hoverTimer = null;
    }
  }

  function setDropHighlight(target) {
    if (lastTarget && lastTarget !== target) {
      lastTarget.classList.remove('drop-highlight');
    }
    if (target) target.classList.add('drop-highlight');
    lastTarget = target;
  }

  function reorderInsert(target, clientX, clientY) {
    // decide antes/depois baseado no centro do target
    const r = target.getBoundingClientRect();
    const centerX = r.left + r.width / 2;
    const centerY = r.top + r.height / 2;

    // heurística simples p/ grid: usa o eixo mais “dominante”
    const dx = clientX - centerX;
    const dy = clientY - centerY;
    const horizontal = Math.abs(dx) > Math.abs(dy);

    if (horizontal) {
      if (clientX < centerX) {
        target.parentNode.insertBefore(placeholder, target);
      } else {
        target.parentNode.insertBefore(placeholder, target.nextSibling);
      }
    } else {
      if (clientY < centerY) {
        target.parentNode.insertBefore(placeholder, target);
      } else {
        target.parentNode.insertBefore(placeholder, target.nextSibling);
      }
    }
  }

  function reorderSwap(target) {
    // troca direta: placeholder ↔ target
    swapInParent(placeholder, target);
  }

  function onPointerMove(e) {
    if (!dragging) return;

    moveDragged(dragging, e.clientX, e.clientY);
    autoScrollViewport(e.clientX, e.clientY, { edge: scrollEdge, speed: scrollSpeed });

    const el = document.elementFromPoint(e.clientX, e.clientY);
    const target = el ? el.closest(itemSelector) : null;

    // nunca considere o próprio item em arrasto
    const validTarget = target && target !== dragging ? target : null;
    setDropHighlight(validTarget);

    if (!validTarget) {
      clearHoverTimer();
      return;
    }

    if (mode === 'insert') {
      reorderInsert(validTarget, e.clientX, e.clientY);
    } else {
      // 'swap': aguarda um pouco sobre o alvo pra evitar tremedeira
      if (!hoverTimer || lastTarget !== validTarget) {
        clearHoverTimer();
        hoverTimer = setTimeout(() => {
          reorderSwap(validTarget);
          clearHoverTimer();
        }, hoverDelayMs);
      }
    }
  }

  function onPointerUp() {
    clearHoverTimer();

    if (lastTarget) {
      lastTarget.classList.remove('drop-highlight');
      lastTarget = null;
    }

    if (dragging) {
      // solta no lugar do placeholder
      placeholder.parentNode.replaceChild(dragging, placeholder);
      placeholder = null;

      // reseta estilos
      dragging.classList.remove('dnd-dragging');
      dragging.style.width = '';
      dragging.style.height = '';
      dragging.style.position = '';
      dragging.style.left = '';
      dragging.style.top = '';
      dragging.style.transform = '';
      dragging.style.zIndex = '';
      dragging.style.pointerEvents = '';

      try { dragging.releasePointerCapture(pointerId); } catch {}
      dragging.removeEventListener('pointermove', onPointerMove);
      dragging = null;

      emitReorder(container, itemSelector);
    }

    document.body.classList.remove('dnd-no-select');
  }

  // Delegação no container para iniciar drag
  container.addEventListener('pointerdown', onPointerDown);
}

export function makeFilesSortable(container, selector = '.file-wrapper', opts = {}) {
  return makeSortableGrid(container, selector, { mode: 'swap', ...opts });
}

export function makePagesSortable(container, selector = '.page-wrapper', opts = {}) {
  return makeSortableGrid(container, selector, { mode: 'insert', ...opts });
}
