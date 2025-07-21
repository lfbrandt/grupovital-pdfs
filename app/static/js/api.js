import { getCSRFToken, mostrarMensagem, mostrarLoading, atualizarProgresso, resetarProgresso } from './utils.js';

function xhrRequest(url, formData, onSuccess) {
  mostrarLoading(true);
  resetarProgresso();

  const xhr = new XMLHttpRequest();
  xhr.open('POST', url);
  xhr.responseType = 'blob';
  xhr.setRequestHeader('X-CSRFToken', getCSRFToken());
  xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
  xhr.withCredentials = true;

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
      let err;
      try {
        err = JSON.parse(xhr.responseText).error;
      } catch {
        err = 'Erro no servidor.';
      }
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

export function convertFiles(files) {
  if (!files.length) {
    mostrarMensagem('Adicione pelo menos um arquivo para converter.', 'erro');
    return;
  }

  files.forEach(file => {
    const form = new FormData();
    form.append('file', file);
    xhrRequest('/api/convert', form, blob => {
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

  const form = new FormData();
  files.forEach(f => form.append('files', f));
  xhrRequest('/api/merge', form, blob => {
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

  const form = new FormData();
  form.append('files', file);
  form.append('pagesMap', JSON.stringify([pages]));
  form.append('rotations', JSON.stringify([rotations]));
  xhrRequest('/api/merge', form, blob => {
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

  const form = new FormData();
  form.append('file', file);
  form.append('pages', JSON.stringify(pages));
  form.append('rotations', JSON.stringify(rotations));

  xhrRequest('/api/split', form, blob => {
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

  const form = new FormData();
  form.append('file', file);
  xhrRequest('/api/split', form, blob => {
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

export function compressFile(file, rotations = []) {
  if (!file) {
    mostrarMensagem('Selecione um PDF.', 'erro');
    return;
  }

  const form = new FormData();
  form.append('file', file);
  form.append('rotations', JSON.stringify(rotations));
  return fetch('/api/compress', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {
      'X-CSRFToken': getCSRFToken(),
      'X-Requested-With': 'XMLHttpRequest'
    },
    body: form
  })
    .then(res => {
      if (!res.ok) throw new Error(`Compressão falhou: ${res.status}`);
      return res.blob();
    })
    .then(blob => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      const base = file.name.replace(/\.[^/.]+$/, '');
      a.download = `${base}_comprimido.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      mostrarMensagem('PDF comprimido com sucesso!');
    })
    .catch(err => {
      console.error(err);
      mostrarMensagem('Erro na compressão.', 'erro');
    });
}
