import { getCSRFToken, mostrarMensagem } from './utils.esm.js';

const PREFIX = 'split';
const INPUT_SELECTOR = `#input-${PREFIX}`;
const DROPZONE_SELECTOR = `#dropzone-${PREFIX}`;
const PREVIEW_SELECTOR = `#preview-${PREFIX}`;
const BTN_SPLIT_SELECTOR = `#btn-${PREFIX}`;
const BTN_SPLIT_ALL_SELECTOR = '#btn-split-all';
const CLEAR_SELECTOR = '#btn-clear-all';
const PAGE_SELECTOR = '.page-wrapper, .page-thumb, .thumb-card, [data-page], [data-page-id], [data-src-page]';

let initialized = false;
let busy = false;
let primaryDisabledBeforeBusy = null;

function $(selector) {
  return document.querySelector(selector);
}

function inputEl() {
  return $(INPUT_SELECTOR);
}

function splitButton() {
  return $(BTN_SPLIT_SELECTOR);
}

function splitAllButton() {
  return $(BTN_SPLIT_ALL_SELECTOR);
}

function previewRoot() {
  return $(PREVIEW_SELECTOR);
}

function asArray(value) {
  return Array.from(value || []);
}

function isPdfFile(file) {
  if (!file) return false;
  const name = String(file.name || '').toLowerCase();
  const type = String(file.type || '').toLowerCase();
  return name.endsWith('.pdf') || type === 'application/pdf';
}

function getDropzoneFiles() {
  const input = inputEl();
  const api = input?.__gvDropzoneApi;

  if (api && typeof api.getFiles === 'function') {
    try {
      const files = api.getFiles();
      if (files?.length) return files;
    } catch {
      // Fall back to the native FileList below.
    }
  }

  return asArray(input?.files);
}

function getLoadedPdf() {
  return getDropzoneFiles().find(isPdfFile) || null;
}

function hasLoadedPdf() {
  return !!getLoadedPdf();
}

function setButtonDisabled(button, disabled) {
  if (!button) return;
  button.disabled = !!disabled;
  button.setAttribute('aria-disabled', disabled ? 'true' : 'false');
}

function syncSplitButtons() {
  const canSplitAll = hasLoadedPdf() && !busy;
  setButtonDisabled(splitAllButton(), !canSplitAll);
  return canSplitAll;
}

function scheduleSync() {
  syncSplitButtons();
  queueMicrotask(syncSplitButtons);
  window.requestAnimationFrame?.(syncSplitButtons);
}

function setBusy(nextBusy) {
  const next = !!nextBusy;
  if (busy === next) return;

  const primary = splitButton();
  if (next) {
    primaryDisabledBeforeBusy = primary ? primary.disabled : null;
    busy = true;
    setButtonDisabled(primary, true);
    setButtonDisabled(splitAllButton(), true);
    return;
  }

  busy = false;
  syncSplitButtons();

  if (primary) {
    const shouldDisablePrimary = !hasLoadedPdf() || primaryDisabledBeforeBusy === true;
    setButtonDisabled(primary, shouldDisablePrimary);
  }
  primaryDisabledBeforeBusy = null;
}

function normalizeAngle(angle) {
  let value = Number(angle) || 0;
  value %= 360;
  if (value < 0) value += 360;
  if (![0, 90, 180, 270].includes(value)) {
    value = (Math.round(value / 90) * 90) % 360;
  }
  return value;
}

function pageCards() {
  const root = previewRoot();
  if (!root) return [];
  return asArray(root.querySelectorAll(PAGE_SELECTOR))
    .filter((el) => el instanceof HTMLElement);
}

function pageNumberFor(card) {
  if (!card) return null;

  const srcPage = card.dataset?.srcPage ?? card.getAttribute?.('data-src-page');
  const src = Number.parseInt(srcPage, 10);
  if (Number.isFinite(src) && src >= 0) return src + 1;

  const page =
    card.dataset?.page ??
    card.getAttribute?.('data-page') ??
    card.dataset?.pageId ??
    card.getAttribute?.('data-page-id');
  const parsed = Number.parseInt(page, 10);
  if (Number.isFinite(parsed) && parsed > 0) return parsed;

  const index = pageCards().indexOf(card);
  return index >= 0 ? index + 1 : null;
}

function collectRotationsMap() {
  const rotations = {};

  for (const card of pageCards()) {
    const page = pageNumberFor(card);
    if (!page) continue;

    const rotation = normalizeAngle(card.dataset?.rotation ?? card.getAttribute?.('data-rotation'));
    if (rotation !== 0) rotations[String(page)] = rotation;
  }

  return Object.keys(rotations).length ? rotations : null;
}

function clamp01(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Math.max(0, Math.min(1, parsed));
}

function cropFromLegacyDataset(card) {
  const x = clamp01(card.dataset?.cropX);
  const y = clamp01(card.dataset?.cropY);
  const w = clamp01(card.dataset?.cropW);
  const h = clamp01(card.dataset?.cropH);

  if ([x, y, w, h].some((value) => value === null) || w <= 0 || h <= 0) return null;
  return {
    unit: 'percent',
    origin: 'topleft',
    x: Number(x.toFixed(6)),
    y: Number(y.toFixed(6)),
    w: Number(Math.min(w, 1 - x).toFixed(6)),
    h: Number(Math.min(h, 1 - y).toFixed(6)),
  };
}

