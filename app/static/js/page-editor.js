// ===================================================================
// EDITOR DE PDF (modal) — sem dependências externas, CSP friendly.
// Ferramentas: Borracha (com redimensionamento), Texto (arrastável,
// redimensionável e escalável), Imagem (escala/rotação), Mover,
// Zoom ±, Ajustar, Desfazer/Refazer, Limpar, Cancelar, Salvar.
// ===================================================================

'use strict';

import { getCSRFToken } from './utils.js';

/* ================================================================
   Config
================================================================ */
const OVERLAY_ENDPOINT = '/api/edit/apply/overlays';
const IMAGE_UPLOAD_ENDPOINT = '/api/edit/overlay-image/upload';
const MAX_VIEW_W = 1400;
const MAX_VIEW_H = 900;

// Texto
const TEXT_MIN_W = 60;
const TEXT_MAX_W_RATIO = 0.95;
const TEXT_MIN_SIZE = 8;
const TEXT_MAX_SIZE = 200;
const TEXT_HANDLE = 10; // px (na view), ajustado por zoom

// Whiteout visual
const WHITE_COLOR = '#FFFFFF';
const ALPHA = { white: 1.0 };

// Persistência da ferramenta atual
const LAST_TOOL_KEY = 'gv:editor:lastTool';

/* ================================================================
   Helpers
================================================================ */
const el = (tag, cls, attrs) => {
  const n = document.createElement(tag);
  if (cls) (Array.isArray(cls) ? cls : [cls]).forEach(c => n.classList.add(c));
  if (attrs) Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, String(v)));
  return n;
};
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const normRect = (x0, y0, x1, y1) => [Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1)];
const dpr = () => Math.max(1, window.devicePixelRatio || 1);

/* ================================================================
   Loader CSP-safe do bitmap
================================================================ */
async function loadBitmapCSPSafe(blob) {
  if (globalThis.createImageBitmap) {
    try { return await createImageBitmap(blob); } catch {}
  }
  const dataUrl = await new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onerror = () => reject(new Error('Falha ao ler DataURL'));
    fr.onload = () => resolve(fr.result);
    fr.readAsDataURL(blob);
  });
  const img = await new Promise((resolve, reject) => {
    const im = new Image();
    im.onload = () => resolve(im);
    im.onerror = () => reject(new Error('Falha ao carregar DataURL'));
    im.src = String(dataUrl);
  });
  return { width: img.naturalWidth || img.width, height: img.naturalHeight || img.height, _img: img };
}

/* ================================================================
   Mapeamento VIEW -> BASE (0°) considerando rotação visual
================================================================ */
function mapViewToBase(nx, ny, viewRotation = 0) {
  const r = ((viewRotation % 360) + 360) % 360;
  switch (r) {
    case 0:   return [nx, ny];
    case 90:  return [1 - ny, nx];
    case 180: return [1 - nx, 1 - ny];
    case 270: return [ny, 1 - nx];
    default:  return [nx, ny];
  }
}

/* ================================================================
   Estado do editor
================================================================ */
function createState(canvas, bmp, viewRotation) {
  return {
    canvas,
    ctx: canvas.getContext('2d', { alpha: false }),
    w: canvas.clientWidth,
    h: canvas.clientHeight,
    bmp,
    viewRotation,

    // viewport
    zoom: 1,
    panX: 0,
    panY: 0,

    // overlays
    tool: 'text',      // default agora é TEXTO
    rects: [],         // { viewPx:[x0,y0,x1,y1], viewNorm:[x0..x1,y0..y1] }
    // Texto: boxW controla a largura; _lines cache das linhas quebradas
    texts: [],         // { view:{x,y,size,boxW,text,width,height}, viewNorm:{x,y,sizeRel} }
    images: [],        // { imageId, bmp, view:{x0,y0,x1,y1, rotate}, viewNorm:{x0,y0,x1,y1, rotate} }

    hover: { type:null, idx:-1, handle:null }, // handle: para texto 0..7 | rect 'rect-#'
    selRect: null,

    pointer: { active:false, mode:null, idx:-1, handle:null, id:null, start:{x:0,y:0}, dx:0, dy:0 },

    undoStack: [],
    redoStack: [],

    _listeners: [],
    _buttons: {},
    _cleanup: [],
    _textWidget: null,
    _imageInput: null,
    _selectedImage: -1,
    _selectedText: -1,
    _selectedRect: -1
  };
}

/* ================================================================
   Conversões tela <-> view
================================================================ */
function screenToView(st, sx, sy) {
  const r = st.canvas.getBoundingClientRect();
  const x = (sx - r.left);
  const y = (sy - r.top);
  const vx = (x - st.panX) / st.zoom;
  const vy = (y - st.panY) / st.zoom;
  return [clamp(vx, 0, st.w), clamp(vy, 0, st.h)];
}

/* ================================================================
   Rect handles (8 alças) + hit-test
================================================================ */
function getRectHandles(r, st) {
  const [x0, y0, x1, y1] = r.viewPx;
  const hs = TEXT_HANDLE / st.zoom;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const pts = [
    [x0, y0], [x1, y0], [x1, y1], [x0, y1], // TL, TR, BR, BL
    [cx, y0], [x1, cy], [cx, y1], [x0, cy]  // T, R, B, L
  ];
  return pts.map(([x,y]) => [x - hs/2, y - hs/2, hs, hs]);
}
function hitRectHandle(st, r, vx, vy) {
  const hs = getRectHandles(r, st);
  for (let i = 0; i < hs.length; i++) {
    const [x, y, w, h] = hs[i];
    if (vx >= x && vx <= x+w && vy >= y && vy <= y+h) return i;
  }
  return -1;
}

/* ================================================================
   Texto — quebra automática por largura
================================================================ */
function layoutLines(ctx, rawText, fontPx, boxW) {
  const paras = String(rawText || '').split('\n');
  const lines = [];
  ctx.font = `${Math.max(10, fontPx)}px sans-serif`;
  for (const p of paras) {
    const words = p.split(/\s+/).filter(Boolean);
    if (!words.length) { lines.push(''); continue; }
    let cur = words[0];
    for (let i = 1; i < words.length; i++) {
      const test = cur + ' ' + words[i];
      if (ctx.measureText(test).width <= boxW) cur = test;
      else { lines.push(cur); cur = words[i]; }
    }
    lines.push(cur);
  }
  return lines;
}
function measureTextBox(st, t) {
  const { ctx } = st;
  const boxW = clamp(t.view.boxW || 220, TEXT_MIN_W, st.w * TEXT_MAX_W_RATIO);
  const lines = layoutLines(ctx, t.view.text || '', t.view.size, boxW);
  t._lines = lines;
  t.view.boxW = boxW;
  t.view.width  = boxW;
  t.view.height = Math.max(12, lines.length * t.view.size * 1.2);
}

