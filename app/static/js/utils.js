// app/static/js/utils.js

// Lê o token CSRF do <meta name="csrf-token">
export function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

/**
 * Wrapper de fetch:
 * - Define Accept: application/json por padrão
 * - Mantém credentials 'same-origin'
 * - Retorna JSON quando o servidor envia JSON; senão retorna texto
 * - Lança erro com mensagem útil quando !res.ok
 *
 * Uso:
 *   const data = await xhrRequest('/api/preview', {
 *     method: 'POST',
 *     body: formData,
 *     headers: { 'X-CSRFToken': getCSRFToken() }
 *   });
 */
export async function xhrRequest(url, options = {}) {
  const { headers = {}, ...rest } = options;

  const finalHeaders = new Headers(headers);
  if (!finalHeaders.has('Accept')) finalHeaders.set('Accept', 'application/json');
  // Se body for FormData, não force Content-Type (o browser define o boundary)

  const res = await fetch(url, {
    credentials: 'same-origin',
    headers: finalHeaders,
    ...rest,
  });

  const ct = res.headers.get('content-type') || '';
  let payload = null;
  try {
    payload = ct.includes('application/json') ? await res.json() : await res.text();
  } catch (_) {
    // sem corpo válido — segue com null
  }

  if (!res.ok) {
    const msg =
      (payload && typeof payload === 'object' && payload.error) ||
      (typeof payload === 'string' && payload) ||
      `HTTP ${res.status}`;
    throw new Error(msg);
  }

  return payload;
}

// Mensagens (compatível com suas classes atuais: 'sucesso' / 'erro')
export function mostrarMensagem(msg, tipo = 'sucesso', timeout = 5000, elId = 'mensagem-feedback') {
  const el = document.getElementById(elId);
  if (!el) {
    // quando não há container, loga no console
    (tipo === 'erro' || tipo === 'error' ? console.error : console.log)(msg);
    return;
  }

  // remove classes conhecidas para evitar acúmulo
  el.classList.remove('sucesso', 'erro', 'aviso', 'info', 'hidden', 'msg-info', 'msg-warn', 'msg-error');
  el.textContent = msg;

  // mapeia sinônimos
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
  const el = document.getElementById(elId);
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
  const container = document.getElementById(containerId);
  const bar = document.getElementById(barId);
  if (!container || !bar) return;
  const p = Math.max(0, Math.min(100, Number(percent) || 0));
  container.classList.remove('hidden');
  bar.style.width = p + '%';
  bar.ariaValueNow = String(p);
}

export function resetarProgresso(containerId = 'progress-container', barId = 'progress-bar') {
  const container = document.getElementById(containerId);
  const bar = document.getElementById(barId);
  if (!container || !bar) return;
  bar.style.width = '0%';
  container.classList.add('hidden');
  bar.ariaValueNow = '0';
}