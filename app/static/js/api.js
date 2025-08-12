// app/static/js/api.js
import {
  getCSRFToken,
  mostrarMensagem,
  mostrarLoading,
  atualizarProgresso,
  resetarProgresso
} from './utils.js';
import { previewPDF } from './preview.js';

/* Helpers */
function getFilenameFromXHR(xhr, fallback = 'arquivo.pdf') {
  const cd = xhr.getResponseHeader('Content-Disposition') || '';
  const m = cd.match(/filename\*=UTF-8''([^;]+)|filename="?([^"]+)"?/i);
  try {
    const raw = (m && (m[1] || m[2])) || fallback;
    // decodifica %.. e remove aspas extras
    return decodeURIComponent(raw).replace(/^"+|"+$/g, '');
  } catch {
    return fallback;
  }
}
function isPdfResponse(xhr, blob) {
  const ct = (xhr.getResponseHeader('Content-Type') || '').toLowerCase();
  return ct.includes('pdf') || blob?.type === 'application/pdf';
}

/* 🚀 Utilitário XHR para todas as requisições de upload/download
   Agora o onSuccess recebe (blob, xhr) para podermos ler headers */
export function xhrRequest(url, formData, onSuccess) {
  mostrarLoading(true);
  resetarProgresso();

  const xhr = new XMLHttpRequest();
  xhr.open('POST', url);
  xhr.responseType = 'blob';
  xhr.setRequestHeader('X-CSRFToken', getCSRFToken());

  xhr.upload.onprogress = e => {
    if (e.lengthComputable) {
      atualizarProgresso(Math.round((e.loaded / e.total) * 100));
    }
  };

  xhr.onload = () => {
    mostrarLoading(false);
    if (xhr.status === 200) {
      atualizarProgresso(100);
      onSuccess(xhr.response, xhr); // <— envia blob + xhr
    } else {
      let err = 'Erro no servidor.';
      try {
        const reader = new FileReader();
        reader.onload = () => {
          try { err = JSON.parse(reader.result).error || err; } catch {}
          mostrarMensagem(err, 'erro');
        };
        reader.readAsText(xhr.response);
        return;
      } catch {}
      mostrarMensagem(err, 'erro');
    }
    resetarProgresso();
  };

  xhr.onerror = () => {
    mostrarLoading(false);
    mostrarMensagem('Falha de rede', 'erro');
    resetarProgresso();
  };

  xhr.send(formData);
}

/* ========= CONVERTER =========
   Preview + link de download (seletor opcional) */
export function convertFiles(
  files,
  previewSelector = '#preview-convert',
  linkSelector = '#download-link',
  containerSelector = '#link-download-container'
) {
  if (!files || !files.length) {
    mostrarMensagem('Selecione ao menos um arquivo para converter.', 'erro');
    return;
  }

  const file = files[0];
  const formData = new FormData();
  formData.append('file', file);

  const previewEl   = document.querySelector(previewSelector);
  const linkEl      = document.querySelector(linkSelector);
  const containerEl = document.querySelector(containerSelector);

  if (previewEl) previewEl.innerHTML = '';
  if (containerEl) containerEl.classList.add('hidden');

  xhrRequest('/api/convert', formData, (blob, xhr) => {
    const filename = getFilenameFromXHR(xhr, file.name.replace(/\.[^/.]+$/, '') + '.pdf');
    const url = URL.createObjectURL(blob);

    // link de download
    if (linkEl) {
      linkEl.href = url;
      linkEl.download = filename;
    }
    if (containerEl) containerEl.classList.remove('hidden');

    // preview somente se de fato for PDF
    if (previewEl && isPdfResponse(xhr, blob)) {
      const resultFile = new File([blob], filename, { type: 'application/pdf' });
      previewPDF(resultFile, previewEl);
    } else if (!isPdfResponse(xhr, blob)) {
      mostrarMensagem('A conversão gerou múltiplos arquivos (ZIP). Faça o download para ver todos.', 'info');
    }

    mostrarMensagem(`Arquivo "${file.name}" convertido com sucesso!`, 'sucesso');
  });
}

/* ========= MERGE =========
   Mantém assinatura antiga, mas aceita options como 4º parâmetro:
   { downloadName, previewSelector, linkSelector, containerSelector }  */