/* ================================================================
   Text handles (8) + cursor mapping
================================================================ */
function getTextHandles(t, st) {
  const x0 = t.view.x, y0 = t.view.y;
  const x1 = x0 + t.view.width, y1 = y0 + t.view.height;
  const hs = TEXT_HANDLE / st.zoom;
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  const pts = [
    [x0, y0], [x1, y0], [x1, y1], [x0, y1],
    [cx, y0], [x1, cy], [cx, y1], [x0, cy]
  ];
  return pts.map(([x,y]) => [x - hs/2, y - hs/2, hs, hs]);
}
function hitTextRectHandle(st, t, vx, vy) {
  const hs = getTextHandles(t, st);
  for (let i = 0; i < hs.length; i++) {
    const [x, y, w, h] = hs[i];
    if (vx >= x && vx <= x+w && vy >= y && vy <= y+h) return i; // 0..7
  }
  return -1;
}
function cursorForTextHandle(h) {
  // 0 TL, 1 TR, 2 BR, 3 BL, 4 T, 5 R, 6 B, 7 L
  if (h === 5 || h === 7) return 'ew-resize';
  if (h === 4 || h === 6) return 'ns-resize';
  if (h === 0 || h === 2) return 'nwse-resize';
  if (h === 1 || h === 3) return 'nesw-resize';
  return 'move';
}

/* ================================================================
   Desenho e hit-tests
================================================================ */
function drawScene(st) {
  const { ctx, canvas } = st;
  const scale = dpr();
  canvas.width = Math.floor(canvas.clientWidth * scale);
  canvas.height = Math.floor(canvas.clientHeight * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  // fundo
  ctx.fillStyle = '#111';
  ctx.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);

  // zoom/pan
  ctx.save();
  ctx.translate(st.panX, st.panY);
  ctx.scale(st.zoom, st.zoom);

  // página
  const bmp = st.bmp;
  if ('close' in bmp && typeof bmp.close === 'function') ctx.drawImage(bmp, 0, 0, st.w, st.h);
  else ctx.drawImage(bmp._img, 0, 0, st.w, st.h);

  // 1) whiteout
  st.rects.forEach((r, i) => {
    const [x0, y0, x1, y1] = r.viewPx;
    ctx.save();
    ctx.globalAlpha = ALPHA.white;
    ctx.fillStyle = WHITE_COLOR;
    ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
    ctx.restore();

    const isHover = (st.hover.type === 'rect' && st.hover.idx === i) || st._selectedRect === i;
    ctx.save();
    ctx.lineWidth = Math.max(1, 1 / st.zoom);
    ctx.strokeStyle = isHover ? '#00B27A' : '#2a2a2a';
    ctx.setLineDash(isHover ? [6 / st.zoom, 6 / st.zoom] : []);
    ctx.strokeRect(Math.round(x0)+0.5, Math.round(y0)+0.5, Math.round(x1-x0)-1, Math.round(y1-y0)-1);
    if (isHover) {
      const hs = getRectHandles(r, st);
      ctx.fillStyle = '#00B27A';
      hs.forEach(([hx, hy, hw, hh]) => ctx.fillRect(hx, hy, hw, hh));
    }
    ctx.restore();
  });

  // 2) imagens
  st.images.forEach((im, i) => {
    const { x0, y0, x1, y1, rotate = 0 } = im.view;
    const cx = (x0 + x1) / 2;
    const cy = (y0 + y1) / 2;
    const w = x1 - x0;
    const h = y1 - y0;

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate((rotate % 360) * Math.PI / 180);
    const bmp = im.bmp;
    if (bmp) {
      const hasClose = ('close' in bmp && typeof bmp.close === 'function');
      if (hasClose) ctx.drawImage(bmp, -w/2, -h/2, w, h);
      else ctx.drawImage(bmp._img, -w/2, -h/2, w, h);
    }
    const isHover = (st.hover.type === 'image' && st.hover.idx === i) || st._selectedImage === i;
    if (isHover) {
      ctx.lineWidth = Math.max(1, 1 / st.zoom);
      ctx.setLineDash([6 / st.zoom, 6 / st.zoom]);
      ctx.strokeStyle = '#00B27A';
      ctx.strokeRect(-w/2 + 0.5, -h/2 + 0.5, w, h);
    }
    ctx.restore();
  });

  // 3) textos (8 alças)
  st.texts.forEach((t, i) => {
    measureTextBox(st, t);
    const { x, y, size, width, height } = t.view;

    ctx.save();
    ctx.font = `${Math.max(10, size)}px sans-serif`;
    ctx.fillStyle = '#000';
    ctx.textBaseline = 'top';
    (t._lines || ['']).forEach((line, idx) => {
      ctx.fillText(line, x, y + idx * size * 1.2);
    });
    ctx.restore();

    const selected = (st.hover.type === 'text' && st.hover.idx === i) || st._selectedText === i;
    if (selected) {
      ctx.save();
      ctx.setLineDash([6 / st.zoom, 6 / st.zoom]);
      ctx.strokeStyle = '#00B27A';
      ctx.lineWidth = Math.max(1, 1 / st.zoom);
      ctx.strokeRect(x + 0.5, y + 0.5, width, height);

      // alças (8)
      ctx.fillStyle = '#00B27A';
      const hs = getTextHandles(t, st);
      hs.forEach(([hx, hy, hw, hh]) => ctx.fillRect(hx, hy, hw, hh));
      ctx.restore();
    }
  });

  // guia de criação
  if (st.selRect) {
    const [x0, y0, x1, y1] = st.selRect;
    ctx.save();
    ctx.globalAlpha = 0.35;
    ctx.fillStyle = WHITE_COLOR;
    ctx.fillRect(x0, y0, (x1 - x0), (y1 - y0));
    ctx.setLineDash([6 / st.zoom, 6 / st.zoom]);
    ctx.strokeStyle = '#00B27A';
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, (x1 - x0), (y1 - y0));
    ctx.restore();
  }

  ctx.restore();
  updateButtonsState(st);

  // cursor
  let cursor = 'default';
  if (st.tool === 'pan') cursor = 'grab';
  if (st.hover.type === 'text') {
    if (Number.isInteger(st.hover.handle)) cursor = cursorForTextHandle(st.hover.handle);
    else cursor = 'move';
  }
  if (st.pointer.mode === 'pan') cursor = 'grabbing';
  st.canvas.style.cursor = cursor;
}

function hitImageAABB(st, vx, vy) {
  for (let i = st.images.length - 1; i >= 0; i--) {
    const im = st.images[i];
    const { x0, y0, x1, y1 } = im.view;
    if (vx >= x0 && vx <= x1 && vy >= y0 && vy <= y1) return i;
  }
  return -1;
}

function findHover(st, vx, vy) {
  // textos (alças primeiro, depois a caixa)
  for (let i = st.texts.length - 1; i >= 0; i--) {
    const t = st.texts[i];
    measureTextBox(st, t);
    const { x, y, width, height } = t.view;

    const hi = hitTextRectHandle(st, t, vx, vy);
    if (hi !== -1) return { type: 'text', idx: i, handle: hi };

    if (vx >= x && vx <= x + width && vy >= y && vy <= y + height) {
      return { type:'text', idx:i, handle:null };
    }
  }
  // imagens
  const imIdx = hitImageAABB(st, vx, vy);
  if (imIdx !== -1) return { type:'image', idx: imIdx, handle:null };
  // retângulos
  for (let i = st.rects.length - 1; i >= 0; i--) {
    const r = st.rects[i];
    const [x0, y0, x1, y1] = r.viewPx;
    const rh = hitRectHandle(st, r, vx, vy);
    if (rh !== -1) return { type:'rect', idx:i, handle:`rect-${rh}` };
    if (vx >= x0 && vx <= x1 && vy >= y0 && vy <= y1) return { type:'rect', idx:i, handle:null };
  }
  return { type:null, idx:-1, handle:null };
}

