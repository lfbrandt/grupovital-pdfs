// app/static/js/page-editor.js
// Editor de página (whiteout/redact + texto) com zoom/pan e CSP-friendly.
// - Não usa blob: em <img>; usa createImageBitmap(blob) ou Data URL em canvas.
// - Integra com /api/edit/apply/overlays (POST JSON, CSRF).
// - Mantém eventos: gv:editor:cropCleared e page-editor:saved.
// - Exporta named + default e ainda define window.GV_OPEN_PAGE_EDITOR.

'use strict';

import { getCSRFToken } from './utils.js';

/* ================================================================
   Config
================================================================ */
const OVERLAY_ENDPOINT = '/api/edit/apply/overlays'; // back aplica redacts/textos
const MAX_VIEW_W = 1400;
const MAX_VIEW_H = 900;

/* ================================================================
   Utils DOM
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
   CSP-safe bitmap loader
   - Prefere createImageBitmap(blob) -> pode desenhar direto no canvas
   - Fallback: DataURL -> <img> -> drawImage
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
    im.src = String(dataUrl); // permitido por CSP (img-src 'self' data:)
  });
  // normaliza para uma interface parecida
  return {
    width: img.naturalWidth || img.width,
    height: img.naturalHeight || img.height,
    _img: img
  };
}

/* ================================================================
   Rotação: view -> base
   Recebemos a imagem já rotacionada como a VIEW (pelo preview.js).
   Precisamos mapear as coordenadas normalizadas para o PDF base.
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
   Editor state + desenho
================================================================ */
function createState(canvas, bmp, viewRotation) {
  const st = {
    canvas,
    ctx: canvas.getContext('2d', { alpha: false }),
    w: canvas.clientWidth,
    h: canvas.clientHeight,
    bmp,
    viewRotation,

    // viewport (zoom/pan) em coordenadas da view (px)
    zoom: 1,
    panX: 0,
    panY: 0,
    draggingViewport: false,

    // overlays
    tool: 'redact', // 'redact' | 'text' | 'pan'
    rects: [],      // { viewPx:[x0,y0,x1,y1], viewNorm:[..] }
    texts: [],      // { view:{x,y,size,text,width,height}, viewNorm:{x,y,sizeRel} }

    // interação overlays
    hover: { type:null, idx:-1, handle:null },
    dragging: { type:null, idx:-1, mode:null, dx:0, dy:0 },

    // seleção de novo retângulo
    selRect: null,  // [x0,y0,x1,y1] em px da view (antes de zoom/pan transform)

    // undo/redo
    undoStack: [],
    redoStack: []
  };
  return st;
}

function pushUndo(st) {
  st.redoStack.length = 0;
  const snap = {
    rects: st.rects.map(r => ({ viewPx: r.viewPx.slice(), viewNorm: r.viewNorm.slice() })),
    texts: st.texts.map(t => ({
      view: { ...t.view }, viewNorm: { ...t.viewNorm }, text: t.view.text
    }))
  };
  st.undoStack.push(snap);
  if (st.undoStack.length > 50) st.undoStack.shift();
}
function doUndo(st) {
  const last = st.undoStack.pop();
  if (!last) return;
  const cur = {
    rects: st.rects.map(r => ({ viewPx: r.viewPx.slice(), viewNorm: r.viewNorm.slice() })),
    texts: st.texts.map(t => ({ view: { ...t.view }, viewNorm: { ...t.viewNorm }, text: t.view.text }))
  };
  st.redoStack.push(cur);
  st.rects = last.rects.map(r => ({ viewPx: r.viewPx.slice(), viewNorm: r.viewNorm.slice() }));
  st.texts = last.texts.map(t => ({
    view: { ...t.view }, viewNorm: { ...t.viewNorm }
  }));
}
function doRedo(st) {
  const last = st.redoStack.pop();
  if (!last) return;
  pushUndo(st);
  st.rects = last.rects.map(r => ({ viewPx: r.viewPx.slice(), viewNorm: r.viewNorm.slice() }));
  st.texts = last.texts.map(t => ({ view: { ...t.view }, viewNorm: { ...t.viewNorm } }));
}