function cropFromPreviewDataset(card) {
  const raw = card.dataset?.crop;
  if (!raw) return null;

  try {
    const crop = JSON.parse(raw);
    const x0 = clamp01(crop?.x0);
    const y0 = clamp01(crop?.y0);
    const x1 = clamp01(crop?.x1);
    const y1 = clamp01(crop?.y1);

    if ([x0, y0, x1, y1].some((value) => value === null)) return null;

    const x = Math.min(x0, x1);
    const y = Math.min(y0, y1);
    const w = Math.abs(x1 - x0);
    const h = Math.abs(y1 - y0);
    if (w <= 0 || h <= 0) return null;

    return {
      unit: 'percent',
      origin: 'topleft',
      x: Number(x.toFixed(6)),
      y: Number(y.toFixed(6)),
      w: Number(Math.min(w, 1 - x).toFixed(6)),
      h: Number(Math.min(h, 1 - y).toFixed(6)),
    };
  } catch {
    return null;
  }
}

function collectModificationsMap() {
  const modifications = {};

  for (const card of pageCards()) {
    const page = pageNumberFor(card);
    if (!page) continue;

    const crop = cropFromLegacyDataset(card) || cropFromPreviewDataset(card);
    if (crop) modifications[String(page)] = { crop };
  }

  return Object.keys(modifications).length ? modifications : null;
}

function getFilenameFromResponse(response, fallback) {
  const disposition = response.headers.get('Content-Disposition') || '';
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded?.[1]) return decodeURIComponent(encoded[1].replace(/^"|"$/g, ''));

  const quoted = disposition.match(/filename="([^"]+)"/i);
  if (quoted?.[1]) return quoted[1];

  const plain = disposition.match(/filename=([^;]+)/i);
  if (plain?.[1]) return plain[1].trim().replace(/^"|"$/g, '');

  return fallback;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function readErrorMessage(response) {
  const contentType = response.headers.get('Content-Type') || '';

  if (contentType.includes('application/json')) {
    try {
      const data = await response.json();
      return data?.error || data?.message || `Falha ao dividir PDF (HTTP ${response.status}).`;
    } catch {
      return `Falha ao dividir PDF (HTTP ${response.status}).`;
    }
  }

  const text = await response.text().catch(() => '');
  if (response.status === 400 && /Falha de Verifica/i.test(text)) {
    return 'Falha de verificacao CSRF. Atualize a pagina e tente novamente.';
  }
  return text ? text.slice(0, 180) : `Falha ao dividir PDF (HTTP ${response.status}).`;
}

async function postSplitAll(file) {
  const formData = new FormData();
  formData.append('file', file, file?.name || 'input.pdf');

  const rotations = collectRotationsMap();
  if (rotations) formData.append('rotations', JSON.stringify(rotations));

  const modifications = collectModificationsMap();
  if (modifications) formData.append('modificacoes', JSON.stringify(modifications));

  const headers = new Headers();
  const csrf = getCSRFToken();
  if (csrf) {
    headers.set('X-CSRFToken', csrf);
    formData.append('csrf_token', csrf);
  }
  headers.set('X-Requested-With', 'XMLHttpRequest');
  headers.set('Accept', 'application/zip, application/pdf, application/json;q=0.9, */*;q=0.1');

  const response = await fetch('/api/split', {
    method: 'POST',
    headers,
    body: formData,
    credentials: 'same-origin',
    cache: 'no-store',
  });

  if (!response.ok) {
    throw new Error(await readErrorMessage(response));
  }

  const blob = await response.blob();
  const filename = getFilenameFromResponse(response, 'paginas_divididas.zip');
  downloadBlob(blob, filename || 'paginas_divididas.zip');
}

async function onSplitAllClick(event) {
  event.preventDefault();

  if (busy) return;

  const file = getLoadedPdf();
  if (!file) {
    scheduleSync();
    mostrarMensagem('Selecione um PDF para dividir.', 'erro');
    return;
  }

  try {
    setBusy(true);
    await postSplitAll(file);
    mostrarMensagem('Paginas separadas com sucesso.', 'sucesso');
  } catch (error) {
    mostrarMensagem(error?.message || 'Falha ao dividir o PDF.', 'erro');
  } finally {
    setBusy(false);
  }
}

function bindSplitAllButton() {
  const button = splitAllButton();
  if (!button || button.dataset.splitAllBound === '1') return;

  button.addEventListener('click', onSplitAllClick);
  button.dataset.splitAllBound = '1';
}

function bindSyncTriggers() {
  const input = inputEl();
  const dropzone = $(DROPZONE_SELECTOR);
  const clearButton = $(CLEAR_SELECTOR);
  const preview = previewRoot();

  input?.addEventListener('change', scheduleSync);
  dropzone?.addEventListener('drop', scheduleSync);
  clearButton?.addEventListener('click', scheduleSync);
  preview?.addEventListener('preview:ready', scheduleSync);

  document.addEventListener('preview:ready', scheduleSync);
  document.addEventListener('gv:clear-files', scheduleSync);
  document.addEventListener('gv:clear-converter', scheduleSync);
  document.addEventListener('split:removePage', scheduleSync);
  window.addEventListener('pageshow', scheduleSync);

  if (preview) {
    const observer = new MutationObserver(scheduleSync);
    observer.observe(preview, { childList: true, subtree: true });
  }
}

function initSplitPage() {
  if (initialized) return;
  initialized = true;

  bindSplitAllButton();
  bindSyncTriggers();
  scheduleSync();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initSplitPage);
} else {
  initSplitPage();
}

window.syncSplitButtons = syncSplitButtons;
