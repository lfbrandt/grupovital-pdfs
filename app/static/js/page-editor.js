// app/static/js/page-editor.js
// Editor de página em modal (zoom, rotação, recorte) – compatível com CSP estrita.
// Nada de estilos inline: todas as regras vão para um <style nonce="..."> dedicado.

let __pe_open = false;

export async function openPageEditor({ pdf, pageNumber = 1, rotation = 0, cropNorm = null }) {
  if (!pdf) throw new Error('openPageEditor: pdf ausente');
  if (__pe_open) throw new Error('Editor já aberto');
  __pe_open = true;

  // Promise de resultado criada logo no início (evita race)
  let resolvePromise, rejectPromise;
  const resultPromise = new Promise((res, rej) => { resolvePromise = res; rejectPromise = rej; });

  // ===== helpers de nonce + stylesheet escopado =====
  const getNonce = () => {
    const meta = document.querySelector('meta[name="csp-nonce"]');
    if (meta?.content) return meta.content;
    // fallback: pega nonce de qualquer <script nonce>
    const s = document.querySelector('script[nonce]');
    return s?.nonce || s?.getAttribute?.('nonce') || '';
  };
  const makeUID  = (p='pe') => p + Math.random().toString(36).slice(2, 10);

  const uid = makeUID();                 // escopo do editor
  const nonce = getNonce();

  // Cria uma <style nonce> com regras base do editor
  const styleEl = document.createElement('style');
  if (nonce) styleEl.setAttribute('nonce', nonce);
  styleEl.textContent = `
    html.pe-noscroll{overflow:hidden!important}
    .${uid}-backdrop{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.6);z-index:9999}
    .${uid}-modal{width:min(94vw,1000px);max-height:94vh;background:#0f1115;color:#e5e7eb;border-radius:14px;display:grid;grid-template-rows:auto 1fr auto;overflow:hidden}
    .${uid}-header,.${uid}-footer{padding:10px 14px;display:flex;align-items:center;justify-content:space-between;background:linear-gradient(90deg,rgba(255,255,255,.04),rgba(255,255,255,0));border-bottom:1px solid rgba(255,255,255,.08)}
    .${uid}-footer{border-top:1px solid rgba(255,255,255,.08);border-bottom:0}
    .${uid}-actions .${uid}-btn{margin-right:6px}
    .${uid}-sep{width:1px;height:24px;background:rgba(255,255,255,.14);margin:0 6px;display:inline-block}
    .${uid}-area{padding:10px;overflow:auto;background:#0a0c10}
    .${uid}-wrap{position:relative;margin:0 auto;width:max-content}
    .${uid}-canvas{display:block;background:#fff;border-radius:8px;max-width:100%;height:auto}
    .${uid}-overlay{position:absolute;inset:0;cursor:crosshair;user-select:none;touch-action:none}
    .${uid}-box{position:absolute;border:2px dashed rgba(255,255,255,.95);background:rgba(0,0,0,.12);pointer-events:none;box-shadow:0 0 0 20000px rgba(0,0,0,.25);border-radius:6px}
    .${uid}-box.is-hidden{display:none}
    .${uid}-btn{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.15);color:#e5e7eb;padding:6px 10px;border-radius:8px}
    .${uid}-btn:hover{background:rgba(255,255,255,.12)}
    .${uid}-primary{background:#00995d;border-color:#00995d;color:#fff}
    .${uid}-primary:hover{filter:brightness(1.05)}
    .${uid}-cta{display:flex;gap:8px}
    .${uid}-hint{opacity:.7;font-size:.9rem}
  `;
  document.head.appendChild(styleEl);
  const sheet = styleEl.sheet;

  // Regras dinâmicas (atualizadas via CSSOM)
  const canvasClass = `${uid}-cnv`;      // define largura visual do canvas
  const boxClass    = `${uid}-boxdyn`;   // define left/top/width/height do crop
  const canvasRuleIndex = sheet.insertRule(`.${canvasClass}{width:auto}`, sheet.cssRules.length);
  const boxRuleIndex    = sheet.insertRule(`.${boxClass}{left:0;top:0;width:1px;height:1px}`, sheet.cssRules.length);

  const setCanvasCssWidth = (px) => {
    const rule = sheet.cssRules[canvasRuleIndex];
    rule.style.width = `${Math.max(1, Math.round(px))}px`;
  };
  const setBoxRect = (x, y, w, h) => {
    const rule = sheet.cssRules[boxRuleIndex];
    rule.style.left   = `${Math.round(x)}px`;
    rule.style.top    = `${Math.round(y)}px`;
    rule.style.width  = `${Math.max(1, Math.round(w))}px`;
    rule.style.height = `${Math.max(1, Math.round(h))}px`;
  };

  // ===== estrutura do modal (sem inline style) =====
  const backdrop = document.createElement('div');
  backdrop.className = `${uid}-backdrop`;
  backdrop.tabIndex = -1;

  const modal = document.createElement('div');
  modal.className = `${uid}-modal`;
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.innerHTML = `
    <div class="${uid}-header">
      <div class="${uid}-title">Editar página ${pageNumber}</div>
      <div class="${uid}-actions">
        <button type="button" class="${uid}-btn" data-zoom-out title="Zoom -">−</button>
        <button type="button" class="${uid}-btn" data-zoom-reset title="Ajustar">Aj</button>
        <button type="button" class="${uid}-btn" data-zoom-in title="Zoom +">+</button>
        <span class="${uid}-sep"></span>
        <button type="button" class="${uid}-btn" data-rot-left title="Girar ⟲">⟲</button>
        <button type="button" class="${uid}-btn" data-rot-right title="Girar ⟳">⟳</button>
        <span class="${uid}-sep"></span>
        <button type="button" class="${uid}-btn" data-clear-crop title="Limpar recorte">Limpar</button>
      </div>
    </div>
    <div class="${uid}-area">
      <div class="${uid}-wrap">
        <canvas class="${uid}-canvas ${canvasClass}" tabindex="0"></canvas>
        <div class="${uid}-overlay" aria-hidden="true"></div>
        <div class="${uid}-box is-hidden ${boxClass}"></div>
      </div>
    </div>
    <div class="${uid}-footer">
      <div class="${uid}-hint">Arraste para selecionar um retângulo. Duplo clique limpa. Ctrl/⌘+scroll = zoom.</div>
      <div class="${uid}-cta">
        <button type="button" class="${uid}-btn ${uid}-cancel">Cancelar</button>
        <button type="button" class="${uid}-btn ${uid}-primary ${uid}-save">Salvar</button>
      </div>
    </div>
  `;
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  document.documentElement.classList.add('pe-noscroll');

  const canvas   = modal.querySelector(`.${uid}-canvas`);
  const overlay  = modal.querySelector(`.${uid}-overlay`);
  const boxEl    = modal.querySelector(`.${uid}-box`);
  const btnZoomIn  = modal.querySelector('[data-zoom-in]');
  const btnZoomOut = modal.querySelector('[data-zoom-out]');
  const btnZoomRes = modal.querySelector('[data-zoom-reset]');
  const btnRotL    = modal.querySelector('[data-rot-left]');
  const btnRotR    = modal.querySelector('[data-rot-right]');
  const btnClear   = modal.querySelector('[data-clear-crop]');
  const btnSave    = modal.querySelector(`.${uid}-save`);
  const btnCancel  = modal.querySelector(`.${uid}-cancel`);
  const areaEl     = modal.querySelector(`.${uid}-area`);

  // ===== estado =====
  const dpr = window.devicePixelRatio || 1;
  let scale = 1.3;                 // zoom relativo ao "fit"
  // normaliza rotação recebida: 0/90/180/270
  const _rotIn = ((rotation | 0) % 360 + 360) % 360;
  let rot = [0, 90, 180, 270].includes(_rotIn) ? _rotIn : 0;

  let crop = cropNorm ? { ...cropNorm } : null; // {x0,y0,x1,y1} (sem rotação)
  let pdfW = 0, pdfH = 0;
  let dragStart = null;           // {x,y} em px do canvas

  const page = await pdf.getPage(pageNumber);
  const unrot = page.getViewport({ scale: 1, rotation: 0 });
  pdfW = unrot.width; pdfH = unrot.height;

  const calcViewport = () => {
    const areaW = areaEl.clientWidth  || 900;
    const areaH = areaEl.clientHeight || 620;
    const base = page.getViewport({ scale: 1, rotation: rot });
    const fitScale = Math.min(areaW / base.width, areaH / base.height) * 0.98;
    return { fitScale, base };
  };

  async function render() {
    const { fitScale } = calcViewport();
    const s  = scale <= 0 ? fitScale : (scale * fitScale);
    const vp = page.getViewport({ scale: s * dpr, rotation: rot });

    // dimensões reais do canvas (device px)
    canvas.width  = Math.floor(vp.width);
    canvas.height = Math.floor(vp.height);

    // largura visual (CSS px) via regra dinâmica (CSP-safe)
    setCanvasCssWidth(Math.round(vp.width / dpr));

    const ctx = canvas.getContext('2d', { alpha: false });
    ctx.fillStyle = '#fff';
    ctx.fillRect(0,0,canvas.width,canvas.height);

    await page.render({ canvasContext: ctx, viewport: vp, intent: 'print' }).promise;

    // reposiciona caixa se houver crop salvo
    if (crop) {
      const rectPdf = [crop.x0 * pdfW, crop.y0 * pdfH, crop.x1 * pdfW, crop.y1 * pdfH];
      const [vx0, vy0, vx1, vy1] = vp.convertToViewportRectangle(rectPdf);
      const x = Math.min(vx0, vx1), y = Math.min(vy0, vy1);
      const w = Math.abs(vx1 - vx0), h = Math.abs(vy1 - vy0);
      setBoxRect(x, y, w, h);
      boxEl.classList.remove('is-hidden');
    } else {
      boxEl.classList.add('is-hidden');
    }
  }

  // ===== zoom/rotação =====
  const zoomIn  = () => { scale = Math.min(3, (scale || 1) + 0.15); render(); };
  const zoomOut = () => { scale = Math.max(0.15, (scale || 1) - 0.15); render(); };
  const zoomFit = () => { scale = 1; render(); };

  btnZoomIn.addEventListener('click',  zoomIn);
  btnZoomOut.addEventListener('click', zoomOut);
  btnZoomRes.addEventListener('click', zoomFit);
  areaEl.addEventListener('wheel', (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    if (e.deltaY > 0) zoomOut(); else zoomIn();
  }, { passive: false });

  btnRotL.addEventListener('click', () => { rot = (rot + 270) % 360; render(); });
  btnRotR.addEventListener('click', () => { rot = (rot + 90)  % 360; render(); });

  // ===== crop =====
  const toCanvasXY = (clientX, clientY) => {
    const r = canvas.getBoundingClientRect();
    return { x: clientX - r.left, y: clientY - r.top };
  };

  const pointerDown = (clientX, clientY) => {
    dragStart = toCanvasXY(clientX, clientY);
    boxEl.classList.remove('is-hidden');
    setBoxRect(dragStart.x, dragStart.y, 1, 1);
  };
  const pointerMove = (clientX, clientY) => {
    if (!dragStart) return;
    const c = toCanvasXY(clientX, clientY);
    const x = Math.min(dragStart.x, c.x), y = Math.min(dragStart.y, c.y);
    const w = Math.abs(c.x - dragStart.x), h = Math.abs(c.y - dragStart.y);
    setBoxRect(x, y, w, h);
  };
  const pointerUp = async () => {
    if (!dragStart) return;
    const rules = sheet.cssRules[boxRuleIndex].style;
    const w = parseFloat(rules.width)  || 0;
    const h = parseFloat(rules.height) || 0;
    dragStart = null;
    if (w < 4 || h < 4) { crop = null; boxEl.classList.add('is-hidden'); return; }

    const { fitScale } = calcViewport();
    const s  = scale <= 0 ? fitScale : (scale * fitScale);
    const vp = page.getViewport({ scale: s * (window.devicePixelRatio || 1), rotation: rot });

    const x = parseFloat(rules.left) || 0;
    const y = parseFloat(rules.top)  || 0;

    const [pdfX0, pdfY0] = vp.convertToPdfPoint(x, y);
    const [pdfX1, pdfY1] = vp.convertToPdfPoint(x + w, y + h);

    const x0n = Math.max(0, Math.min(1, Math.min(pdfX0, pdfX1) / pdfW));
    const x1n = Math.max(0, Math.min(1, Math.max(pdfX0, pdfX1) / pdfW));
    const y0n = Math.max(0, Math.min(1, Math.min(pdfY0, pdfY1) / pdfH));
    const y1n = Math.max(0, Math.min(1, Math.max(pdfY0, pdfY1) / pdfH));
    crop = { x0: x0n, y0: y0n, x1: x1n, y1: y1n };
  };

  overlay.addEventListener('pointerdown', (e) => {
    overlay.setPointerCapture?.(e.pointerId);
    pointerDown(e.clientX, e.clientY);
  });
  overlay.addEventListener('pointermove', (e) => pointerMove(e.clientX, e.clientY));
  overlay.addEventListener('pointerup',   () => pointerUp());
  overlay.addEventListener('pointercancel', () => { dragStart = null; });
  overlay.addEventListener('dblclick', () => { crop = null; boxEl.classList.add('is-hidden'); });

  // ===== teclado =====
  function onKey(e){
    if (e.key === 'Escape') { e.preventDefault(); finish(false); }
    if ((e.key === 'Enter') && (e.ctrlKey || e.metaKey)) { e.preventDefault(); finish(true); }
    if (!e.ctrlKey && !e.metaKey) {
      if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomIn(); }
      if (e.key === '-') { e.preventDefault(); zoomOut(); }
      if (e.key.toLowerCase?.() === 'r') { e.preventDefault(); rot = e.shiftKey ? (rot + 270) % 360 : (rot + 90) % 360; render(); }
    }
  }
  document.addEventListener('keydown', onKey);

  // ===== resize =====
  const ro = new ResizeObserver(() => render());
  ro.observe(areaEl);

  // ===== ações =====
  btnClear.addEventListener('click', () => { crop = null; boxEl.classList.add('is-hidden'); });
  btnSave.addEventListener('click',   () => finish(true));
  btnCancel.addEventListener('click', () => finish(false));
  backdrop.addEventListener('click',  (e) => { if (e.target === backdrop) finish(false); });

  async function finish(ok){
    cleanup();
    if (!ok) { rejectPromise(new Error('cancelado')); return; }
    const cropAbs = crop ? [crop.x0 * pdfW, crop.y0 * pdfH, crop.x1 * pdfW, crop.y1 * pdfH] : null;
    const changed = (rot !== _rotIn) || (JSON.stringify(cropNorm || null) !== JSON.stringify(crop || null));
    resolvePromise({ pageNumber, rotation: rot, cropNorm: crop, cropAbs, pdfW, pdfH, changed });
  }

  function cleanup(){
    __pe_open = false;
    document.removeEventListener('keydown', onKey);
    ro.disconnect();
    document.documentElement.classList.remove('pe-noscroll');
    backdrop.remove();
    styleEl.remove();
  }

  // Render inicial
  await render();
  setTimeout(() => canvas.focus(), 0);

  return resultPromise;
}