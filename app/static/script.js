
if (window.pdfjsLib) {
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.9.179/build/pdf.worker.min.js';
}

function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

/* Feedback Messages */
function mostrarMensagem(mensagem, tipo = 'sucesso') {
  const msgDiv = document.getElementById('mensagem-feedback');
  if (!msgDiv) return;
  msgDiv.textContent = mensagem;
  msgDiv.className = tipo;
  msgDiv.style.display = 'block';
  setTimeout(() => { msgDiv.style.display = 'none'; }, 5000);
}

/* Loading Spinner */
function mostrarLoading(mostrar = true) {
  const loadingDiv = document.getElementById('loading-spinner');
  if (loadingDiv) loadingDiv.style.display = mostrar ? 'block' : 'none';
}

/* Progress Bar */
function atualizarProgresso(percent) {
  const container = document.getElementById('progress-container');
  const bar = document.getElementById('progress-bar');
  if (!container || !bar) return;
  container.style.display = 'block';
  bar.style.width = percent + '%';
}

function resetarProgresso() {
  const container = document.getElementById('progress-container');
  const bar = document.getElementById('progress-bar');
  if (!container || !bar) return;
  bar.style.width = '0%';
  container.style.display = 'none';
}

/* File Operations */
function enviarArquivosConverter(files) {
  if (!files || files.length === 0) {
    mostrarMensagem('Adicione pelo menos um arquivo para converter.', 'erro');
    return;
  }

  files.forEach(file => {
    mostrarLoading(true);
    resetarProgresso();
    const formData = new FormData();
    formData.append('file', file);

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/convert');
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
        const blob = xhr.response;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = file.name.replace(/\.[^/.]+$/, '') + '.pdf';
        document.body.appendChild(a);
        a.click();
        a.remove();
        mostrarMensagem(`Arquivo "${file.name}" convertido com sucesso!`);
      } else {
        xhr.response.text().then(text => {
          let errMsg;
          try {
            errMsg = JSON.parse(text).error;
          } catch {
            errMsg = `Erro ao converter: ${file.name}`;
          }
          mostrarMensagem(errMsg, 'erro');
        });
      }
      resetarProgresso();
    };

    xhr.onerror = () => {
      mostrarLoading(false);
      mostrarMensagem(`Erro ao converter: ${file.name}`, 'erro');
      resetarProgresso();
    };

    xhr.send(formData);
  });
}

function enviarArquivosMerge(files) {
  if (!files || files.length === 0) {
    mostrarMensagem('Adicione pelo menos um PDF.', 'erro');
    return;
  }

  mostrarLoading(true);
  resetarProgresso();

  const formData = new FormData();
  files.forEach(file => formData.append('files', file));

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/merge');
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
      const url = URL.createObjectURL(xhr.response);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'pdf_juntado.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      mostrarMensagem('PDFs juntados com sucesso!');
    } else {
      xhr.response.text().then(text => {
        let errMsg;
        try {
          errMsg = JSON.parse(text).error;
        } catch {
          errMsg = 'Erro ao juntar os arquivos PDF.';
        }
        mostrarMensagem(errMsg, 'erro');
      });
    }
    resetarProgresso();
  };

  xhr.onerror = () => {
    mostrarLoading(false);
    mostrarMensagem('Erro ao juntar os arquivos PDF.', 'erro');
    resetarProgresso();
  };

  xhr.send(formData);
}

function enviarArquivosSplit(files) {
  if (!files || files.length !== 1) {
    mostrarMensagem('Selecione exatamente um arquivo PDF para dividir.', 'erro');
    return;
  }

  mostrarLoading(true);
  resetarProgresso();

  const formData = new FormData();
  formData.append('file', files[0]);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/split');
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
      const url = URL.createObjectURL(xhr.response);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'pdf_dividido.zip';
      document.body.appendChild(a);
      a.click();
      a.remove();
      mostrarMensagem('PDF dividido com sucesso!');
    } else {
      xhr.response.text().then(text => {
        let errMsg;
        try {
          errMsg = JSON.parse(text).error;
        } catch {
          errMsg = 'Erro ao dividir o PDF.';
        }
        mostrarMensagem(errMsg, 'erro');
      });
    }
    resetarProgresso();
  };

  xhr.onerror = () => {
    mostrarLoading(false);
    mostrarMensagem('Erro ao dividir o PDF.', 'erro');
    resetarProgresso();
  };

  xhr.send(formData);
}