export function mergeFiles(files, pagesMap, rotations, downloadNameOrOptions = 'merged.pdf') {
  if (!files || files.length < 2) {
    mostrarMensagem('Selecione ao menos dois PDFs para juntar.', 'erro');
    return;
  }

  const opts = (typeof downloadNameOrOptions === 'object' && downloadNameOrOptions !== null)
    ? { downloadName: 'merged.pdf', ...downloadNameOrOptions }
    : { downloadName: downloadNameOrOptions };

  const formData = new FormData();
  files.forEach(f => formData.append('files', f, f.name));
  formData.append('pagesMap', JSON.stringify(pagesMap));
  formData.append('rotations', JSON.stringify(rotations));

  const previewEl   = opts.previewSelector   ? document.querySelector(opts.previewSelector)   : null;
  const linkEl      = opts.linkSelector      ? document.querySelector(opts.linkSelector)      : null;
  const containerEl = opts.containerSelector ? document.querySelector(opts.containerSelector) : null;

  if (previewEl) previewEl.innerHTML = '';
  if (containerEl) containerEl.classList.add('hidden');

  xhrRequest('/api/merge?flatten=true', formData, (blob, xhr) => {
    const filename = getFilenameFromXHR(xhr, opts.downloadName || 'merged.pdf');

    if (previewEl && isPdfResponse(xhr, blob)) {
      const url = URL.createObjectURL(blob);
      if (linkEl) { linkEl.href = url; linkEl.download = filename; }
      if (containerEl) containerEl.classList.remove('hidden');

      const resultFile = new File([blob], filename, { type: 'application/pdf' });
      previewPDF(resultFile, previewEl);
      mostrarMensagem('PDFs juntados com sucesso!', 'sucesso');
    } else {
      // download direto (comportamento antigo)
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      mostrarMensagem('PDFs juntados com sucesso!', 'sucesso');
    }
  });
}

/* ========= SPLIT =========
   Normalmente retorna ZIP; mantemos download direto. */
export function splitPages(file, pages, rotations, downloadName = 'split.pdf') {
  if (!file || !pages?.length) {
    mostrarMensagem('Selecione um PDF e páginas para dividir.', 'erro');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);
  formData.append('pages', JSON.stringify(pages));
  formData.append('rotations', JSON.stringify(rotations));

  xhrRequest('/api/split', formData, (blob, xhr) => {
    const filename = getFilenameFromXHR(xhr, downloadName);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    mostrarMensagem('PDF dividido com sucesso!', 'sucesso');
  });
}

/* ========= COMPRESS =========
   Aceita options como 4º parâmetro:
   {
     previewSelector, linkSelector, containerSelector,
     profile,            // opcional: "equilibrio" | "mais-leve" | "alta-qualidade" | "sem-perdas"
     modificacoes        // opcional: objeto com cortes etc. (será enviado em JSON)
   }
*/
export function compressFile(file, rotations, downloadNameSuffix = '_compressed.pdf', options = null) {
  if (!file) {
    mostrarMensagem('Selecione um PDF para comprimir.', 'erro');
    return;
  }

  const opts = (options && typeof options === 'object') ? options : {};

  const formData = new FormData();
  formData.append('file', file);
  formData.append('rotations', JSON.stringify(rotations || null));

  // 🔹 NOVO: perfil de compressão
  const selectedProfile =
    opts.profile ||
    (document.getElementById('profile')?.value) ||
    'equilibrio';
  formData.append('profile', selectedProfile);

  // 🔹 OPCIONAL: modificações (crop etc.)
  if (opts.modificacoes) {
    formData.append('modificacoes', JSON.stringify(opts.modificacoes));
  }

  const previewEl   = opts.previewSelector   ? document.querySelector(opts.previewSelector)   : null;
  const linkEl      = opts.linkSelector      ? document.querySelector(opts.linkSelector)      : null;
  const containerEl = opts.containerSelector ? document.querySelector(opts.containerSelector) : null;

  if (previewEl) previewEl.innerHTML = '';
  if (containerEl) containerEl.classList.add('hidden');

  xhrRequest('/api/compress', formData, (blob, xhr) => {
    const base = file.name.replace(/\.[^/.]+$/, '');
    const filename = getFilenameFromXHR(xhr, `${base}${downloadNameSuffix}`);

    if (previewEl && isPdfResponse(xhr, blob)) {
      const url = URL.createObjectURL(blob);
      if (linkEl) { linkEl.href = url; linkEl.download = filename; }
      if (containerEl) containerEl.classList.remove('hidden');

      const resultFile = new File([blob], filename, { type: 'application/pdf' });
      previewPDF(resultFile, previewEl);
      mostrarMensagem('PDF comprimido com sucesso!', 'sucesso');
    } else {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      mostrarMensagem('PDF comprimido com sucesso!', 'sucesso');
    }
  });
}