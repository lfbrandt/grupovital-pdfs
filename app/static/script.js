let arquivosSelecionados = [];

function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

function mostrarMensagem(mensagem, tipo = 'sucesso') {
  const msgDiv = document.getElementById('mensagem-feedback');
  msgDiv.textContent = mensagem;
  msgDiv.className = tipo;
  msgDiv.style.display = 'block';
  setTimeout(() => { msgDiv.style.display = 'none'; }, 5000);
}

function mostrarLoading(mostrar = true) {
  const loadingDiv = document.getElementById('loading-spinner');
  if (loadingDiv) loadingDiv.style.display = mostrar ? 'block' : 'none';
}

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

function adicionarArquivo() {
  const input = document.getElementById('file-input');
  if (!input) return;
  arquivosSelecionados.push(...Array.from(input.files));
  input.value = '';
  atualizarLista();
}

function adicionarArquivoSplit() {
  const input = document.getElementById('file-input');
  if (!input) return;
  arquivosSelecionados = Array.from(input.files);
  input.value = '';
  atualizarLista();
}

function atualizarLista() {
  const lista = document.getElementById('lista-arquivos');
  if (!lista) return;
  lista.innerHTML = arquivosSelecionados.map(file => `<li>${file.name}</li>`).join('');
}

function enviarArquivosConverter() {
  if (arquivosSelecionados.length === 0) {
    mostrarMensagem('Adicione pelo menos um arquivo para converter.', 'erro');
    return;
  }

  arquivosSelecionados.forEach(file => {
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

  arquivosSelecionados = [];
  atualizarLista();
}

function enviarArquivosMerge() {
  if (arquivosSelecionados.length === 0) {
    mostrarMensagem('Adicione pelo menos um PDF.', 'erro');
    return;
  }
  mostrarLoading(true);
  resetarProgresso();

  const formData = new FormData();
  arquivosSelecionados.forEach(file => formData.append('files', file));

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
  arquivosSelecionados = [];
  atualizarLista();
}

function enviarArquivosSplit() {
  if (arquivosSelecionados.length !== 1) {
    mostrarMensagem('Selecione exatamente um arquivo PDF para dividir.', 'erro');
    return;
  }
  mostrarLoading(true);
  resetarProgresso();

  const formData = new FormData();
  formData.append('file', arquivosSelecionados[0]);

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
  arquivosSelecionados = [];
  atualizarLista();
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

// Configura eventos após o carregamento do DOM
document.addEventListener('DOMContentLoaded', () => {
  const fileInput = document.getElementById('file-input');
  const converterBtn = document.getElementById('converter-btn');
  const mergeBtn = document.getElementById('merge-btn');
  const splitBtn = document.getElementById('split-btn');
  const compressForm = document.querySelector('form[action="/api/compress"]');
  const dropzone = document.getElementById('dropzone');

  if (fileInput && converterBtn) {
    fileInput.addEventListener('change', adicionarArquivo);
    converterBtn.addEventListener('click', enviarArquivosConverter);
  }

  if (fileInput && mergeBtn) {
    fileInput.addEventListener('change', adicionarArquivo);
    mergeBtn.addEventListener('click', enviarArquivosMerge);
  }

  if (fileInput && splitBtn) {
    fileInput.addEventListener('change', adicionarArquivoSplit);
    splitBtn.addEventListener('click', enviarArquivosSplit);
  }

  if (compressForm) {
    compressForm.addEventListener('submit', enviarArquivoCompress);
  }

  if (dropzone) {
    dropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });

    dropzone.addEventListener('dragleave', () => {
      dropzone.classList.remove('dragover');
    });

    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      const arquivos = Array.from(e.dataTransfer.files);
      if (splitBtn) {
        arquivosSelecionados = arquivos;
      } else {
        arquivosSelecionados.push(...arquivos);
      }
      atualizarLista();
    });
  }
});