import { getCSRFToken, mostrarMensagem, mostrarLoading, atualizarProgresso, resetarProgresso } from './utils.js';

export const API_BASE = '/api/pdf';

export function uploadPdf({ url, files = [], pagesMap, rotations, modifications, onProgress }) {
  return new Promise((resolve, reject) => {
    mostrarLoading(true);
    resetarProgresso();

    const formData = new FormData();
    const useFilesField = url.includes('/merge') || files.length > 1;
    if (useFilesField) {
      files.forEach(f => formData.append('files', f));
    } else if (files.length === 1) {
      formData.append('file', files[0]);
    }
    if (pagesMap) formData.append('pagesMap', JSON.stringify(pagesMap));
    if (rotations) formData.append('rotations', JSON.stringify(rotations));
    if (modifications) formData.append('modificacoes', JSON.stringify(modifications));

    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.responseType = 'blob';
    xhr.setRequestHeader('X-CSRFToken', getCSRFToken());
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.withCredentials = true;

    xhr.upload.onprogress = e => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        atualizarProgresso(pct);
        if (onProgress) onProgress(pct);
      }
    };

    xhr.onload = () => {
      mostrarLoading(false);
      if (xhr.status === 200) {
        atualizarProgresso(100);
        resolve(xhr.response);
      } else {
        let err;
        try {
          err = JSON.parse(xhr.responseText).error;
        } catch {
          err = 'Erro no servidor.';
        }
        mostrarMensagem(err, 'erro');
        reject(new Error(err));
      }
      resetarProgresso();
    };

    xhr.onerror = () => {
      mostrarLoading(false);
      mostrarMensagem('Falha de rede', 'erro');
      resetarProgresso();
      reject(new Error('network'));
    };

    xhr.send(formData);
  });
}

export function convertFiles(files) {
  if (!files.length) {
    mostrarMensagem('Adicione pelo menos um arquivo para converter.', 'erro');
    return;
  }

  files.forEach(file => {
    uploadPdf({ url: `${API_BASE}/convert`, files: [file] })
      .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.name.replace(/\.[^/.]+$/, '') + '.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      mostrarMensagem(`Arquivo "${file.name}" convertido com sucesso!`);
      });
  });
}

export function mergePdfs(files) {
  if (files.length < 2) {
    mostrarMensagem('Adicione ao menos dois PDFs.', 'erro');
    return;
  }

  uploadPdf({ url: `${API_BASE}/merge`, files })
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'juntado.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      mostrarMensagem('PDFs juntados com sucesso!');
    });
}

export function extractPages(file, pages, rotations = []) {
  if (!file || !pages.length) {
    mostrarMensagem('Selecione um PDF e páginas válidas.', 'erro');
    return;
  }

  uploadPdf({
    url: `${API_BASE}/merge`,
    files: [file],
    pagesMap: [pages],
    rotations: [rotations]
  }).then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'pdf_selecionado.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
  });
}

export function splitPages(file, pages, rotations = []) {
  if (!file || !pages.length) {
    mostrarMensagem('Selecione um PDF e páginas válidas.', 'erro');
    return;
  }

  uploadPdf({
    url: `${API_BASE}/split`,
    files: [file],
    pagesMap: [pages],
    rotations: [rotations]
  }).then(blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'pdf_dividido.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    mostrarMensagem('PDF dividido com sucesso!', 'sucesso');
  });
}

export function splitFile(file) {
  if (!file) {
    mostrarMensagem('Selecione um PDF.', 'erro');
    return;
  }

  uploadPdf({ url: `${API_BASE}/split`, files: [file] }).then(blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'pdf_dividido.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    mostrarMensagem('PDF dividido com sucesso!');
  });
}

export function compressFile(file, rotation = 0) {
  if (!file) {
    mostrarMensagem('Selecione um PDF.', 'erro');
    return;
  }

  uploadPdf({
    url: `${API_BASE}/compress`,
    files: [file],
    modifications: { rotate: rotation }
  }).then(blob => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = file.name.replace(/\.[^.]+$/, '') + '_comprimido.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    mostrarMensagem('PDF comprimido com sucesso!', 'sucesso');
  });
}
