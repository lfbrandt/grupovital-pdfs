// ================= Helpers locais (tolerantes) =================
function _metaCSRF() {
  const el1 = document.querySelector('meta[name="csrf-token"]');
  const el2 = document.querySelector('meta[name="csrf_token"]');
  return el1?.getAttribute('content') || el2?.getAttribute('content') || null;
}
function _cookieCSRF() {
  try {
    const m = document.cookie.match(/(?:^|;)\s*csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  } catch (_) { return null; }
}
function _getCSRFToken() {
  try { if (typeof getCSRFToken === 'function') return getCSRFToken(); } catch(_) {}
  return _metaCSRF() || _cookieCSRF();
}
const _ui = {
  msg: (texto, tipo=null) => {
    try {
      if (typeof mostrarMensagem === 'function') return mostrarMensagem(texto, tipo);
      if (typeof window !== 'undefined' && typeof window.mostrarMensagem === 'function') {
        return window.mostrarMensagem(texto, tipo);
      }
    } catch(_){}
    if (tipo === 'erro') console.error(texto);
    else console.log(texto);
  },
  progress: (pct) => {
    try {
      if (typeof atualizarProgresso === 'function') return atualizarProgresso(pct);
      if (typeof window !== 'undefined' && typeof window.atualizarProgresso === 'function') {
        return window.atualizarProgresso(pct);
      }
    } catch(_){}
  },
  reset: () => {
    try {
      if (typeof resetarProgresso === 'function') return resetarProgresso();
      if (typeof window !== 'undefined' && typeof window.resetarProgresso === 'function') {
        return window.resetarProgresso();
      }
    } catch(_){}
  },
  preview: (file, el) => {
    try {
      if (typeof previewPDF === 'function') return previewPDF(file, el);
      if (typeof window !== 'undefined' && typeof window.previewPDF === 'function') {
        return window.previewPDF(file, el);
      }
    } catch(_){}
  }
};

// ================= Utilitários =================
function isPdfResponse(xhr, blob) {
  const ct = xhr.getResponseHeader('Content-Type') || '';
  return ct.includes('application/pdf') || (blob && blob.type === 'application/pdf');
}
function getFilenameFromXHR(xhr, fallback) {
  const cd = xhr.getResponseHeader('Content-Disposition') || '';
  const m = cd.match(/filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i);
  let name = m ? decodeURIComponent(m[1] || m[2] || '') : '';
  return name || fallback || 'output.pdf';
}
function revokeLater(url) {
  try { setTimeout(() => URL.revokeObjectURL(url), 0); } catch(_) {}
}

// ================= XHR com progresso (Render-safe) =================
export function xhrRequest(url, formData, onSuccess) {
  const xhr = new XMLHttpRequest();
  xhr.open('POST', url, true);

  // *** ESSENCIAL no Render: cookies/sessão precisam ir junto (CSRF verifica sessão) ***
  xhr.withCredentials = true;

  // Esperamos binário (PDF/ZIP) ou JSON de erro
  xhr.responseType = 'blob';

  // CSRF + dica de AJAX
  const csrf = _getCSRFToken();
  if (csrf) xhr.setRequestHeader('X-CSRFToken', csrf);
  xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
  xhr.setRequestHeader('Accept', 'application/pdf, application/zip, application/octet-stream, application/json;q=0.9, */*;q=0.1');

  xhr.upload.onprogress = (evt) => {
    if (!evt.lengthComputable) return;
    const percent = Math.round((evt.loaded / evt.total) * 100);
    _ui.progress(percent);
  };

  xhr.onload = () => {
    const blob = xhr.response;
    const ok = xhr.status >= 200 && xhr.status < 300;

    if (ok) {
      try { onSuccess(blob, xhr); }
      catch (err) { _ui.msg(err?.message || 'Erro ao processar a resposta.', 'erro'); }
      return;
    }

    const ct = xhr.getResponseHeader('Content-Type') || '';

    // Ler resposta textual (HTML/JSON) a partir do blob
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || '');

      if (/application\/json/i.test(ct)) {
        try {
          const j = JSON.parse(text);
          _ui.msg(j?.error || j?.message || `Erro (HTTP ${xhr.status}).`, 'erro');
        } catch {
          _ui.msg(`Erro (HTTP ${xhr.status}).`, 'erro');
        }
        return;
      }

      if (/text\/html/i.test(ct) || /^<!doctype html/i.test(text)) {
        // Página de “Falha de Verificação” do backend (CSRF)
        const isCsrf = /Falha de Verifica/i.test(text) || /csrf/i.test(text);
        const msg = isCsrf
          ? 'Falha de Verificação (CSRF). Atualize a página e tente novamente.'
          : `Falha (HTTP ${xhr.status}).`;
        _ui.msg(msg, 'erro');
        return;
      }

      // Genérico: tenta extrair algo útil, senão mostra status
      const short = text.trim().slice(0, 300);
      _ui.msg(short || `Falha (HTTP ${xhr.status}).`, 'erro');
    };
    reader.readAsText(blob);
  };

  xhr.onerror = () => _ui.msg('Falha de rede durante a requisição.', 'erro');
  xhr.onabort  = () => _ui.msg('Envio cancelado.', 'erro');

  // Importante: NÃO definir Content-Type manualmente com FormData (boundary automático)
  xhr.send(formData);
}

