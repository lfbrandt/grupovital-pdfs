// app/static/js/utils.js
/* ============================================================================
   Utils — ES Module com exports nomeados e default
   - Compat: também anexa em window.* para scripts clássicos (não-modules).
   - CSP ok: sem inline, sem dependências externas.
   ============================================================================ */

/* ===== CSRF ===== */

/** Lê o token CSRF do <meta name="csrf-token"> */
export function getCSRFToken() {
  try {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  } catch {
    return '';
  }
}

/** Retorna Headers com X-CSRFToken preenchido (preserva já existentes) */
export function withCSRFFromMeta(headers = new Headers()) {
  const h = headers instanceof Headers ? headers : new Headers(headers || {});
  if (!h.has('X-CSRFToken')) h.set('X-CSRFToken', getCSRFToken());
  return h;
}

/* ===== Fetch wrapper ===== */

/**
 * xhrRequest(url, { method, body, headers })
 * - Accept: application/json por padrão
 * - credentials: 'same-origin'
 * - Retorno automático: JSON quando for JSON; senão texto
 * - Lança Error com status e payload quando !res.ok
 */
export async function xhrRequest(url, options = {}) {
  const { headers = {}, ...rest } = options;

  const finalHeaders = new Headers(headers);
  if (!finalHeaders.has('Accept')) finalHeaders.set('Accept', 'application/json');
  // Se body for FormData, *não* force Content-Type. O browser define o boundary.

  const res = await fetch(url, {
    credentials: 'same-origin',
    headers: finalHeaders,
    ...rest,
  });

  const ct = res.headers.get('content-type') || '';
  let payload = null;
  try {
    payload = ct.includes('application/json') ? await res.json() : await res.text();
  } catch {
    // sem corpo válido
  }

  if (!res.ok) {
    const msg =
      (payload && typeof payload === 'object' && (payload.message || payload.error)) ||
      (typeof payload === 'string' && payload) ||
      `HTTP ${res.status}`;
    const err = new Error(msg);
    err.status = res.status;
    err.payload = payload;
    throw err;
  }

  return payload;
}

/* ===== UI helpers ===== */

/** Exibe mensagem simples; se não houver container, loga no console */
export function mostrarMensagem(msg, tipo = 'sucesso', timeout = 5000, elId = 'mensagem-feedback') {
  const el = typeof document !== 'undefined' && document.getElementById(elId);
  if (!el) {
    (tipo === 'erro' || tipo === 'error' ? console.error : console.log)(msg);
    return;
  }

  // normaliza classes conhecidas
  el.classList.remove('sucesso', 'erro', 'aviso', 'info', 'hidden', 'msg-info', 'msg-warn', 'msg-error');
  el.textContent = msg;

  let cls = 'sucesso';
  if (['erro', 'error'].includes(tipo)) cls = 'erro';
  else if (['aviso', 'warn', 'warning'].includes(tipo)) cls = 'aviso';
  else if (['info', 'informacao', 'informação'].includes(tipo)) cls = 'info';
  el.classList.add(cls);

  if (timeout) {
    clearTimeout(el.__hideTimer);
    el.__hideTimer = setTimeout(() => el.classList.add('hidden'), timeout);
  }
}

export function mostrarLoading(show = true, elId = 'loading-spinner') {
  const el = typeof document !== 'undefined' && document.getElementById(elId);
  if (!el) return;
  if (show) {
    el.classList.remove('hidden');
    el.setAttribute('aria-hidden', 'false');
  } else {
    el.classList.add('hidden');
    el.setAttribute('aria-hidden', 'true');
  }
}

export function atualizarProgresso(percent, containerId = 'progress-container', barId = 'progress-bar') {
  const container = typeof document !== 'undefined' && document.getElementById(containerId);
  const bar = typeof document !== 'undefined' && document.getElementById(barId);
  if (!container || !bar) return;
  const p = Math.max(0, Math.min(100, Number(percent) || 0));
  container.classList.remove('hidden');
  bar.style.width = p + '%';
  bar.ariaValueNow = String(p);
}

export function resetarProgresso(containerId = 'progress-container', barId = 'progress-bar') {
  const container = typeof document !== 'undefined' && document.getElementById(containerId);
  const bar = typeof document !== 'undefined' && document.getElementById(barId);
  if (!container || !bar) return;
  bar.style.width = '0%';
  container.classList.add('hidden');
  bar.ariaValueNow = '0';
}

