
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

const modificacoesPorArquivo = [];

function fecharPreview() {
  const modal = document.getElementById('preview-modal');
  const previewFrame = document.getElementById('pdf-frame');
  const previewContainer = document.getElementById('pdf-preview');
  if (modal) {
    modal.classList.add('hidden');
    modal.style.display = 'none';
  }
  if (previewFrame && previewFrame.src) {
    URL.revokeObjectURL(previewFrame.src);
    previewFrame.src = '';
  }
  if (previewContainer) previewContainer.style.display = 'none';
  modificacoesPorArquivo.length = 0;
}

function mostrarPreview(arquivos, aoConfirmar) {
  const modal = document.getElementById('preview-modal');
  const list = document.getElementById('preview-list');
  const previewContainer = document.getElementById('pdf-preview');
  const previewFrame = document.getElementById('pdf-frame');
  if (!modal || !list) {
    aoConfirmar();
    return;
  }
  list.innerHTML = '';
  if (previewFrame) previewFrame.src = '';
  if (previewContainer) previewContainer.style.display = 'none';
  modificacoesPorArquivo.length = 0;
  const pdfFile = arquivos.find(f => f.type === 'application/pdf');
  if (pdfFile && previewFrame && previewContainer) {
    const url = URL.createObjectURL(pdfFile);
    previewFrame.src = url;
    previewContainer.style.display = 'block';
  }
  arquivos.forEach((f, i) => {
    modificacoesPorArquivo[i] = {};
    const li = document.createElement('li');
    const span = document.createElement('span');
    span.textContent = f.name;
    const actions = document.createElement('div');

    const rot = document.createElement('button');
    rot.textContent = 'Girar';
    rot.addEventListener('click', () => rotacionarArquivo(i));
    actions.appendChild(rot);

    const crop = document.createElement('button');
    crop.textContent = 'Recortar';
    crop.addEventListener('click', () => recortarArquivo(i));
    actions.appendChild(crop);

    li.appendChild(span);
    li.appendChild(actions);
    list.appendChild(li);
  });
  const cancelBtn = document.getElementById('preview-cancel');
  const confirmBtn = document.getElementById('preview-confirm');
  const closeBtn = document.getElementById('preview-close');

  if (cancelBtn) cancelBtn.onclick = fecharPreview;
  if (closeBtn) closeBtn.onclick = fecharPreview;
  if (confirmBtn) confirmBtn.onclick = () => {
    fecharPreview();
    aoConfirmar();
  };
  modal.classList.remove('hidden');
  modal.style.display = 'flex';
}

function rotacionarArquivo(index) {
  modificacoesPorArquivo[index] = modificacoesPorArquivo[index] || {};
  const val = modificacoesPorArquivo[index].rotate || 0;
  modificacoesPorArquivo[index].rotate = (val + 90) % 360;
}

function recortarArquivo(index) {
  const llx = parseInt(prompt('Coordenada X inferior esquerda:', '0')) || 0;
  const lly = parseInt(prompt('Coordenada Y inferior esquerda:', '0')) || 0;
  const urx = parseInt(prompt('Coordenada X superior direita:', '0')) || 0;
  const ury = parseInt(prompt('Coordenada Y superior direita:', '0')) || 0;
  modificacoesPorArquivo[index] = modificacoesPorArquivo[index] || {};
  modificacoesPorArquivo[index].crop = [llx, lly, urx, ury];
}

/* File Operations */
function enviarArquivosConverter(files) {
  if (!files || files.length === 0) {
    mostrarMensagem('Adicione pelo menos um arquivo para converter.', 'erro');
    return;
  }

  files.forEach((file, idx) => {
    mostrarLoading(true);
    resetarProgresso();
    const formData = new FormData();
    formData.append('file', file);
    formData.append('modificacoes', JSON.stringify(modificacoesPorArquivo[idx] || {}));

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
  formData.append('modificacoes', JSON.stringify(modificacoesPorArquivo[0] || {}));

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
  formData.append('modificacoes', JSON.stringify(modificacoesPorArquivo[0] || {}));

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
  formData.append('modificacoes', JSON.stringify(modificacoesPorArquivo[0] || {}));

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
  const previewModal = document.getElementById('preview-modal');
  if (previewModal) {
    previewModal.classList.add('hidden');
    previewModal.addEventListener('click', e => {
      if (e.target === previewModal) fecharPreview();
    });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && !previewModal.classList.contains('hidden')) {
        fecharPreview();
      }
    });
  }
  const fileInput   = document.getElementById('file-input');
  const dropzoneEl  = document.getElementById('dropzone');
  const fileList    = document.getElementById('lista-arquivos');
  const converterBtn = document.getElementById('converter-btn');
  const mergeBtn     = document.getElementById('merge-btn');
  const splitBtn     = document.getElementById('split-btn');
  const compressForm = document.querySelector('form[action="/api/compress"]');
  const previewBtn   = document.getElementById('preview-btn');

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
      onChange: () => {
        const files = dz.getFiles();
        const modal = document.getElementById('preview-modal');
        if (modal) {
          if (files.length > 0) {
            modal.classList.remove('hidden');
          } else {
            modal.classList.add('hidden');
          }
        }
      }
    });

    const cancelBtn  = document.getElementById('preview-cancel');
    const confirmBtn = document.getElementById('preview-confirm');

    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => {
        const modal = document.getElementById('preview-modal');
        if (modal) modal.classList.add('hidden');
        if (dz) dz.clear();
      });
    }

    if (confirmBtn) {
      confirmBtn.addEventListener('click', () => {
        const modal = document.getElementById('preview-modal');
        if (modal) modal.classList.add('hidden');
      });
    }
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
      event.preventDefault();
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

  if (previewBtn && fileInput) {
    previewBtn.addEventListener('click', () => {
      const files = dz ? dz.getFiles() : Array.from(fileInput.files);
      if (!files.length) {
        mostrarMensagem('Adicione pelo menos um arquivo para visualizar.', 'erro');
        return;
      }
      let action;
      if (converterBtn) action = () => enviarArquivosConverter(files);
      else if (mergeBtn) action = () => enviarArquivosMerge(files);
      else if (splitBtn) action = () => enviarArquivosSplit(files);
      else if (compressForm) action = () => enviarArquivoCompress({preventDefault(){}});
      mostrarPreview(files, action);
    });
  }
});