/* ================================================================
   UI helpers
================================================================ */
function setTool(st, t) {
  st.tool = t;
  // persiste a última ferramenta
  try { sessionStorage.setItem(LAST_TOOL_KEY, t); } catch {}
  const { btnErs, btnTxt, btnPan, btnImg } = st._buttons;
  [btnErs, btnTxt, btnPan, btnImg].forEach(b => b && b.classList.remove('is-active'));
  const map = { erase: btnErs, text: btnTxt, pan: btnPan, image: btnImg };
  if (map[t]) map[t].classList.add('is-active');
  if (t !== 'image') st._selectedImage = -1;
}

function updateButtonsState(st) {
  const { btnUndo, btnRedo, btnClear, btnRotateL, btnRotateR } = st._buttons;
  if (!btnUndo) return;
  btnUndo.disabled = !st.undoStack.length;
  btnRedo.disabled = !st.redoStack.length;
  btnClear.disabled = !(st.rects.length || st.texts.length || st.images.length);
  const sel = st._selectedImage;
  if (btnRotateL) btnRotateL.disabled = sel < 0;
  if (btnRotateR) btnRotateR.disabled = sel < 0;
}

/* ================================================================
   Undo/Redo
================================================================ */
function snapshot(st) {
  return {
    rects: st.rects.map(r => ({ viewPx:[...r.viewPx], viewNorm:[...r.viewNorm] })),
    texts: st.texts.map(t => ({ view: structuredClone(t.view), viewNorm: structuredClone(t.viewNorm) })),
    images: st.images.map(im => ({ imageId: im.imageId, view: structuredClone(im.view), viewNorm: structuredClone(im.viewNorm) })),
    zoom: st.zoom, panX: st.panX, panY: st.panY,
    _selectedImage: st._selectedImage, _selectedText: st._selectedText, _selectedRect: st._selectedRect
  };
}
function pushUndo(st) { st.undoStack.push(snapshot(st)); st.redoStack.length = 0; }
function doUndo(st) { if (!st.undoStack.length) return; const snap = st.undoStack.pop(); st.redoStack.push(snapshot(st)); Object.assign(st, snap); }
function doRedo(st) { if (!st.redoStack.length) return; const snap = st.redoStack.pop(); st.undoStack.push(snapshot(st)); Object.assign(st, snap); }

/* ================================================================
   Editor inline de texto (NÃO muda para Pan ao fechar)
================================================================ */
function openInlineTextEditor(st, vx, vy, editIdx = null) {
  closeInlineTextEditor(st);

  const wrap = st.canvas.parentElement;
  const box = el('div', ['pe-text-editor']);
  box.style.position = 'absolute';
  box.style.left = `${Math.round(st.panX + vx * st.zoom)}px`;
  box.style.top  = `${Math.round(st.panY + vy * st.zoom)}px`;
  box.style.zIndex = '2';
  box.style.background = 'rgba(255,255,255,.96)';
  box.style.border = '1px solid #00995d';
  box.style.borderRadius = '8px';
  box.style.padding = '8px';
  box.style.boxShadow = '0 6px 22px rgba(0,0,0,.2)';
  box.style.maxWidth = 'min(70vw, 640px)';

  const ta = el('textarea', ['pe-textarea'], { rows: '3', placeholder: 'Digite o texto…' });
  ta.style.minWidth = '200px';
  ta.style.minHeight = '54px';
  ta.style.resize = 'both';
  ta.style.font = '14px sans-serif';

  const row = el('div', ['pe-row']);
  row.style.display = 'flex';
  row.style.gap = '8px';
  row.style.alignItems = 'center';
  row.style.marginTop = '6px';

  const lbl = el('label'); lbl.textContent = 'Tamanho:';
  const inputSize = el('input', null, { type: 'number', min: String(TEXT_MIN_SIZE), max: String(TEXT_MAX_SIZE), step: '1', value: '16' });
  inputSize.style.width = '72px';

  const btnOk = el('button', ['pe-btn','pe-primary'], { type:'button' }); btnOk.textContent = 'OK';
  const btnCancel = el('button', ['pe-btn'], { type:'button' }); btnCancel.textContent = 'Cancelar';

  row.append(lbl, inputSize, btnOk, btnCancel);
  box.append(ta, row);
  wrap.append(box);

  if (editIdx !== null && editIdx >= 0 && st.texts[editIdx]) {
    const T = st.texts[editIdx];
    ta.value = T.view.text || '';
    inputSize.value = String(Math.round(T.view.size));
  }

  const finish = () => { closeInlineTextEditor(st); /* mantém a ferramenta atual */ drawScene(st); };

  const doCommit = () => {
    const txt = ta.value ?? '';
    const size = clamp(parseInt(inputSize.value || '16', 10) || 16, TEXT_MIN_SIZE, TEXT_MAX_SIZE);

    if (editIdx !== null && editIdx >= 0 && st.texts[editIdx]) {
      pushUndo(st);
      const t = st.texts[editIdx];
      t.view.text = txt;
      t.view.size = size;
      measureTextBox(st, t);
      t.view.x = clamp(t.view.x, 0, st.w - t.view.width);
      t.view.y = clamp(t.view.y, 0, st.h - t.view.height);
      t.viewNorm.x = t.view.x / st.w;
      t.viewNorm.y = t.view.y / st.h;
      t.viewNorm.sizeRel = t.view.size / st.h;
      st._selectedText = editIdx;
      finish();
      return;
    }

    if (!txt.trim()) { finish(); return; }
    pushUndo(st);
    const t = {
      view: { x: vx, y: vy, size, boxW: 220, text: txt, width: 0, height: 0 },
      viewNorm: { x: vx / st.w, y: vy / st.h, sizeRel: size / st.h }
    };
    measureTextBox(st, t);
    t.view.x = clamp(t.view.x, 0, st.w - t.view.width);
    t.view.y = clamp(t.view.y, 0, st.h - t.view.height);
    t.viewNorm.x = t.view.x / st.w;
    t.viewNorm.y = t.view.y / st.h;
    st.texts.push(t);
    st._selectedText = st.texts.length - 1;
    finish();
  };

  const doCancel = () => finish();

  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doCommit(); }
    if (e.key === 'Escape') { e.preventDefault(); doCancel(); }
  };

  btnOk.addEventListener('click', doCommit);
  btnCancel.addEventListener('click', doCancel);
  ta.addEventListener('keydown', onKey);

  st._cleanup.push(() => {
    btnOk.removeEventListener('click', doCommit);
    btnCancel.removeEventListener('click', doCancel);
    ta.removeEventListener('keydown', onKey);
  });

  st._textWidget = box;
  ta.focus();
}
function closeInlineTextEditor(st) {
  if (!st._textWidget) return;
  st._cleanup.forEach(fn => { try { fn(); } catch {} });
  st._cleanup.length = 0;
  try { st._textWidget.remove(); } catch {}
  st._textWidget = null;
}