/* ===== Conveniências ===== */

export function debounce(fn, wait = 200) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), wait);
  };
}

export function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

/* ======== Utilitários compartilhados para grid de páginas ======== */

/** Normaliza ângulo para [0,360) */
export function normalizeAngle(a) {
  a = Number(a) || 0;
  a %= 360;
  if (a < 0) a += 360;
  return a;
}

/**
 * Lê --thumb-w do CSS (container, seletor ou root) com fallback padronizado (200).
 * @param {HTMLElement|string|null} containerOrSel
 * @param {number} fallback
 */
export function getThumbWidth(containerOrSel = null, fallback = 200) {
  try {
    const el =
      typeof containerOrSel === 'string'
        ? document.querySelector(containerOrSel)
        : (containerOrSel || document.documentElement);
    const v = parseInt(getComputedStyle(el).getPropertyValue('--thumb-w') || String(fallback), 10);
    return Number.isFinite(v) && v > 0 ? v : fallback;
  } catch {
    return fallback;
  }
}

/** Dimensões “intrínsecas” do conteúdo (canvas/img), priorizando dataset.bmpW/H */
export function getMediaSize(el) {
  if (!el) return null;

  const ds = el.dataset || {};
  const bmpW = Number(ds.bmpW || ds.bmpw || 0);
  const bmpH = Number(ds.bmpH || ds.bmph || 0);
  if (bmpW > 0 && bmpH > 0) return { w: bmpW, h: bmpH };

  if (el instanceof HTMLCanvasElement) {
    const w = Number(el.width) || 0;
    const h = Number(el.height) || 0;
    return (w && h) ? { w, h } : null;
  }
  if (el instanceof HTMLImageElement) {
    const w = Number(el.naturalWidth)  || Number(el.width)        || Number(el.clientWidth)  || 0;
    const h = Number(el.naturalHeight) || Number(el.height)       || Number(el.clientHeight) || 0;
    return (w && h) ? { w, h } : null;
  }
  const w = Number(el.naturalWidth || el.videoWidth || el.clientWidth)  || 0;
  const h = Number(el.naturalHeight || el.videoHeight || el.clientHeight) || 0;
  return (w && h) ? { w, h } : null;
}

/** Fator 'contain' (sem corte) para ajustar conteúdo dentro do frame */
export function containScale(containerW, containerH, contentW, contentH) {
  const cw = Math.max(1, Number(containerW) || 0);
  const ch = Math.max(1, Number(containerH) || 0);
  const iw = Math.max(1, Number(contentW) || 0);
  const ih = Math.max(1, Number(contentH) || 0);
  return Math.min(cw / iw, ch / ih);
}

/**
 * Aplica fit + rotate na MÍDIA (não no frame), com translate(-50%,-50%) + rotate + scale(contain)
 * - Não amplia thumbs (limitado a 1x)
 * - Micro anti-serrilhado em 90/270 no Chrome via filter: blur(0.001px)
 */
export function fitRotateMedia({ frameEl, mediaEl, angle = 0 } = {}) {
  if (!frameEl || !mediaEl) return;
  const deg = normalizeAngle(angle);

  const fw = Math.max(1, frameEl.clientWidth  || frameEl.offsetWidth  || 0);
  const fh = Math.max(1, frameEl.clientHeight || frameEl.offsetHeight || 0);

  const m = getMediaSize(mediaEl);
  if (!m) return;

  const rotOdd = (deg === 90 || deg === 270);
  const baseW = rotOdd ? m.h : m.w;
  const baseH = rotOdd ? m.w : m.h;

  const scale = Math.min(1, containScale(fw, fh, baseW, baseH)); // não ampliar thumbs

  mediaEl.style.position = 'absolute';
  mediaEl.style.left = '50%';
  mediaEl.style.top  = '50%';
  mediaEl.style.transformOrigin = '50% 50%';
  mediaEl.style.backfaceVisibility = 'hidden';
  mediaEl.style.maxWidth = 'none';
  mediaEl.style.height = 'auto';
  if (!mediaEl.style.transition) mediaEl.style.transition = 'transform .12s ease';

  mediaEl.style.transform = `translate(-50%, -50%) rotate(${deg}deg) scale(${scale})`;
  // micro anti-serrilhado em 90/270 (principalmente Chrome)
  mediaEl.style.filter = rotOdd ? 'blur(0.001px)' : '';
}