// transforma ponto/retângulo da tela (mouse) -> coordenadas da view (antes de zoom/pan)
function screenToView(st, sx, sy) {
  const r = st.canvas.getBoundingClientRect();
  const x = (sx - r.left);
  const y = (sy - r.top);
  // aplica inverso de zoom/pan
  const vx = (x - st.panX) / st.zoom;
  const vy = (y - st.panY) / st.zoom;
  return [clamp(vx, 0, st.w), clamp(vy, 0, st.h)];
}
function viewToScreen(st, vx, vy) {
  const x = vx * st.zoom + st.panX;
  const y = vy * st.zoom + st.panY;
  return [x, y];
}

function drawScene(st) {
  const { ctx, canvas, w, h, bmp } = st;

  // DPR
  const scale = dpr();
  canvas.width = Math.floor(canvas.clientWidth * scale);
  canvas.height = Math.floor(canvas.clientHeight * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0);

  // fundo
  ctx.fillStyle = '#1a1a1a';
  ctx.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);

  // aplica zoom/pan
  ctx.save();
  ctx.translate(st.panX, st.panY);
  ctx.scale(st.zoom, st.zoom);

  // base
  if ('close' in bmp && typeof bmp.close === 'function') {
    ctx.drawImage(bmp, 0, 0, w, h);
  } else {
    ctx.drawImage(bmp._img, 0, 0, w, h);
  }

  // redacts
  st.rects.forEach((r, i) => {
    const [x0, y0, x1, y1] = r.viewPx;
    ctx.save();
    ctx.globalAlpha = 0.9;
    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
    ctx.restore();

    ctx.save();
    ctx.lineWidth = 1 / st.zoom;
    ctx.strokeStyle = (st.hover.type === 'rect' && st.hover.idx === i) ? '#00B27A' : '#3a3a3a';
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, (x1 - x0) - 1, (y1 - y0) - 1);
    ctx.restore();
  });

  // textos
  st.texts.forEach((t, i) => {
    const { x, y, size, text, width, height } = t.view;
    ctx.save();
    ctx.font = `${Math.max(10, size)}px sans-serif`;
    ctx.fillStyle = '#000';
    ctx.textBaseline = 'top';
    ctx.fillText(text, x, y);
    ctx.restore();

    if (st.hover.type === 'text' && st.hover.idx === i) {
      ctx.save();
      ctx.setLineDash([6 / st.zoom, 6 / st.zoom]);
      ctx.strokeStyle = '#00B27A';
      ctx.strokeRect(x + 0.5, y + 0.5, width, height);
      ctx.restore();
    }
  });

  // guia seleção
  if (st.selRect) {
    const [x0, y0, x1, y1] = st.selRect;
    ctx.save();
    ctx.setLineDash([6 / st.zoom, 6 / st.zoom]);
    ctx.strokeStyle = '#00B27A';
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, (x1 - x0), (y1 - y0));
    ctx.restore();
  }

  ctx.restore();
}

function measureTextBox(st, t) {
  const { ctx } = st;
  ctx.save();
  ctx.font = `${Math.max(10, t.view.size)}px sans-serif`;
  const m = ctx.measureText(t.view.text || '');
  t.view.width  = Math.max(4, m.width);
  t.view.height = Math.max(12, t.view.size * 1.3);
  ctx.restore();
}

function findHover(st, vx, vy) {
  // procura texto por cima
  for (let i = st.texts.length - 1; i >= 0; i--) {
    const t = st.texts[i];
    measureTextBox(st, t);
    const ok = (vx >= t.view.x && vx <= t.view.x + t.view.width && vy >= t.view.y && vy <= t.view.y + t.view.height);
    if (ok) return { type:'text', idx:i, handle:null };
  }
  // procura redact
  for (let i = st.rects.length - 1; i >= 0; i--) {
    const r = st.rects[i];
    const [x0, y0, x1, y1] = r.viewPx;
    if (vx >= x0 && vx <= x1 && vy >= y0 && vy <= y1) return { type:'rect', idx:i, handle:null };
  }
  return { type:null, idx:-1, handle:null };
}