/* ================================================================
   Upload + criação de imagem
================================================================ */
async function uploadOverlayImage(file, sessionId) {
  const fd = new FormData();
  fd.append('session_id', sessionId);
  fd.append('image', file);

  const resp = await fetch(IMAGE_UPLOAD_ENDPOINT, {
    method: 'POST',
    headers: { 'Accept': 'application/json', 'X-CSRFToken': getCSRFToken() },
    body: fd,
    credentials: 'same-origin',
    cache: 'no-store'
  });
  const data = await resp.json().catch(()=> ({}));
  if (!resp.ok) throw new Error(data?.error || `${resp.status} ${resp.statusText}`);
  return data;
}

/* ================================================================
   Editor principal
================================================================ */
export async function openPageEditor(opts) {
  const {
    bitmap, sessionId, pageIndex,
    pdfPageSize = null, getBitmap = null, viewRotation = 0
  } = opts || {};

  if (!bitmap || !sessionId || typeof pageIndex !== 'number') {
    alert('Editor indisponível: parâmetros insuficientes.');
    return;
  }

  // ---- Modal
  const overlay = el('div', ['pe-overlay','modal-overlay'], { role:'dialog', 'aria-modal':'true' });
  const modal   = el('div', ['pe-modal','modal']);
  const header  = el('div', ['pe-header','modal-header']);
  const title   = el('div', 'pe-title'); title.textContent = 'Editor de página';
  const btnClose= el('button', ['pe-btn','modal-close'], { type:'button', 'aria-label':'Fechar' }); btnClose.textContent = '×';

  // Toolbar
  const toolbar = el('div', ['pe-toolbar','wizard-toolbar']);
  const btnErs  = el('button', ['pe-btn','pe-tool'], { type:'button', title: 'Borracha (criar/mover/redimensionar; Delete apaga)' }); btnErs.textContent = 'Borracha';
  const btnTxt  = el('button', ['pe-btn','pe-tool'], { type:'button', title: 'Texto (T)' }); btnTxt.textContent = 'Texto';
  const btnImg  = el('button', ['pe-btn','pe-tool'], { type:'button', title: 'Imagem (I)' }); btnImg.textContent = 'Imagem';
  const btnPan  = el('button', ['pe-btn','pe-tool'], { type:'button', title: 'Mover (H)' }); btnPan.textContent = 'Mover';

  const sep1    = el('div', 'pe-spacer-sm');
  const btnZOut = el('button', ['pe-btn'], { type:'button', title:'Zoom - (Ctrl -)' }); btnZOut.textContent = '−';
  const btnZFit = el('button', ['pe-btn'], { type:'button', title:'Ajustar' }); btnZFit.textContent = 'Ajustar';
  const btnZIn  = el('button', ['pe-btn'], { type:'button', title:'Zoom + (Ctrl +)' }); btnZIn.textContent = '+';

  const sep2    = el('div', 'pe-spacer');
  const btnUndo = el('button', ['pe-btn','pe-ghost'], { type:'button', title:'Desfazer (Ctrl+Z)' }); btnUndo.textContent = 'Desfazer';
  const btnRedo = el('button', ['pe-btn','pe-ghost'], { type:'button', title:'Refazer (Ctrl+Shift+Z)' }); btnRedo.textContent = 'Refazer';
  const btnRotateL = el('button', ['pe-btn','pe-ghost'], { type:'button', title:'Girar imagem -90°' }); btnRotateL.textContent = '⟲';
  const btnRotateR = el('button', ['pe-btn','pe-ghost'], { type:'button', title:'Girar imagem +90°' }); btnRotateR.textContent = '⟳';
  const btnClear= el('button', ['pe-btn','pe-ghost'], { type:'button', title:'Limpar todos' }); btnClear.textContent = 'Limpar';
  const btnCancel = el('button', ['pe-btn'], { type:'button' }); btnCancel.textContent = 'Cancelar';
  const btnSave   = el('button', ['pe-btn','pe-primary'], { type:'button' }); btnSave.textContent = 'Salvar';

  toolbar.append(btnErs, btnTxt, btnImg, btnPan, sep1, btnZOut, btnZFit, btnZIn, sep2, btnUndo, btnRedo, btnRotateL, btnRotateR, btnClear, btnCancel, btnSave);
  header.append(title, btnClose);

  // Corpo
  const body = el('div', ['pe-body','modal-body']);
  const canvasWrap = el('div', 'pe-canvas-wrap');
  const canvas = el('canvas', 'pe-canvas', { 'data-no-drag': '' });
  canvasWrap.append(canvas);
  body.append(canvasWrap);

  const note = el('div', 'pe-hint');
  note.textContent = 'Texto: arraste para criar uma caixa; clique para selecionar e arrastar; alças (8) redimensionam (laterais ajustam largura, topo/base/cantos escalam a fonte); duplo-clique edita; roda = fonte, Shift+roda = largura. Imagem: arraste; roda redimensiona; ⟲/⟳ gira. Borracha: crie/mova/redimensione.';
  body.append(note);

  modal.append(header, toolbar, body);
  overlay.append(modal);
  document.body.appendChild(overlay);

  // Input de imagem
  const imageInput = el('input', null, { type: 'file', accept: 'image/png,image/jpeg' });
  imageInput.style.display = 'none';
  body.append(imageInput);

  // ---- Carrega bitmap da página
  let bmp;
  try { bmp = await loadBitmapCSPSafe(bitmap); }
  catch (e) { console.error('[page-editor] loadBitmapCSPSafe falhou:', e); alert('Falha ao abrir a página no editor.'); overlay.remove(); return; }

  // ---- Fit inicial
  const baseW = bmp.width, baseH = bmp.height;
  const fitScale = Math.min(MAX_VIEW_W / baseW, MAX_VIEW_H / baseH, 1);
  const viewW = Math.floor(baseW * fitScale);
  const viewH = Math.floor(baseH * fitScale);
  canvas.style.width = `${viewW}px`;
  canvas.style.height = `${viewH}px`;

  const st = createState(canvas, bmp, viewRotation);
  st.w = viewW; st.h = viewH;
  st._buttons = { btnErs, btnTxt, btnPan, btnImg, btnZOut, btnZFit, btnZIn, btnUndo, btnRedo, btnClear, btnCancel, btnSave, btnRotateL, btnRotateR };
  st._imageInput = imageInput;

  // ---------- helpers ----------
  const addL = (target, type, fn, opts) => { target.addEventListener(type, fn, opts); st._listeners.push([target, type, fn, opts]); };
  const closeModal = () => {
    closeInlineTextEditor(st);
    st._listeners.forEach(([t, ty, fn, op]) => t.removeEventListener(ty, fn, op));
    st._listeners.length = 0;
    try { if ('close' in bmp && typeof bmp.close === 'function') bmp.close(); } catch {}
    overlay.remove();
  };

  /* ========== Pointer & Mouse ========== */
  const onPointerMove = (ev) => {
    const [vx, vy] = screenToView(st, ev.clientX, ev.clientY);
    st.hover = findHover(st, vx, vy);

    if (!st.pointer.active) { drawScene(st); return; }

    if (st.pointer.mode === 'pan') {
      st.panX += ev.movementX;
      st.panY += ev.movementY;
      drawScene(st); return;
    }

    if (st.pointer.mode === 'erase-create') {
      const [x0, y0] = [st.pointer.start.x, st.pointer.start.y];
      st.selRect = normRect(x0, y0, vx, vy);
      drawScene(st); return;
    }

    if (st.pointer.mode === 'rect-move' && st.pointer.idx > -1) {
      const r = st.rects[st.pointer.idx];
      const [rx0, ry0, rx1, ry1] = r.viewPx;
      const w = rx1 - rx0, h = ry1 - ry0;

      let nx0 = clamp(vx - st.pointer.dx, 0, st.w - w);
      let ny0 = clamp(vy - st.pointer.dy, 0, st.h - h);
      r.viewPx = [nx0, ny0, nx0 + w, ny0 + h];
      r.viewNorm = [nx0/st.w, ny0/st.h, (nx0+w)/st.w, (ny0+h)/st.h];
      drawScene(st); return;
    }

    if (st.pointer.mode === 'rect-resize' && st.pointer.idx > -1) {
      const r = st.rects[st.pointer.idx];
      let [x0, y0, x1, y1] = r.viewPx;
      const min = 4;
      const h = st.pointer.handle; // 0..7
      if (h === 0 || h === 7 || h === 3) x0 = clamp(vx, 0, x1 - min);
      if (h === 1 || h === 2 || h === 5) x1 = clamp(vx, x0 + min, st.w);
      if (h === 0 || h === 1 || h === 4) y0 = clamp(vy, 0, y1 - min);
      if (h === 2 || h === 3 || h === 6) y1 = clamp(vy, y0 + min, st.h);
      r.viewPx = [x0, y0, x1, y1];
      r.viewNorm = [x0/st.w, y0/st.h, x1/st.w, y1/st.h];
      drawScene(st); return;
    }

    // ======== TEXT ========
    if (st.pointer.mode === 'text-create') {
      const [x0, y0] = [st.pointer.start.x, st.pointer.start.y];
      st.selRect = normRect(x0, y0, vx, vy);
      drawScene(st); return;
    }

    if (st.pointer.mode === 'text-drag' && st.pointer.idx > -1) {
      const t = st.texts[st.pointer.idx];
      measureTextBox(st, t);
      t.view.x = clamp(vx - st.pointer.dx, 0, st.w - t.view.width);
      t.view.y = clamp(vy - st.pointer.dy, 0, st.h - t.view.height);
      drawScene(st); return;
    }

    if (st.pointer.mode === 'text-resize' && st.pointer.idx > -1) {
      const t = st.texts[st.pointer.idx];
      const h = st.pointer.handle; // 0..7
      const s = t._rs;

      // lados L/R mudam a largura (reflow)
      if (h === 5) { // R
        const deltaX = vx - s.x1;
        t.view.boxW = clamp(s.boxW + deltaX, TEXT_MIN_W, st.w * TEXT_MAX_W_RATIO);
      } else if (h === 7) { // L
        const deltaX = vx - s.x;
        t.view.x = clamp(s.x + deltaX, 0, st.w - TEXT_MIN_W);
        t.view.boxW = clamp(s.boxW - deltaX, TEXT_MIN_W, st.w * TEXT_MAX_W_RATIO);
      } else {
        // topo/base/cantos -> escalar fonte (mantém largura inicial)
        const baseTL = { x: s.x, y: s.y };
        const startBR = { x: s.x + s.width, y: s.y + s.height };
        const d0 = Math.hypot(startBR.x - baseTL.x, startBR.y - baseTL.y);
        const d1 = Math.hypot(vx - baseTL.x, vy - baseTL.y);
        const factor = clamp(d1 / Math.max(1, d0), 0.25, 4);
        t.view.size = clamp(s.size * factor, TEXT_MIN_SIZE, TEXT_MAX_SIZE);

        // se mexeu no topo, reposiciona y para "crescer para cima"
        if (h === 0 || h === 1 || h === 4) {
          const newH = (t._lines?.length || 1) * t.view.size * 1.2;
          t.view.y = clamp(vy, 0, st.h - newH);
        }
      }

      measureTextBox(st, t);
      // mantém dentro da página
      t.view.x = clamp(t.view.x, 0, st.w - t.view.width);
      t.view.y = clamp(t.view.y, 0, st.h - t.view.height);

      drawScene(st); return;
    }

    // ======== IMAGE ========
    if (st.pointer.mode === 'image-drag' && st.pointer.idx > -1) {
      const im = st.images[st.pointer.idx];
      const { x0, y0, x1, y1 } = im.view;
      const w = x1 - x0, h = y1 - y0;
      let nx0 = clamp(vx - st.pointer.dx, 0, st.w - w);
      let ny0 = clamp(vy - st.pointer.dy, 0, st.h - h);
      im.view.x0 = nx0; im.view.y0 = ny0; im.view.x1 = nx0 + w; im.view.y1 = ny0 + h;
      im.viewNorm.x0 = im.view.x0 / st.w; im.viewNorm.y0 = im.view.y0 / st.h;
      im.viewNorm.x1 = im.view.x1 / st.w; im.viewNorm.y1 = im.view.y1 / st.h;
      drawScene(st); return;
    }

    drawScene(st);
  };

  const onPointerDown = (ev) => {
    const isMiddle = (ev.button === 1);
    const [vx, vy] = screenToView(st, ev.clientX, ev.clientY);
    st.pointer.active = true;
    st.pointer.id = ev.pointerId;
    st.canvas.setPointerCapture(ev.pointerId);

    if (st.tool === 'pan' || isMiddle) {
      st.pointer.mode = 'pan';
      return;
    }

    const hover = findHover(st, vx, vy);

    // ======== TEXT ========
    if (st.tool === 'text') {
      if (hover.type === 'text') {
        const t = st.texts[hover.idx];
        measureTextBox(st, t);
        st._selectedText = hover.idx;

        if (Number.isInteger(hover.handle)) {
          // redimensionar por alça (0..7)
          st.pointer.mode = 'text-resize';
          st.pointer.idx = hover.idx;
          st.pointer.handle = hover.handle;
          t._rs = {
            x: t.view.x, y: t.view.y,
            width: t.view.width, height: t.view.height,
            boxW: t.view.boxW, size: t.view.size,
            x1: t.view.x + t.view.width, y1: t.view.y + t.view.height
          };
          drawScene(st);
          return;
        }
        // mover
        st.pointer.mode = 'text-drag';
        st.pointer.idx = hover.idx;
        st.pointer.dx = vx - t.view.x;
        st.pointer.dy = vy - t.view.y;
        drawScene(st);
        return;
      }

      // arrastar para criar
      st.pointer.mode = 'text-create';
      st.pointer.start = { x: vx, y: vy };
      st.selRect = [vx, vy, vx, vy];
      drawScene(st);
      return;
    }

    // ======== ERASE (whiteout) ========
    if (st.tool === 'erase') {
      if (hover.type === 'rect') {
        pushUndo(st);
        st._selectedRect = hover.idx;
        if (hover.handle && hover.handle.startsWith('rect-')) {
          st.pointer.mode = 'rect-resize';
          st.pointer.idx = hover.idx;
          st.pointer.handle = parseInt(hover.handle.split('-')[1], 10);
          return;
        }
        st.pointer.mode = 'rect-move';
        st.pointer.idx = hover.idx;
        const [x0, y0] = st.rects[hover.idx].viewPx;
        st.pointer.dx = vx - x0;
        st.pointer.dy = vy - y0;
        return;
      }
      st.pointer.mode = 'erase-create';
      st.pointer.start = { x: vx, y: vy };
      st.selRect = [vx, vy, vx, vy];
      drawScene(st);
      return;
    }

    // ======== IMAGE ========
    if (st.tool === 'image') {
      if (hover.type === 'image') {
        const im = st.images[hover.idx];
        st._selectedImage = hover.idx;
        st.pointer.mode = 'image-drag';
        st.pointer.idx = hover.idx;
        st.pointer.dx = vx - im.view.x0;
        st.pointer.dy = vy - im.view.y0;
        drawScene(st);
        return;
      }
      st._selectedImage = -1;
      st._imageInput.value = '';
      st._imageInput.click();
      return;
    }
  };

  const onPointerUp = (ev) => {
    if (!st.pointer.active) return;

    if (st.pointer.mode === 'erase-create' && st.selRect) {
      const [x0, y0, x1, y1] = st.selRect;
      const [X0, Y0, X1, Y1] = normRect(x0, y0, x1, y1);
      st.selRect = null;
      if (Math.abs(X1 - X0) > 3 && Math.abs(Y1 - Y0) > 3) {
        pushUndo(st);
        st.rects.push({
          viewPx: [X0, Y0, X1, Y1],
          viewNorm: [X0/st.w, Y0/st.h, X1/st.w, Y1/st.h]
        });
        st._selectedRect = st.rects.length - 1;
      }
      drawScene(st);
    }

    if (st.pointer.mode === 'text-create' && st.selRect) {
      const [x0, y0, x1, y1] = st.selRect;
      const [X0, Y0, X1, Y1] = normRect(x0, y0, x1, y1);
      st.selRect = null;
      if (Math.abs(X1 - X0) > 3 && Math.abs(Y1 - Y0) > 3) {
        pushUndo(st);
        const initSize = clamp((Y1 - Y0) * 0.6, TEXT_MIN_SIZE, TEXT_MAX_SIZE);
        const t = {
          view: { x: X0, y: Y0, size: initSize, boxW: (X1 - X0), text: '', width: 0, height: 0 },
          viewNorm: { x: X0 / st.w, y: Y0 / st.h, sizeRel: initSize / st.h }
        };
        measureTextBox(st, t);
        st.texts.push(t);
        st._selectedText = st.texts.length - 1;
        // abre editor de texto para digitação
        openInlineTextEditor(st, t.view.x, t.view.y, st._selectedText);
      }
      drawScene(st);
    }

    if ((st.pointer.mode === 'text-drag' || st.pointer.mode === 'text-resize') && st.pointer.idx > -1) {
      const t = st.texts[st.pointer.idx];
      measureTextBox(st, t);
      t.viewNorm.x = t.view.x / st.w;
      t.viewNorm.y = t.view.y / st.h;
      t.viewNorm.sizeRel = t.view.size / st.h;
      delete t._rs;
    }

    if (st.pointer.mode === 'rect-resize' && st.pointer.idx > -1) {
      const r = st.rects[st.pointer.idx];
      r.viewNorm = [r.viewPx[0]/st.w, r.viewPx[1]/st.h, r.viewPx[2]/st.w, r.viewPx[3]/st.h];
    }

    st.pointer = { active:false, mode:null, idx:-1, handle:null, id:null, start:{x:0,y:0}, dx:0, dy:0 };
    try { st.canvas.releasePointerCapture(ev.pointerId); } catch {}
    drawScene(st);
  };

  // Duplo-clique: criar/editar texto
  const onDblClick = (ev) => {
    const [vx, vy] = screenToView(st, ev.clientX, ev.clientY);
    const h = findHover(st, vx, vy);
    if (h.type === 'text') {
      const t = st.texts[h.idx];
      openInlineTextEditor(st, t.view.x, t.view.y, h.idx);
    } else if (st.tool === 'text') {
      openInlineTextEditor(st, vx, vy, null);
    }
  };

  // Roda do mouse:
  // - sem Ctrl sobre IMAGEM => escala imagem
  // - sem Ctrl sobre TEXTO  => fonte (Shift = largura)
  // - sem Ctrl sobre RETÂNGULO => escala retângulo
  // - com Ctrl => zoom da página
  const onWheel = (ev) => {
    const [vx, vy] = screenToView(st, ev.clientX, ev.clientY);
    const over = findHover(st, vx, vy);
    if (!ev.ctrlKey && over.type === 'image') {
      ev.preventDefault();
      const idx = over.idx;
      const im = st.images[idx];
      const cx = (im.view.x0 + im.view.x1) / 2;
      const cy = (im.view.y0 + im.view.y1) / 2;
      const factor = Math.sign(-ev.deltaY) > 0 ? 1.12 : (1/1.12);
      const w = (im.view.x1 - im.view.x0) * factor;
      const h = (im.view.y1 - im.view.y0) * factor;
      pushUndo(st);
      im.view.x0 = clamp(cx - w/2, 0, st.w);
      im.view.y0 = clamp(cy - h/2, 0, st.h);
      im.view.x1 = clamp(cx + w/2, 0, st.w);
      im.view.y1 = clamp(cy + h/2, 0, st.h);
      im.viewNorm.x0 = im.view.x0 / st.w; im.viewNorm.y0 = im.view.y0 / st.h;
      im.viewNorm.x1 = im.view.x1 / st.w; im.viewNorm.y1 = im.view.y1 / st.h;
      st._selectedImage = idx;
      drawScene(st);
      return;
    }
    if (!ev.ctrlKey && over.type === 'text') {
      ev.preventDefault();
      const t = st.texts[over.idx];
      if (ev.shiftKey) {
        const f = Math.sign(-ev.deltaY) > 0 ? 1.10 : (1/1.10);
        t.view.boxW = clamp(t.view.boxW * f, TEXT_MIN_W, st.w * TEXT_MAX_W_RATIO);
      } else {
        const f = Math.sign(-ev.deltaY) > 0 ? 1.08 : (1/1.08);
        t.view.size = clamp(t.view.size * f, TEXT_MIN_SIZE, TEXT_MAX_SIZE);
      }
      measureTextBox(st, t);
      // mantém cache coerente (opcional)
      t.viewNorm = t.viewNorm || {};
      t.viewNorm.sizeRel = t.view.size / st.h;

      st._selectedText = over.idx;
      drawScene(st);
      return;
    }
    if (!ev.ctrlKey && over.type === 'rect') {
      ev.preventDefault();
      const r = st.rects[over.idx];
      const [x0, y0, x1, y1] = r.viewPx;
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      const factor = Math.sign(-ev.deltaY) > 0 ? 1.08 : (1/1.08);
      const nw = (x1 - x0) * factor, nh = (y1 - y0) * factor;
      pushUndo(st);
      r.viewPx = [clamp(cx - nw/2, 0, st.w), clamp(cy - nh/2, 0, st.h),
                  clamp(cx + nw/2, 0, st.w), clamp(cy + nh/2, 0, st.h)];
      r.viewNorm = [r.viewPx[0]/st.w, r.viewPx[1]/st.h, r.viewPx[2]/st.w, r.viewPx[3]/st.h];
      st._selectedRect = over.idx;
      drawScene(st);
      return;
    }
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    const factor = Math.sign(-ev.deltaY) > 0 ? 1.15 : 1/1.15;
    const rect = st.canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    const old = st.zoom;
    st.zoom = clamp(st.zoom * factor, 0.2, 6);
    st.panX = mx - (mx - st.panX) * (st.zoom / old);
    st.panY = my - (my - st.panY) * (st.zoom / old);
    drawScene(st);
  };

  const keyHandler = (ev) => {
    if (!ev.ctrlKey && !ev.metaKey) {
      const k = ev.key.toLowerCase();
      if (k === 'e') setTool(st, 'erase');
      if (k === 't') setTool(st, 'text');
      if (k === 'h') setTool(st, 'pan');
      if (k === 'i') setTool(st, 'image');
      if (k === '[' && st._selectedText >= 0) {
        const t = st.texts[st._selectedText];
        t.view.boxW = clamp(t.view.boxW / 1.1, TEXT_MIN_W, st.w * TEXT_MAX_W_RATIO);
        measureTextBox(st, t); drawScene(st);
      }
      if (k === ']' && st._selectedText >= 0) {
        const t = st.texts[st._selectedText];
        t.view.boxW = clamp(t.view.boxW * 1.1, TEXT_MIN_W, st.w * TEXT_MAX_W_RATIO);
        measureTextBox(st, t); drawScene(st);
      }
      if (ev.key === 'Escape') { ev.preventDefault(); closeModal(); return; }
      if (ev.key === 'Delete' || ev.key === 'Backspace') {
        if (st.hover.type === 'rect') { pushUndo(st); st.rects.splice(st.hover.idx, 1); st._selectedRect = -1; drawScene(st); return; }
        if (st.hover.type === 'text') { pushUndo(st); st.texts.splice(st.hover.idx, 1); st._selectedText = -1; drawScene(st); return; }
        if (st.hover.type === 'image') { pushUndo(st); st.images.splice(st.hover.idx, 1); st._selectedImage = -1; drawScene(st); return; }
      }
    }
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === '+' || ev.key === '=')) { ev.preventDefault(); st.zoom = Math.min(6, st.zoom * 1.15); drawScene(st); }
    if ((ev.ctrlKey || ev.metaKey) && ev.key === '-') { ev.preventDefault(); st.zoom = Math.max(0.2, st.zoom / 1.15); drawScene(st); }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === '0') { ev.preventDefault(); st.zoom = 1; st.panX = 0; st.panY = 0; drawScene(st); }
    if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && ev.key.toLowerCase() === 'z') { ev.preventDefault(); doUndo(st); drawScene(st); }
    if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && ev.key.toLowerCase() === 'z') { ev.preventDefault(); doRedo(st); drawScene(st); }
  };

  /* ---------- BINDS ---------- */
  addL(st.canvas, 'pointermove', onPointerMove);
  addL(st.canvas, 'pointerdown', onPointerDown);
  addL(st.canvas, 'pointerup', onPointerUp);
  addL(st.canvas, 'dblclick', onDblClick);
  addL(st.canvas, 'wheel', onWheel, { passive:false });
  addL(window, 'keydown', keyHandler, true);

  // botões
  addL(btnErs, 'click', () => { closeInlineTextEditor(st); setTool(st, 'erase'); });
  addL(btnTxt, 'click', () => { setTool(st, 'text'); });
  addL(btnImg, 'click', () => { setTool(st, 'image'); st._selectedImage = -1; st._imageInput.value=''; st._imageInput.click(); });
  addL(btnPan, 'click', () => { closeInlineTextEditor(st); setTool(st, 'pan'); });
  addL(btnZFit, 'click', () => { st.zoom = 1; st.panX = 0; st.panY = 0; drawScene(st); });
  addL(btnZIn,  'click', () => { st.zoom = Math.min(6, st.zoom * 1.15); drawScene(st); });
  addL(btnZOut, 'click', () => { st.zoom = Math.max(0.2, st.zoom / 1.15); drawScene(st); });
  addL(btnUndo, 'click', () => { doUndo(st); drawScene(st); });
  addL(btnRedo, 'click', () => { doRedo(st); drawScene(st); });
  addL(btnClear, 'click', () => {
    if (!st.rects.length && !st.texts.length && !st.images.length) return;
    pushUndo(st); st.rects = []; st.texts = []; st.images = [];
    st._selectedImage = -1; st._selectedText = -1; st._selectedRect = -1; drawScene(st);
    try { document.dispatchEvent(new CustomEvent('gv:editor:cropCleared')); } catch {}
  });
  addL(btnCancel, 'click', closeModal);
  addL(btnClose,  'click', closeModal);

  // Rotação de imagem selecionada
  const rotateSel = (delta) => {
    const idx = st._selectedImage;
    if (idx < 0) return;
    pushUndo(st);
    const im = st.images[idx];
    im.view.rotate = ((im.view.rotate || 0) + delta + 360) % 360;
    im.viewNorm.rotate = im.view.rotate;
    drawScene(st);
  };
  addL(btnRotateL, 'click', () => rotateSel(-90));
  addL(btnRotateR, 'click', () => rotateSel(+90));

  // Upload de imagem
  addL(imageInput, 'change', async () => {
    const file = imageInput.files && imageInput.files[0];
    if (!file) return;
    try {
      const bmp = await loadBitmapCSPSafe(file);
      const up = await uploadOverlayImage(file, sessionId);
      if (!up?.ok) throw new Error('Upload falhou.');
      pushUndo(st);
      const targetW = Math.max(24, st.w * 0.2);
      const ratio = bmp.width > 0 ? (bmp.height / bmp.width) : 1;
      const targetH = targetW * ratio;
      const cx = st.w * 0.5, cy = st.h * 0.5;
      const x0 = clamp(cx - targetW/2, 0, st.w), y0 = clamp(cy - targetH/2, 0, st.h);
      const x1 = clamp(cx + targetW/2, 0, st.w), y1 = clamp(cy + targetH/2, 0, st.h);

      st.images.push({
        imageId: up.image_id, bmp,
        view: { x0, y0, x1, y1, rotate: 0 },
        viewNorm: { x0: x0/st.w, y0: y0/st.h, x1: x1/st.w, y1: y1/st.h, rotate: 0 }
      });
      st._selectedImage = st.images.length - 1;
      setTool(st, 'image');
      drawScene(st);
    } catch (e) {
      console.error('[page-editor] upload image:', e);
      alert('Falha ao enviar imagem: ' + (e?.message || 'desconhecido'));
    }
  });

  /* ================= Salvar ================= */
  async function postVariant(fields, mode /* 'urlenc' | 'form' */) {
    const headers = { 'Accept':'application/json', 'X-CSRFToken': getCSRFToken() };
    let body;
    if (mode === 'urlenc') {
      body = new URLSearchParams(fields);
      headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8';
    } else {
      body = new FormData();
      Object.entries(fields).forEach(([k, v]) => body.append(k, v));
    }
    const resp = await fetch(OVERLAY_ENDPOINT, { method: 'POST', headers, credentials: 'same-origin', cache: 'no-store', body });
    const data = await resp.json().catch(()=> ({}));
    if (!resp.ok) throw new Error(data?.error || `${resp.status} ${resp.statusText}`);
    return data;
  }

  addL(btnSave, 'click', async () => {
    try {
      const mapRect = (r) => {
        const [nx0, ny0, nx1, ny1] = r.viewNorm;
        const [bx0, by0] = mapViewToBase(nx0, ny0, st.viewRotation);
        const [bx1, by1] = mapViewToBase(nx1, ny1, st.viewRotation);
        const [X0, Y0, X1, Y1] = normRect(bx0, by0, bx1, by1);
        return { x0: X0, y0: Y0, x1: X1, y1: Y1 };
      };

      // Whiteout que deve ser APLICADO/ACHATADO pelo backend
      const whiteouts = st.rects.map(mapRect);

      // TEXTO — envia bounding box normalizada + âncora top-left
      const texts = st.texts.map(t => {
        // canto superior esquerdo e inferior direito na VIEW
        const x0v = clamp(t.view.x / st.w, 0, 1);
        const y0v = clamp(t.view.y / st.h, 0, 1);
        const x1v = clamp((t.view.x + t.view.width) / st.w, 0, 1);
        const y1v = clamp((t.view.y + t.view.height) / st.h, 0, 1);
        // mapeia para base 0°
        const [bx0, by0] = mapViewToBase(x0v, y0v, st.viewRotation);
        const [bx1, by1] = mapViewToBase(x1v, y1v, st.viewRotation);
        const [X0, Y0, X1, Y1] = normRect(bx0, by0, bx1, by1);

        // tamanho relativo da fonte: usa dimensão vertical da base
        const baseDen = (Math.abs((st.viewRotation % 360)) === 90 || Math.abs((st.viewRotation % 360)) === 270) ? st.w : st.h;
        const sizeRel = clamp(t.view.size / baseDen, 0.001, 1);

        const txtOut = (t._lines && t._lines.length) ? t._lines.join('\n') : (t.view.text || '');

        return {
          // API moderna
          x0: X0, y0: Y0, x1: X1, y1: Y1,
          x: X0, y: Y0,                      // compat c/ APIs que esperam (x,y)
          box_w_rel: (X1 - X0),              // largura normalizada
          size_rel: sizeRel,
          anchor: 'top-left',
          text: txtOut
        };
      });

      const images = st.images.map(im => {
        const nx0 = im.viewNorm.x0, ny0 = im.viewNorm.y0, nx1 = im.viewNorm.x1, ny1 = im.viewNorm.y1;
        const [bx0, by0] = mapViewToBase(nx0, ny0, st.viewRotation);
        const [bx1, by1] = mapViewToBase(nx1, ny1, st.viewRotation);
        const [X0, Y0, X1, Y1] = normRect(bx0, by0, bx1, by1);
        return { image_id: im.imageId, x0: X0, y0: Y0, x1: X1, y1: Y1, rotate: im.view.rotate || 0 };
      });

      if (!whiteouts.length && !texts.length && !images.length) { alert('Nada para salvar.'); return; }

      const opsV2 = {
        whiteouts,
        texts,
        images,
        options: { flatten: true, color: '#FFFFFF', alpha: 1, text_anchor: 'top-left' }
      };
      const opsJSON = JSON.stringify(opsV2);

      // Tentativas compat com backends anteriores (variações de nomes)
      const tries = [
        { mode:'urlenc', fields: { action:'apply_overlays', session_id: String(sessionId), page_index: String(pageIndex), operations: opsJSON } },
        { mode:'urlenc', fields: { action:'apply',          session_id: String(sessionId), page_idx:   String(pageIndex), ops:        opsJSON } },
        { mode:'form',   fields: { action:'apply_overlays', session_id: String(sessionId), page_number:String(pageIndex+1), operations: opsJSON } },
        { mode:'form',   fields: { action:'apply',          session_id: String(sessionId), page_index: String(pageIndex),    operations: opsJSON } },
      ];

      btnSave.disabled = true;

      let lastErr = '';
      for (const t of tries) {
        try {
          await postVariant(t.fields, t.mode);
          try { document.dispatchEvent(new CustomEvent('page-editor:saved', { detail: { session_id: sessionId, page_index: pageIndex, ts: Date.now() } })); } catch {}
          closeModal();
          return;
        } catch (e) {
          lastErr = e?.message || String(e);
          console.warn('[page-editor] tentativa falhou:', lastErr);
        }
      }
      throw new Error(lastErr || 'Falha ao aplicar overlays.');
    } catch (e) {
      console.error('[page-editor] salvar falhou:', e);
      alert('Erro ao salvar: ' + (e?.message || 'desconhecido'));
    } finally {
      btnSave.disabled = false;
    }
  });

  // Primeira render + bloqueio do scroll de fundo
  st.zoom = 1; st.panX = 0; st.panY = 0;
  drawScene(st);
  addL(overlay, 'wheel', (e) => e.preventDefault(), { passive:false });

  // Ferramenta inicial: usa a última ou Texto por padrão
  let initialTool = 'text';
  try { initialTool = sessionStorage.getItem(LAST_TOOL_KEY) || 'text'; } catch {}
  setTool(st, initialTool);
}