// ================= Converter =================
export function convertFiles(files, downloadName = 'convertidos.zip') {
  if (!files?.length) return _ui.msg('Selecione ao menos um arquivo para converter.', 'erro');
  const formData = new FormData();
  files.forEach(f => formData.append('files', f, f.name));

  _ui.reset();
  xhrRequest('/api/convert', formData, (blob, xhr) => {
    const filename = getFilenameFromXHR(xhr, downloadName);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    revokeLater(url);
    _ui.msg('Arquivos convertidos com sucesso!', 'sucesso');
  });
}

// ================= Merge =================
export function mergeFiles(files, pagesMap, rotations, downloadNameOrOptions = 'merged.pdf') {
  if (!files || files.length < 2) return _ui.msg('Selecione ao menos dois PDFs para juntar.', 'erro');

  const opts = (typeof downloadNameOrOptions === 'object' && downloadNameOrOptions !== null)
    ? { downloadName: 'merged.pdf', ...downloadNameOrOptions }
    : { downloadName: downloadNameOrOptions };

  const formData = new FormData();
  files.forEach(f => formData.append('files', f, f.name));
  if (Array.isArray(pagesMap)) formData.append('pagesMap', JSON.stringify(pagesMap));
  if (Array.isArray(rotations)) formData.append('rotations', JSON.stringify(rotations));
  if (Array.isArray(opts.crops)) formData.append('crops', JSON.stringify(opts.crops));

  const previewEl   = opts.previewSelector   ? document.querySelector(opts.previewSelector)   : null;
  const linkEl      = opts.linkSelector      ? document.querySelector(opts.linkSelector)      : null;
  const containerEl = opts.containerSelector ? document.querySelector(opts.containerSelector) : null;

  if (previewEl) previewEl.innerHTML = '';
  if (containerEl) containerEl.classList.add('is-loading');
  _ui.reset();

  xhrRequest('/api/merge', formData, (blob, xhr) => {
    const filename = getFilenameFromXHR(xhr, opts.downloadName);

    if (previewEl) {
      const resultFile = new File([blob], filename, { type: 'application/pdf' });
      _ui.preview(resultFile, previewEl);
      _ui.msg('PDFs juntados com sucesso!', 'sucesso');
    } else {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      revokeLater(url);
      _ui.msg('PDFs juntados com sucesso!', 'sucesso');
    }

    if (linkEl) {
      const linkUrl = URL.createObjectURL(blob);
      linkEl.href = linkUrl; linkEl.download = filename;
    }
    if (containerEl) containerEl.classList.remove('is-loading');
  });
}

