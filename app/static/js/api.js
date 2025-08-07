// app/static/js/api.js
import {
  getCSRFToken,
  mostrarMensagem,
  mostrarLoading,
  atualizarProgresso,
  resetarProgresso
} from './utils.js';
import { previewPDF } from './preview.js';

// 🚀 Utilitário XHR para todas as requisições de upload/download
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
      onSuccess(xhr.response);
    } else {
      let err = 'Erro no servidor.';
      try {
        err = JSON.parse(xhr.responseText).error || err;
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

// Convert files to PDF (preview + download)
export function convertFiles(files,
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

  const previewEl = document.querySelector(previewSelector);
  const linkEl = document.querySelector(linkSelector);
  const containerEl = document.querySelector(containerSelector);

  previewEl.innerHTML = '';
  containerEl.classList.add('hidden');

  xhrRequest('/api/convert', formData, blob => {
    const url = URL.createObjectURL(blob);
    linkEl.href = url;
    linkEl.download = file.name.replace(/\.[^/.]+$/, '') + '.pdf';
    containerEl.classList.remove('hidden');

    previewPDF(new File([blob], linkEl.download, { type: 'application/pdf' }), previewEl);
    mostrarMensagem(`Arquivo "${file.name}" convertido com sucesso!`, 'sucesso');
  });
}

// Merge multiple PDFs into one
export function mergeFiles(
  files,
  pagesMap,
  rotations,
  downloadName = 'merged.pdf'
) {
  if (!files || files.length < 2) {
    mostrarMensagem('Selecione ao menos dois PDFs para juntar.', 'erro');
    return;
  }

  const formData = new FormData();
  files.forEach(f => formData.append('files', f, f.name));
  formData.append('pagesMap', JSON.stringify(pagesMap));
  formData.append('rotations', JSON.stringify(rotations));

  xhrRequest('/api/merge?flatten=true', formData, blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = downloadName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    mostrarMensagem('PDFs juntados com sucesso!', 'sucesso');
  });
}

// Split PDF by selected pages
export function splitPages(
  file,
  pages,
  rotations,
  downloadName = 'split.pdf'
) {
  if (!file || !pages?.length) {
    mostrarMensagem('Selecione um PDF e páginas para dividir.', 'erro');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);
  formData.append('pages', JSON.stringify(pages));
  formData.append('rotations', JSON.stringify(rotations));

  xhrRequest('/api/split', formData, blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = downloadName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    mostrarMensagem('PDF dividido com sucesso!', 'sucesso');
  });
}

// Compress a PDF file
export function compressFile(
  file,
  rotations,
  downloadNameSuffix = '_compressed.pdf'
) {
  if (!file) {
    mostrarMensagem('Selecione um PDF para comprimir.', 'erro');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);
  formData.append('rotations', JSON.stringify(rotations));

  xhrRequest('/api/compress', formData, blob => {
    const base = file.name.replace(/\.[^/.]+$/, '');
    const downloadName = `${base}${downloadNameSuffix}`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = downloadName;
    document.body.appendChild(a);
    a.click();
    a.remove();
    mostrarMensagem('PDF comprimido com sucesso!', 'sucesso');
  });
}