function enviarArquivoCompress(event) {
  event.preventDefault();
  const input = document.getElementById('file-input');
  if (!input || input.files.length === 0) {
    mostrarMensagem('Escolha um arquivo para comprimir.', 'erro');
    return;
  }
  mostrarLoading(true);
  resetarProgresso();

  const formData = new FormData();
  formData.append('file', input.files[0]);

  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/compress');
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
      const url = URL.createObjectURL(xhr.response);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'comprimido.pdf';
      document.body.appendChild(a);
      a.click();
      a.remove();
      mostrarMensagem('PDF comprimido com sucesso!');
    } else {
      xhr.response.text().then(text => {
        let errMsg;
        try {
          errMsg = JSON.parse(text).error;
        } catch {
          errMsg = 'Erro ao comprimir o PDF.';
        }
        mostrarMensagem(errMsg, 'erro');
      });
    }
    resetarProgresso();
  };

  xhr.onerror = () => {
    mostrarLoading(false);
    mostrarMensagem('Erro ao comprimir o PDF.', 'erro');
    resetarProgresso();
  };

  xhr.send(formData);
}

/* DOM Ready */
document.addEventListener('DOMContentLoaded', () => {
  const fileInput   = document.getElementById('file-input');
  const dropzoneEl  = document.getElementById('dropzone');
  const fileList    = document.getElementById('lista-arquivos');
  const converterBtn = document.getElementById('converter-btn');
  const mergeBtn     = document.getElementById('merge-btn');
  const splitBtn     = document.getElementById('split-btn');
  const compressForm = document.querySelector('form[action="/api/compress"]');

  let dz;
  if (fileInput && dropzoneEl) {
    let exts = [];
    let allowMultiple = true;
    if (converterBtn) {
      exts = ['.doc','.docx','.odt','.ods','.odp','.jpg','.jpeg','.png','.csv','.xls','.xlsx'];
      allowMultiple = true;
    } else if (mergeBtn) {
      exts = ['.pdf'];
      allowMultiple = true;
    } else if (splitBtn) {
      exts = ['.pdf'];
      allowMultiple = false;
    } else if (compressForm) {
      exts = ['.pdf'];
      allowMultiple = false;
    }

    dz = createFileDropzone({
      dropzone: dropzoneEl,
      input: fileInput,
      list: fileList,
      extensions: exts,
      multiple: allowMultiple,
      onChange: () => {}
    });
  }

  if (converterBtn && fileInput) {
    converterBtn.addEventListener('click', () => {
      const files = dz ? dz.getFiles() : Array.from(fileInput.files);
      enviarArquivosConverter(files);
    });
  }

  if (mergeBtn && fileInput) {
    mergeBtn.addEventListener('click', () => {
      const files = dz ? dz.getFiles() : Array.from(fileInput.files);
      enviarArquivosMerge(files);
    });
  }

  if (splitBtn && fileInput) {
    splitBtn.addEventListener('click', () => {
      const files = dz ? dz.getFiles() : Array.from(fileInput.files);
      enviarArquivosSplit(files);
    });
  }

  if (compressForm) {
    compressForm.addEventListener('submit', event => {
      if (dz) {
        const files = dz.getFiles();
        if (files.length) {
          const dt = new DataTransfer();
          dt.items.add(files[0]);
          fileInput.files = dt.files;
        }
      }
      enviarArquivoCompress(event);
    });
  }
});
