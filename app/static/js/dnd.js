// app/static/js/dnd.js

/**
 * Drag & Drop de grid com dois modos:
 *  - mode: "swap"   → troca 1↔1
 *  - mode: "insert" → insere antes/depois (lista)
 *
 * Emite "reorder" no container ao finalizar:
 *   container.addEventListener("reorder", (e) => console.log(e.detail));
 *
 * Opções extras (p/ grid de páginas):
 *   groupByAttr?: string            // dataset que identifica o PDF (ex.: "source" ou "letter")
 *   groupHeadPredicate?: (el)=>bool // define o “arquivo mãe” (default: Pg1/cover)
 *   groupScopeEl?: Element          // onde buscar TODAS as páginas (default: auto)
 *   forceNative?: boolean           // força DnD nativo mesmo c/ SortableJS
 *   dragHandle?: string             // seletor que delimita onde pode iniciar o drag
 */

function getItems(root, selector) {
  return Array.from(root.querySelectorAll(selector));
}

function swapInParent(a, b) {
  if (!a || !b || !a.parentNode || a.parentNode !== b.parentNode) return;
  const p = a.parentNode;
  const m = document.createComment('dnd-swap');
  p.replaceChild(m, a);
  p.replaceChild(a, b);
  p.replaceChild(b, m);
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
  const vw = window.innerWidth, vh = window.innerHeight;
  let dx = 0, dy = 0;
  if (clientX < edge) dx = -speed; else if (clientX > vw - edge) dx = speed;
  if (clientY < edge) dy = -speed; else if (clientY > vh - edge) dy = speed;
  if (dx || dy) window.scrollBy(dx, dy);
}

/* ---- Agrupamento: auto-descoberta de chave e “mãe” ---- */
function inferGroupAttr(container, selector) {
  const sample = getItems(container, selector).slice(0, 20);
  const candidates = ['source','letter','file','fileId','fid','group','origin','parent'];
  for (const el of sample) {
    for (const k of candidates) {
      const v = el?.dataset?.[k];
      if (v != null && v !== '') return k;
    }
  }
  // último recurso: tenta detectar pela existência de letras A..Z
  const guess = sample.find(el => /^[A-Z]$/.test(el?.dataset?.letter || ''));
  if (guess) return 'letter';
  return null;
}

function defaultHeadPredicate(el) {
  return el?.dataset?.page === '1' || el?.dataset?.cover === '1' || el?.classList?.contains('is-cover');
}

