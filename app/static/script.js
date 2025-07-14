
function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

/* Feedback Messages */
function mostrarMensagem(mensagem, tipo = 'sucesso') {
  const msgDiv = document.getElementById('mensagem-feedback');
  if (!msgDiv) return;
  msgDiv.textContent = mensagem;
  msgDiv.classList.remove('sucesso', 'erro', 'hidden');
  msgDiv.classList.add(tipo);
  setTimeout(() => { msgDiv.classList.add('hidden'); }, 5000);
}

/* Loading Spinner */
function mostrarLoading(mostrar = true) {
  const loadingDiv = document.getElementById('loading-spinner');
  if (loadingDiv) {
    loadingDiv.classList[mostrar ? 'remove' : 'add']('hidden');
  }
}

/* Progress Bar */
function atualizarProgresso(percent) {
  const container = document.getElementById('progress-container');
  const bar = document.getElementById('progress-bar');
  if (!container || !bar) return;
  container.classList.remove('hidden');
  bar.style.width = percent + '%';
}

function resetarProgresso() {
  const container = document.getElementById('progress-container');
  const bar = document.getElementById('progress-bar');
  if (!container || !bar) return;
  bar.style.width = '0%';
  container.classList.add('hidden');
}


const modificacoesPorArquivo = [];
// No topo do script, declare essa variável global
let previewPdfUrl = null;


/**
 * Limpa o container de canvas e revoga a URL anterior
 */
function clearPdfCanvas() {
  const container = document.getElementById('pdf-canvas-container');
  if (container) container.innerHTML = '';

  if (previewPdfUrl) {
    URL.revokeObjectURL(previewPdfUrl);
    previewPdfUrl = null;
  }
}


/**
 * Renderiza todas as páginas de um PDF usando PDF.js
 */
function renderPDF(url) {
  const container = document.getElementById('pdf-canvas-container');
  if (!container) return;

  // PDF.js: carregando o documento
  const loadingTask = pdfjsLib.getDocument(url);
  loadingTask.promise.then(pdf => {
    for (let pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
      pdf.getPage(pageNum).then(page => {
        const viewport = page.getViewport({ scale: 1.0 });
        const canvas = document.createElement('canvas');
        canvas.width  = viewport.width;
        canvas.height = viewport.height;
        container.appendChild(canvas);

        const context = canvas.getContext('2d');
        page.render({ canvasContext: context, viewport });
      });
    }
  }).catch(err => {
    console.error('Erro ao carregar PDF:', err);
    mostrarMensagem('Não foi possível exibir o PDF', 'erro');
  });
}


/**
 * Exibe uma imagem no modal
 */
function renderImage(url) {
  const imgContainer = document.getElementById('img-preview-container');
  const imgPreview   = document.getElementById('img-preview');
  if (!imgContainer || !imgPreview) return;

  imgPreview.src = url;
  imgContainer.classList.remove('hidden');
}


/**
 * Abre o modal de preview para PDF ou imagens
 */
function openPreview(file) {
  const modal        = document.getElementById('preview-modal');
  const pdfContainer = document.getElementById('pdf-canvas-container');
  const imgContainer = document.getElementById('img-preview-container');

  if (!modal || !file) return;

  // Limpar previews anteriores
  clearPdfCanvas();
  if (imgContainer) imgContainer.classList.add('hidden');

  // Cria e guarda a URL do Blob para revogação posterior
  previewPdfUrl = URL.createObjectURL(file);

  // Exibe o modal
  modal.classList.remove('hidden');

  // Escolhe renderização por tipo MIME
  if (file.type === 'application/pdf') {
    renderPDF(previewPdfUrl);
  } else if (file.type.startsWith('image/')) {
    renderImage(previewPdfUrl);
  } else {
    mostrarMensagem('Formato não suportado para preview', 'erro');
    URL.revokeObjectURL(previewPdfUrl);
    previewPdfUrl = null;
  }
}


/**
 * Fecha o modal de preview e limpa tudo
 */
function closePreview() {
  const modal = document.getElementById('preview-modal');
  if (modal) modal.classList.add('hidden');
  clearPdfCanvas();
  const imgContainer = document.getElementById('img-preview-container');
  if (imgContainer) imgContainer.classList.add('hidden');
}


function fecharPreview() {
  const modal      = document.getElementById('preview-modal');
  const imgPreview = document.getElementById('img-preview');
  const pdfCont    = document.getElementById('pdf-preview');
  const imgCont    = document.getElementById('img-preview-container');

  modal.classList.add('hidden');

  clearPdfCanvas();
  if (imgPreview && imgPreview.src) {
    URL.revokeObjectURL(imgPreview.src);
    imgPreview.src = '';
  }

  if (pdfCont) {
    pdfCont.classList.add('hidden');
    pdfCont.classList.remove('preview-flex');
  }
  if (imgCont) imgCont.classList.add('hidden');

  modificacoesPorArquivo.length = 0;
}

function mostrarPreview(arquivos, aoConfirmar) {
  const modal      = document.getElementById('preview-modal');
  const list       = document.getElementById('preview-list');
  const pdfCont    = document.getElementById('pdf-preview');
  const imgCont    = document.getElementById('img-preview-container');
  const imgPreview = document.getElementById('img-preview');

  if (!modal || !list) {
    aoConfirmar();
    return;
  }

  list.innerHTML = '';
  clearPdfCanvas();
  imgPreview.src = '';
  if (pdfCont) {
    pdfCont.classList.add('hidden');
    pdfCont.classList.remove('preview-flex');
  }
  if (imgCont) imgCont.classList.add('hidden');
  modificacoesPorArquivo.length = 0;

  arquivos.forEach((f, i) => {
    modificacoesPorArquivo[i] = {};
    const li = document.createElement('li');
    li.textContent = f.name;
    const actions = document.createElement('div');

    const rot = document.createElement('button');
    rot.textContent = 'Girar';
    rot.onclick = () => rotacionarArquivo(i);
    const crop = document.createElement('button');
    crop.textContent = 'Recortar';
    crop.onclick = () => recortarArquivo(i);

    actions.append(rot, crop);
    li.append(actions);
    list.append(li);
  });

  const pdfFile = arquivos.find(f => f.type === 'application/pdf');
  if (pdfFile) {
    previewPdfUrl = URL.createObjectURL(pdfFile);
    renderPDF(previewPdfUrl);
    if (pdfCont) {
      pdfCont.classList.remove('hidden');
      pdfCont.classList.add('preview-flex');
    }
  } else if (arquivos.length === 1 && arquivos[0].type.startsWith('image/')) {
    const url = URL.createObjectURL(arquivos[0]);
    imgPreview.src = url;
    if (imgCont) imgCont.classList.remove('hidden');
  }

  document.getElementById('preview-cancel').onclick = fecharPreview;
  document.getElementById('preview-close').onclick  = fecharPreview;
  document.getElementById('preview-confirm').onclick = () => {
    fecharPreview();
    aoConfirmar();
  };

  modal.classList.remove('hidden');
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
      onChange: files => {
        if (!allowMultiple && files.length) {
          openPreview(files[0]);
        }
      }
    });

    if (!allowMultiple) {
      fileInput.addEventListener('change', e => {
        const f = e.target.files[0];
        if (f) openPreview(f);
      });
    }

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