// ================= Split =================
// Se pages === null/undefined -> backend entende como "todas" (ZIP).
export function splitPages(file, pages = null, rotations = null, downloadName = 'split.zip', options = null) {
  if (!file) return _ui.msg('Selecione um PDF para dividir.', 'erro');

  const opts = (options && typeof options === 'object') ? options : {};
  const formData = new FormData();
  // >>> GARANTE NOME COM EXTENSÃO <<<
  formData.append('file', file, file?.name || 'input.pdf');

  if (pages !== null && pages !== undefined) formData.append('pages', JSON.stringify(pages));
  if (rotations !== null && rotations !== undefined) formData.append('rotations', JSON.stringify(rotations));

  if (opts.modificacoes) {
    formData.append('modificacoes', JSON.stringify(opts.modificacoes));
  } else if (Array.isArray(opts.crops) && opts.crops.length) {
    const mods = {};
    for (const c of opts.crops) {
      const p = Number.parseInt(c?.page ?? c?.pagina ?? c?.index ?? c?.i, 10);
      if (!Number.isFinite(p)) continue;
      const crop = { x:Number(c?.x)||0, y:Number(c?.y)||0, w:Number(c?.w)||0, h:Number(c?.h)||0, unit:'percent', origin:'topleft' };
      if (!mods[p]) mods[p] = {};
      mods[p].crop = crop;
    }
    if (Object.keys(mods).length) formData.append('modificacoes', JSON.stringify(mods));
  }

  _ui.reset();
  xhrRequest('/api/split', formData, (blob, xhr) => {
    const filename = getFilenameFromXHR(xhr, downloadName);

    if (opts.previewSelector) {
      const resultFile = new File([blob], filename, { type: isPdfResponse(xhr, blob) ? 'application/pdf' : 'application/zip' });
      const previewEl = document.querySelector(opts.previewSelector);
      if (isPdfResponse(xhr, blob)) _ui.preview(resultFile, previewEl);
      _ui.msg('PDF dividido com sucesso!', 'sucesso');
      return;
    }

    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    revokeLater(url);
    _ui.msg('PDF dividido com sucesso!', 'sucesso');
  });
}

// ================= Compress =================
export function compressFile(file, rotations, downloadNameSuffix = '_compressed.pdf', options = null) {
  if (!file) return _ui.msg('Selecione um PDF para comprimir.', 'erro');
  const opts = (options && typeof options === 'object') ? options : {};

  const formData = new FormData();
  // >>> GARANTE NOME COM EXTENSÃO <<<
  formData.append('file', file, file?.name || 'input.pdf');

  if (rotations !== null && rotations !== undefined) formData.append('rotations', JSON.stringify(rotations));
  if (Array.isArray(opts.pages) && opts.pages.length) formData.append('pages', JSON.stringify(opts.pages));

  const selectedProfile = opts.profile || (document.getElementById('profile')?.value) || 'equilibrio';
  formData.append('profile', selectedProfile);

  if (opts.modificacoes) formData.append('modificacoes', JSON.stringify(opts.modificacoes));

  const previewEl   = opts.previewSelector   ? document.querySelector(opts.previewSelector)   : null;
  const linkEl      = opts.linkSelector      ? document.querySelector(opts.linkSelector)      : null;
  const containerEl = opts.containerSelector ? document.querySelector(opts.containerSelector) : null;

  if (previewEl) previewEl.innerHTML = '';
  if (containerEl) containerEl.classList.add('is-loading');
  _ui.reset();

  xhrRequest('/api/compress', formData, (blob, xhr) => {
    const safeBase = (file.name && file.name.replace(/\.pdf$/i,'')) || 'output';
    const filename = getFilenameFromXHR(xhr, (safeBase + downloadNameSuffix));

    if (previewEl && isPdfResponse(xhr, blob)) {
      const resultFile = new File([blob], filename, { type: 'application/pdf' });
      _ui.preview(resultFile, previewEl);
      _ui.msg('PDF comprimido com sucesso!', 'sucesso');
    } else {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      revokeLater(url);
      _ui.msg('PDF comprimido com sucesso!', 'sucesso');
    }

    if (linkEl) {
      const linkUrl = URL.createObjectURL(blob);
      linkEl.href = linkUrl; linkEl.download = filename;
    }
    if (containerEl) containerEl.classList.remove('is-loading');
  });
}