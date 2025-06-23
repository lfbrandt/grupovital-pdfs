
function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

/* Feedback Messages */
function mostrarMensagem(mensagem, tipo = 'sucesso') {
  const msgDiv = document.getElementById('mensagem-feedback');
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
Pronto! Escolhi a abordagem com o createFileDropzone, que encapsula a lógica de seleção, drag-and-drop e lista de arquivos de forma modular. Removi os conflitos e comentários-resíduos, e o código agora está limpo e consistente. Dê uma conferida e me avise se quiser algum ajuste!






Você disse:
chat refaça


O ChatGPT disse:

Pensou por 6 segundos
Claro! Só pra eu entender melhor: você quer que eu refaça a integração usando a abordagem de escutar o change no input (sem usar o createFileDropzone), ou prefere outro ajuste específico no código? Pode me dizer qual parte você quer alterar?







Você disse:
quero que vc me mande de novo o codigo 


O ChatGPT disse:
javascript
Copiar
Editar
/* CSRF Token Retrieval */
function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

/* Feedback Messages */
function mostrarMensagem(mensagem, tipo = 'sucesso') {
  const msgDiv = document.getElementById('mensagem-feedback');
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
  const input = document.querySelector('input[name="file"]');
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
  const dropzoneElem = document.getElementById('dropzone');
  const fileInput   = document.getElementById('file-input');
  const listElem    = document.getElementById('lista-arquivos');

  const converterBtn = document.getElementById('converter-btn');
  const mergeBtn     = document.getElementById('merge-btn');
  const splitBtn     = document.getElementById('split-btn');
  const compressForm = document.querySelector('form[action="/api/compress"]');

  if (converterBtn && fileInput && listElem && dropzoneElem) {
    const handler = createFileDropzone({
      dropzone:   dropzoneElem,
      input:      fileInput,
      list:       listElem,
      extensions: ['.doc','.docx','.odt','.ods','.odp','.jpg','.jpeg','.png','.csv','.xls','.xlsx'],
      multiple:   true
    });
    converterBtn.addEventListener('click', () => {
      enviarArquivosConverter(handler.getFiles());
      handler.clear();
    });
  }

  if (mergeBtn && fileInput && listElem && dropzoneElem) {
    const handlerMerge = createFileDropzone({
      dropzone:   dropzoneElem,
      input:      fileInput,
      list:       listElem,
      extensions: ['.pdf'],
      multiple:   true
    });
    mergeBtn.addEventListener('click', () => {
      enviarArquivosMerge(handlerMerge.getFiles());
      handlerMerge.clear();
    });
  }

  if (splitBtn && fileInput && listElem && dropzoneElem) {
    const handlerSplit = createFileDropzone({
      dropzone:   dropzoneElem,
      input:      fileInput,
      list:       listElem,
      extensions: ['.pdf'],
      multiple:   false
    });
    splitBtn.addEventListener('click', () => {
      enviarArquivosSplit(handlerSplit.getFiles());
      handlerSplit.clear();
    });
  }

  if (compressForm) {
    compressForm.addEventListener('submit', enviarArquivoCompress);
  }
});