export function makeSortableGrid(container, itemSelector, opts = {}) {
  const {
    mode = 'swap',
    dragHandle = null,
    scrollEdge = 30,
    scrollSpeed = 16,
    hoverDelayMs = 80,
    groupByAttr = null,
    groupHeadPredicate = defaultHeadPredicate,
    groupScopeEl = null,
    forceNative = false,
  } = opts;

  if (!container) return;

  // SortableJS apenas quando NÃO há agrupamento
  const Sortable = window.Sortable;
  if (!forceNative && Sortable && mode === 'insert' && !groupByAttr && groupHeadPredicate === defaultHeadPredicate) {
    const sortable = Sortable.create(container, {
      animation: 150,
      ghostClass: 'sortable-ghost',
      dragClass: 'dnd-dragging',
      chosenClass: 'dnd-chosen',
      handle: dragHandle || undefined,
      forceFallback: true,
      onEnd() { emitReorder(container, itemSelector); },
    });
    return sortable;
  }

  // ===== DnD Nativo =====
  let dragging = null;
  let placeholder = null;
  let startRect = null;
  let startX = 0, startY = 0, offsetX = 0, offsetY = 0;
  let lastTarget = null, hoverTimer = null, pointerId = null;

  // Estado de agrupamento
  let isGroupDrag = false;
  let groupKey = null;
  let groupAttr = null;
  const scope =
    groupScopeEl ||
    container.closest('#preview-merge') ||
    container.closest('#merge-page') ||
    container ||
    document;

  function isLeftButton(e) { return e.pointerType === 'touch' || e.buttons === 1 || e.button === 0; }

  function pickItemFromEvent(e) {
    const fromHandle = dragHandle ? e.target.closest(dragHandle) : null;
    const candidate = fromHandle ? fromHandle.closest(itemSelector) : e.target.closest(itemSelector);
    if (!candidate || !container.contains(candidate)) return null;
    return candidate;
  }

  function setDropHighlight(target) {
    if (lastTarget && lastTarget !== target) lastTarget.classList.remove('drop-highlight');
    if (target) target.classList.add('drop-highlight');
    lastTarget = target;
  }

  function clearHoverTimer() { if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; } }

  function moveDragged(item, clientX, clientY) {
    const x = clientX - offsetX, y = clientY - offsetY;
    item.style.transform = `translate(${x - startRect.left}px, ${y - startRect.top}px)`;
  }

  function reorderInsert(target, clientX, clientY) {
    const r = target.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const dx = clientX - cx, dy = clientY - cy;
    const horizontal = Math.abs(dx) > Math.abs(dy);
    if (horizontal) {
      (clientX < cx)
        ? target.parentNode.insertBefore(placeholder, target)
        : target.parentNode.insertBefore(placeholder, target.nextSibling);
    } else {
      (clientY < cy)
        ? target.parentNode.insertBefore(placeholder, target)
        : target.parentNode.insertBefore(placeholder, target.nextSibling);
    }
  }

  function reorderSwap(target) { swapInParent(placeholder, target); }

  function onPointerDown(e) {
    // Não iniciar drag em controles/inputs/links ou elementos marcados
    if (e.target.closest('[data-no-drag],button,a,input,textarea,select,label')) return;
    if (!isLeftButton(e)) return;

    const item = pickItemFromEvent(e);
    if (!item) return;

    dragging = item;
    pointerId = e.pointerId;

    const rect = item.getBoundingClientRect();
    startRect = rect;
    startX = e.clientX; startY = e.clientY;
    offsetX = startX - rect.left; offsetY = startY - rect.top;

    // Detecta “arrasto em grupo”: precisa ser “mãe” e possuir chave de grupo
    isGroupDrag = false;
    groupKey = null;
    groupAttr = groupByAttr || inferGroupAttr(scope, itemSelector);

    if (mode === 'insert' && groupAttr) {
      const key = dragging?.dataset?.[groupAttr];
      const isHead = typeof groupHeadPredicate === 'function' ? !!groupHeadPredicate(dragging) : false;
      if (key && isHead) {
        isGroupDrag = true;
        groupKey = key;
      }
    }

    // placeholder
    placeholder = document.createElement('div');
    placeholder.className = 'file-placeholder';
    placeholder.style.width = `${rect.width}px`;
    placeholder.style.height = `${rect.height}px`;

    item.parentNode.insertBefore(placeholder, item);
    item.classList.add('dnd-dragging');
    item.style.width = `${rect.width}px`;
    item.style.height = `${rect.height}px`;
    item.style.position = 'fixed';
    item.style.left = `${rect.left}px`;
    item.style.top = `${rect.top}px`;
    item.style.transform = `translate(0px, 0px)`;
    item.style.zIndex = '9999';
    item.style.pointerEvents = 'none';

    document.body.classList.add('dnd-no-select');
    try { item.setPointerCapture(pointerId); } catch {}

    item.addEventListener('pointermove', onPointerMove);
    item.addEventListener('pointerup', onPointerUp, { once: true });
    item.addEventListener('pointercancel', onPointerUp, { once: true });
  }

  function onPointerMove(e) {
    if (!dragging) return;
    moveDragged(dragging, e.clientX, e.clientY);
    autoScrollViewport(e.clientX, e.clientY, { edge: scrollEdge, speed: scrollSpeed });

    const el = document.elementFromPoint(e.clientX, e.clientY);
    const target = el ? el.closest(itemSelector) : null;
    const validTarget = target && target !== dragging ? target : null;
    setDropHighlight(validTarget);

    if (!validTarget) { clearHoverTimer(); return; }

    if (mode === 'insert') {
      reorderInsert(validTarget, e.clientX, e.clientY);
    } else {
      if (!hoverTimer || lastTarget !== validTarget) {
        clearHoverTimer();
        hoverTimer = setTimeout(() => { reorderSwap(validTarget); clearHoverTimer(); }, hoverDelayMs);
      }
    }
  }

  function restoreDraggedStyles() {
    if (!dragging) return;
    dragging.classList.remove('dnd-dragging');
    dragging.style.width = dragging.style.height =
    dragging.style.position = dragging.style.left =
    dragging.style.top = dragging.style.transform =
    dragging.style.zIndex = dragging.style.pointerEvents = '';
  }

  function onPointerUp() {
    clearHoverTimer();
    if (lastTarget) { lastTarget.classList.remove('drop-highlight'); lastTarget = null; }
    if (!dragging) { document.body.classList.remove('dnd-no-select'); return; }

    try { dragging.releasePointerCapture(pointerId); } catch {}
    dragging.removeEventListener('pointermove', onPointerMove);

    // === Arrasto em grupo (Pg1 “mãe”) ===
    if (mode === 'insert' && isGroupDrag && groupAttr && groupKey) {
      const allNow = getItems(scope, itemSelector);
      const ofThisGroup = allNow.filter(el => el?.dataset?.[groupAttr] === groupKey);

      restoreDraggedStyles();

      const frag = document.createDocumentFragment();
      ofThisGroup.forEach(el => frag.appendChild(el));

      if (placeholder && placeholder.parentNode) {
        placeholder.parentNode.replaceChild(frag, placeholder);
      }
      placeholder = null; dragging = null;

      emitReorder(container, itemSelector);
      document.body.classList.remove('dnd-no-select');
      return;
    }

    // === Arrasto comum ===
    if (placeholder && placeholder.parentNode) {
      placeholder.parentNode.replaceChild(dragging, placeholder);
    }
    placeholder = null;
    restoreDraggedStyles();
    dragging = null;

    emitReorder(container, itemSelector);
    document.body.classList.remove('dnd-no-select');
  }

  container.addEventListener('pointerdown', onPointerDown);
}

/* ===== Inits ===== */

// Arquivos → INSERT (C para o início = C A B)
export function makeFilesSortable(container, selector = '.file-wrapper', opts = {}) {
  return makeSortableGrid(container, selector, { mode: 'insert', ...opts });
}

// Páginas → INSERT + Agrupamento (arrastar Pg1 move TODO o bloco)
export function makePagesSortable(container, selector = '.page-wrapper', opts = {}) {
  return makeSortableGrid(container, selector, {
    mode: 'insert',
    forceNative: true,
    ...opts, // pode passar groupByAttr / groupScopeEl / dragHandle se quiser forçar
  });
}