/* ================================================================
   Editor principal
================================================================ */
export async function openPageEditor(opts) {
  const {
    bitmap,            // Blob PNG rotacionado igual à VIEW
    sessionId,
    pageIndex,         // 0-based
    pdfPageSize = null,
    getBitmap = null,  // (needScale:number)=>Promise<Blob>
    viewRotation = 0
  } = opts || {};

  if (!bitmap || !sessionId || typeof pageIndex !== 'number') {
    alert('Editor indisponível: parâmetros insuficientes.');
    return;
  }

  // ---- Estrutura do modal (sem inline)
  const overlay = el('div', ['pe-overlay','modal-overlay'], { role:'dialog', 'aria-modal':'true' });
  const modal   = el('div', ['pe-modal','modal']);
  const header  = el('div', ['pe-header','modal-header']);
  const title   = el('div', 'pe-title'); title.textContent = 'Editor de página';
  const btnClose= el('button', ['pe-btn','modal-close'], { type:'button', 'aria-label':'Fechar' }); btnClose.textContent = '×';

  // toolbar
  const toolbar = el('div', ['pe-toolbar','wizard-toolbar']);
  const btnRed  = el('button', ['pe-btn','pe-tool','is-active'], { type:'button', title: 'Tapar/whiteout (R)' }); btnRed.textContent = 'Tapar';
  const btnTxt  = el('button', ['pe-btn','pe-tool'], { type:'button', title: 'Texto (T)' }); btnTxt.textContent = 'Texto';
  const btnPan  = el('button', ['pe-btn','pe-tool'], { type:'button', title: 'Mover tela (H)' }); btnPan.textContent = 'Mover';
  const sep1    = el('div', 'pe-spacer-sm');
  const btnZOut = el('button', ['pe-btn'], { type:'button', title:'Zoom - (Ctrl -)' }); btnZOut.textContent = '−';
  const btnZFit = el('button', ['pe-btn'], { type:'button', title:'Ajustar' }); btnZFit.textContent = 'Ajustar';
  const btnZIn  = el('button', ['pe-btn'], { type:'button', title:'Zoom + (Ctrl +)' }); btnZIn.textContent = '+';
  const sep2    = el('div', 'pe-spacer');
  const btnUndo = el('button', ['pe-btn','pe-ghost'], { type:'button', title:'Desfazer (Ctrl+Z)' }); btnUndo.textContent = 'Desfazer';
  const btnRedo = el('button', ['pe-btn','pe-ghost'], { type:'button', title:'Refazer (Ctrl+Shift+Z)' }); btnRedo.textContent = 'Refazer';
  const btnClear= el('button', ['pe-btn','pe-ghost'], { type:'button', title:'Limpar' }); btnClear.textContent = 'Limpar';
  const btnCancel = el('button', ['pe-btn'], { type:'button' }); btnCancel.textContent = 'Cancelar';
  const btnSave = el('button', ['pe-btn','pe-primary'], { type:'button' }); btnSave.textContent = 'Salvar';

  toolbar.append(btnRed, btnTxt, btnPan, sep1, btnZOut, btnZFit, btnZIn, sep2, btnUndo, btnRedo, btnClear, btnCancel, btnSave);

  header.append(title, btnClose);

  // corpo
  const body = el('div', ['pe-body','modal-body']);
  const canvasWrap = el('div', 'pe-canvas-wrap');
  const canvas = el('canvas', 'pe-canvas', { 'data-no-drag': '' });
  canvasWrap.append(canvas);
  body.append(canvasWrap);

  modal.append(header, toolbar, body);
  overlay.append(modal);
  document.body.appendChild(overlay);

  // ---- Carrega bitmap
  let bmp;
  try {
    bmp = await loadBitmapCSPSafe(bitmap);
  } catch (e) {
    console.error('[page-editor] loadBitmapCSPSafe falhou:', e);
    alert('Falha ao abrir a página no editor.');
    overlay.remove();
    return;
  }

  // ---- Fit inicial
  const baseW = bmp.width;
  const baseH = bmp.height;
  const fitScale = Math.min(MAX_VIEW_W / baseW, MAX_VIEW_H / baseH, 1);
  const viewW = Math.floor(baseW * fitScale);
  const viewH = Math.floor(baseH * fitScale);

  // dimensões CSS (client) e DPR
  canvas.style.width = `${viewW}px`;
  canvas.style.height = `${viewH}px`;

  // estado
  const st = createState(canvas, bmp, viewRotation);
  st.w = viewW;
  st.h = viewH;

  // centraliza no wrap
  const wrapPad = 24;
  canvasWrap.style.width = `${viewW}px`;
  canvasWrap.style.height = `${viewH}px`;
  canvasWrap.style.padding = `${wrapPad}px`;

  // zoom fit
  const doFit = () => {
    st.zoom = 1;
    st.panX = 0;
    st.panY = 0;
    drawScene(st);
  };

  // tool helpers
  const setTool = (t) => {
    st.tool = t;
    [btnRed, btnTxt, btnPan].forEach(b => b.classList.remove('is-active'));
    if (t === 'redact') btnRed.classList.add('is-active');
    else if (t === 'text') btnTxt.classList.add('is-active');
    else if (t === 'pan') btnPan.classList.add('is-active');
  };

  // eventos UI
  btnRed.addEventListener('click', () => setTool('redact'));
  btnTxt.addEventListener('click', () => setTool('text'));
  btnPan.addEventListener('click', () => setTool('pan'));

  btnZFit.addEventListener('click', doFit);
  btnZIn.addEventListener('click', () => { st.zoom = Math.min(6, st.zoom * 1.15); drawScene(st); });
  btnZOut.addEventListener('click', () => { st.zoom = Math.max(0.2, st.zoom / 1.15); drawScene(st); });

  const closeModal = () => {
    try { if ('close' in bmp && typeof bmp.close === 'function') bmp.close(); } catch {}
    overlay.remove();
    window.removeEventListener('keydown', keyHandler, true);
  };
  btnCancel.addEventListener('click', closeModal);
  btnClose.addEventListener('click', closeModal);

  // ---- Interação canvas
  const onMouseMove = (ev) => {
    // viewport panning
    if (st.draggingViewport) {
      st.panX += ev.movementX;
      st.panY += ev.movementY;
      drawScene(st);
      return;
    }

    const [vx, vy] = screenToView(st, ev.clientX, ev.clientY);

    // arrasto de overlay
    if (st.dragging.type) {
      if (st.dragging.type === 'rect') {
        const r = st.rects[st.dragging.idx];
        const w = r.viewPx[2] - r.viewPx[0];
        const h = r.viewPx[3] - r.viewPx[1];
        r.viewPx[0] = clamp(vx - st.dragging.dx, 0, st.w - w);
        r.viewPx[1] = clamp(vy - st.dragging.dy, 0, st.h - h);
        r.viewPx[2] = r.viewPx[0] + w;
        r.viewPx[3] = r.viewPx[1] + h;
      } else if (st.dragging.type === 'text') {
        const t = st.texts[st.dragging.idx];
        // recalcula tamanho
        measureTextBox(st, t);
        t.view.x = clamp(vx - st.dragging.dx, 0, st.w - t.view.width);
        t.view.y = clamp(vy - st.dragging.dy, 0, st.h - t.view.height);
      }
      drawScene(st);
      return;
    }

    // sizing de novo retângulo
    if (st.selRect) {
      const [x0, y0] = [st.selRect[0], st.selRect[1]];
      st.selRect = normRect(x0, y0, vx, vy);
      drawScene(st);
      return;
    }

    // hover normal
    st.hover = findHover(st, vx, vy);
    drawScene(st);
  };

  const onMouseDown = (ev) => {
    // botão do meio/pan com ferramenta mover
    if (st.tool === 'pan' || ev.button === 1) {
      st.draggingViewport = true;
      canvas.style.cursor = 'grabbing';
      return;
    }

    const [vx, vy] = screenToView(st, ev.clientX, ev.clientY);
    const h = findHover(st, vx, vy);

    // mover overlays existentes
    if (h.type && (st.tool === h.type || (st.tool === 'redact' && h.type === 'rect') || (st.tool === 'text' && h.type === 'text'))) {
      st.dragging.type = h.type;
      st.dragging.idx = h.idx;
      if (h.type === 'rect') {
        const r = st.rects[h.idx];
        st.dragging.dx = vx - r.viewPx[0];
        st.dragging.dy = vy - r.viewPx[1];
      } else {
        const t = st.texts[h.idx];
        measureTextBox(st, t);
        st.dragging.dx = vx - t.view.x;
        st.dragging.dy = vy - t.view.y;
      }
      return;
    }

    // criar novo
    if (st.tool === 'redact') {
      st.selRect = [vx, vy, vx, vy];
      return;
    }
    if (st.tool === 'text') {
      const txt = prompt('Texto a inserir:', '');
      if (txt && txt.length) {
        pushUndo(st);
        const size = Math.max(8, Math.round(16 * (1 / fitScale))); // baseado no fit
        const t = {
          view: { x: vx, y: vy, size, text: txt, width: 0, height: 0 },
          viewNorm: { x: vx / st.w, y: vy / st.h, sizeRel: size / st.h }
        };
        measureTextBox(st, t);
        t.view.x = clamp(t.view.x, 0, st.w - t.view.width);
        t.view.y = clamp(t.view.y, 0, st.h - t.view.height);
        t.viewNorm.x = t.view.x / st.w;
        t.viewNorm.y = t.view.y / st.h;
        st.texts.push(t);
        drawScene(st);
      }
      return;
    }
  };

  const onMouseUp = () => {
    if (st.draggingViewport) {
      st.draggingViewport = false;
      canvas.style.cursor = 'default';
      return;
    }
    if (st.dragging.type) {
      pushUndo(st);
      if (st.dragging.type === 'rect') {
        const r = st.rects[st.dragging.idx];
        r.viewNorm = [r.viewPx[0]/st.w, r.viewPx[1]/st.h, r.viewPx[2]/st.w, r.viewPx[3]/st.h];
      } else if (st.dragging.type === 'text') {
        const t = st.texts[st.dragging.idx];
        t.viewNorm.x = t.view.x / st.w;
        t.viewNorm.y = t.view.y / st.h;
        t.viewNorm.sizeRel = t.view.size / st.h;
      }
      st.dragging.type = null; st.dragging.idx = -1;
      return;
    }
    if (st.selRect) {
      const [x0, y0, x1, y1] = st.selRect;
      const [X0, Y0, X1, Y1] = normRect(x0, y0, x1, y1);
      st.selRect = null;
      if (Math.abs(X1 - X0) > 3 && Math.abs(Y1 - Y0) > 3) {
        pushUndo(st);
        st.rects.push({
          viewPx: [X0, Y0, X1, Y1],
          viewNorm: [X0/st.w, Y0/st.h, X1/st.w, Y1/st.h]
        });
      }
      drawScene(st);
      return;
    }
  };

  const onWheel = (ev) => {
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    const dir = Math.sign(-ev.deltaY);
    const old = st.zoom;
    const factor = dir > 0 ? 1.15 : 1/1.15;

    // zoom no ponto do cursor
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left;
    const my = ev.clientY - rect.top;

    st.zoom = clamp(st.zoom * factor, 0.2, 6);
    st.panX = mx - (mx - st.panX) * (st.zoom / old);
    st.panY = my - (my - st.panY) * (st.zoom / old);
    drawScene(st);
  };

  canvas.addEventListener('mousemove', onMouseMove);
  window.addEventListener('mouseup', onMouseUp, true);
  canvas.addEventListener('mousedown', onMouseDown);
  canvas.addEventListener('wheel', onWheel, { passive:false });

  // atalhos
  const keyHandler = (ev) => {
    // ferramentas
    if (!ev.ctrlKey && !ev.metaKey) {
      if (ev.key.toLowerCase() === 'r') { setTool('redact'); }
      if (ev.key.toLowerCase() === 't') { setTool('text'); }
      if (ev.key.toLowerCase() === 'h') { setTool('pan'); }
      if (ev.key === 'Escape') { ev.preventDefault(); closeModal(); return; }
      if (ev.key === 'Delete' || ev.key === 'Backspace') {
        if (st.tool === 'redact' && st.rects.length) { pushUndo(st); st.rects.pop(); drawScene(st); }
        else if (st.tool === 'text' && st.texts.length) { pushUndo(st); st.texts.pop(); drawScene(st); }
      }
    }
    // zoom
    if ((ev.ctrlKey || ev.metaKey) && (ev.key === '+' || ev.key === '=')) { ev.preventDefault(); st.zoom = Math.min(6, st.zoom * 1.15); drawScene(st); }
    if ((ev.ctrlKey || ev.metaKey) && ev.key === '-') { ev.preventDefault(); st.zoom = Math.max(0.2, st.zoom / 1.15); drawScene(st); }
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === '0') { ev.preventDefault(); doFit(); }

    // undo/redo
    if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && ev.key.toLowerCase() === 'z') { ev.preventDefault(); doUndo(st); drawScene(st); }
    if ((ev.ctrlKey || ev.metaKey) && ev.shiftKey && ev.key.toLowerCase() === 'z') { ev.preventDefault(); doRedo(st); drawScene(st); }
  };
  window.addEventListener('keydown', keyHandler, true);

  // limpar
  btnClear.addEventListener('click', () => {
    if (!st.rects.length && !st.texts.length) return;
    pushUndo(st);
    st.rects = [];
    st.texts = [];
    drawScene(st);
    try { document.dispatchEvent(new CustomEvent('gv:editor:cropCleared')); } catch {}
  });
  btnUndo.addEventListener('click', () => { doUndo(st); drawScene(st); });
  btnRedo.addEventListener('click', () => { doRedo(st); drawScene(st); });

  // Salvar → POST JSON
  btnSave.addEventListener('click', async () => {
    try {
      // mapeia para base (0°)
      const redact = st.rects.map(r => {
        const [nx0, ny0, nx1, ny1] = r.viewNorm;
        const [bx0, by0] = mapViewToBase(nx0, ny0, st.viewRotation);
        const [bx1, by1] = mapViewToBase(nx1, ny1, st.viewRotation);
        const [X0, Y0, X1, Y1] = normRect(bx0, by0, bx1, by1);
        return { x0: X0, y0: Y0, x1: X1, y1: Y1 };
      });

      const texts = st.texts.map(t => {
        const { x, y, sizeRel } = t.viewNorm;
        const [bx, by] = mapViewToBase(x, y, st.viewRotation);
        return { x: bx, y: by, text: t.view.text, size_rel: sizeRel };
      });

      if (!redact.length && !texts.length) { alert('Nada para salvar.'); return; }

      btnSave.disabled = true;

      const payload = {
        session_id: sessionId,
        page_index: pageIndex,
        operations: { redact, texts }
      };

      const resp = await fetch(OVERLAY_ENDPOINT, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRFToken': getCSRFToken()
        },
        credentials: 'same-origin',
        cache: 'no-store',
        body: JSON.stringify(payload)
      });
      const data = await resp.json().catch(()=> ({}));
      if (!resp.ok) throw new Error(data?.error || 'Falha ao aplicar overlays.');

      // avisa para recarregar o PDF da sessão
      document.dispatchEvent(new CustomEvent('page-editor:saved', {
        detail: { session_id: sessionId, page_index: pageIndex, ts: Date.now() }
      }));

      closeModal();
    } catch (e) {
      console.error('[page-editor] salvar falhou:', e);
      alert('Erro ao salvar: ' + (e?.message || 'desconhecido'));
    } finally {
      btnSave.disabled = false;
    }
  });

  // Upgrade de nitidez (opcional)
  if (typeof getBitmap === 'function' && dpr() > 1.5) {
    try {
      const hi = await getBitmap(Math.min(3.5, dpr() * 1.25));
      const hiBmp = await loadBitmapCSPSafe(hi);
      st.bmp = hiBmp;
      drawScene(st);
    } catch { /* ignore */ }
  }

  // desenha primeira vez
  doFit();

  // evita scroll de fundo
  overlay.addEventListener('wheel', (e) => e.preventDefault(), { passive:false });
}

// default export e fallback global
export default openPageEditor;
try { window.GV_OPEN_PAGE_EDITOR = openPageEditor; } catch {}