/* Utils ESM shim — reexporta a API global de utils.js como módulos.
   Mantém uma única origem de verdade (utils.js UMD), evitando duplicação. */

import './utils.js'; // garante execução do IIFE que seta window.utils

const u = (typeof window !== 'undefined' && window.utils) || null;
if (!u) {
  throw new Error('Utils não disponível. Verifique se app/static/js/utils.js está sendo servido.');
}

// Exports nomeados (mesmos nomes da sua API atual)
export const getCSRFToken       = u.getCSRFToken;
export const withCSRFFromMeta   = u.withCSRFFromMeta;
export const xhrRequest         = u.xhrRequest;
export const mostrarMensagem    = u.mostrarMensagem;
export const mostrarLoading     = u.mostrarLoading;
export const atualizarProgresso = u.atualizarProgresso;
export const resetarProgresso   = u.resetarProgresso;
export const debounce           = u.debounce;
export const sleep              = u.sleep;

export const normalizeAngle     = u.normalizeAngle;
export const getThumbWidth      = u.getThumbWidth;
export const getMediaSize       = u.getMediaSize;
export const containScale       = u.containScale;
export const fitRotateMedia     = u.fitRotateMedia;
export const getCropBoxAbs      = u.getCropBoxAbs;
export const collectPagesRotsCropsAllOrSelection = u.collectPagesRotsCropsAllOrSelection;

// Export default da API inteira
export default u;