/** Extrai crop absoluto [x0,y0,x1,y1] do wrapper se existir (ou converte de dataset.crop com pdfW/H) */
export function getCropBoxAbs(wrapperEl) {
  if (!wrapperEl) return null;

  // Prioriza crop absoluto pronto
  let abs = wrapperEl.dataset?.cropAbs || wrapperEl.dataset?.cropabs;
  if (abs) {
    try {
      const box = JSON.parse(abs);
      if (Array.isArray(box) && box.length === 4) return box.map(Number);
    } catch {}
  }

  // Converte de crop normalizado (0..1) se tiver pdfW/H
  const norm = wrapperEl.dataset?.crop;
  const W = Number(wrapperEl.dataset?.pdfW || wrapperEl.dataset?.pdfw || 0);
  const H = Number(wrapperEl.dataset?.pdfH || wrapperEl.dataset?.pdfh || 0);
  if (norm && W > 0 && H > 0) {
    try {
      const { x0, y0, x1, y1 } = JSON.parse(norm);
      return [x0 * W, y0 * H, x1 * W, y1 * H].map(n => Math.max(0, Math.round(n)));
    } catch {}
  }

  return null;
}

/**
 * Coleta pages/rotations/crops a partir do grid:
 *  - Se houver seleção (por aria-selected='true' ou util getSelectedPages) → usa seleção na ordem do DOM
 *  - Se não houver → usa TODAS as páginas presentes no grid (ordem do DOM)
 *
 * Aceita tanto grids renderizados pelo preview.js quanto grids simples.
 */
export function collectPagesRotsCropsAllOrSelection(gridEl) {
  if (!gridEl) return { pages: [], rotations: [], crops: [] };

  // 1) tenta util público do preview.js (se existir)
  let pages = [];
  try {
    const fn = (window.getSelectedPages || (window.preview && window.preview.getSelectedPages));
    if (typeof fn === 'function') pages = fn(gridEl, true) || [];
  } catch { /* ignore */ }

  // 2) se não houver seleção, usa todas as páginas visíveis no grid
  if (!pages || pages.length === 0) {
    pages = Array.from(gridEl.querySelectorAll('.page-wrapper[data-page]'))
      .map(w => Number(w.dataset.page))
      .filter(n => Number.isFinite(n));
  }

  // 3) rotações alinhadas ao vetor pages (UI)
  const rotations = pages.map(pg => {
    const el = gridEl.querySelector(`.page-wrapper[data-page="${pg}"]`);
    const rot = Number(el?.dataset?.rotation) || 0;
    return normalizeAngle(rot);
  });

  // 4) crops (absolutos) apenas se existirem
  const crops = [];
  pages.forEach(pg => {
    const el = gridEl.querySelector(`.page-wrapper[data-page="${pg}"]`);
    const box = getCropBoxAbs(el);
    if (box) crops.push({ page: pg, box });
  });

  return { pages, rotations, crops };
}

/* ===== API default + anexos em window para scripts clássicos ===== */

const api = {
  getCSRFToken,
  withCSRFFromMeta,
  xhrRequest,
  mostrarMensagem,
  mostrarLoading,
  atualizarProgresso,
  resetarProgresso,
  debounce,
  sleep,

  // utils de grid
  normalizeAngle,
  getThumbWidth,
  getMediaSize,
  containScale,
  fitRotateMedia,
  getCropBoxAbs,
  collectPagesRotsCropsAllOrSelection,
};

export default api;

// Expor também como globais quando carregado no browser
if (typeof window !== 'undefined') {
  window.utils = api;
  window.getCSRFToken       = getCSRFToken;
  window.xhrRequest         = xhrRequest;
  window.mostrarMensagem    = mostrarMensagem;
  window.mostrarLoading     = mostrarLoading;
  window.atualizarProgresso = atualizarProgresso;
  window.resetarProgresso   = resetarProgresso;

  // grid helpers
  window.normalizeAngle = normalizeAngle;
  window.getThumbWidth  = getThumbWidth;
  window.getMediaSize   = getMediaSize;
  window.containScale   = containScale;
  window.fitRotateMedia = fitRotateMedia;
  window.getCropBoxAbs  = getCropBoxAbs;
  window.collectPagesRotsCropsAllOrSelection = collectPagesRotsCropsAllOrSelection;
}