// default export + fallback global
export default openPageEditor;
try { window.GV_OPEN_PAGE_EDITOR = openPageEditor; } catch {}
// ==== Chrome do Page Editor: ajusta CSS vars e bloqueia scroll do body ====
(() => {
  const SEL_ROOT = '.pe-root.is-open, .pemodal.is-open, .pe-overlay-backdrop.is-open';

  function applyVars(root) {
    try {
      const dialog = root.querySelector('.pe-dialog, .pemodal__dialog');
      const header = dialog?.querySelector('.pe-header, .pe-topbar');
      if (!dialog || !header) return;
      const h = header.getBoundingClientRect().height || 56;
      dialog.style.setProperty('--pe-header-h', `${Math.round(h)}px`);
    } catch (_) {}
  }

  function onOpen(root) {
    document.body.classList.add('gv-modal-open');
    applyVars(root);
  }
  function onClose() {
    document.body.classList.remove('gv-modal-open');
  }

  // Observa quando o modal abre/fecha (não depende de eventos custom)
  const mo = new MutationObserver(() => {
    const openRoot = document.querySelector(SEL_ROOT);
    if (openRoot) {
      onOpen(openRoot);
    } else {
      onClose();
    }
  });
  mo.observe(document.documentElement, { childList: true, subtree: true, attributes: true });

  // Recalcula em resize (debounce simples)
  let t;
  window.addEventListener('resize', () => {
    clearTimeout(t);
    t = setTimeout(() => {
      const openRoot = document.querySelector(SEL_ROOT);
      if (openRoot) applyVars(openRoot);
    }, 80);
  }, { passive: true });
})();