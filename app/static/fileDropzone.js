// app/static/js/fileDropzone.js
export function createFileDropzone({ dropzone, input, extensions = [], multiple = true, onChange = () => {} }) {
  let files = [];

  // Atualiza input.files para submissão via form
  function updateInputFiles() {
    if (!input) return;
    const dt = new DataTransfer();
    files.forEach(f => dt.items.add(f));
    input.files = dt.files;
  }

  // Remove arquivo do array
  function removeFile(index) {
    if (index < 0 || index >= files.length) return;
    files.splice(index, 1);
    updateInputFiles();
    onChange(files);
  }

  // Move arquivo de uma posição para outra
  function moveFile(from, to) {
    if (from === to || from < 0 || from >= files.length || to < 0 || to >= files.length) return;
    const [item] = files.splice(from, 1);
    files.splice(to, 0, item);
    updateInputFiles();
    onChange(files);
  }

  // Valida extensão do arquivo
  function validExtension(file) {
    if (!extensions.length) return true;
    const ext = file.name.split('.').pop().toLowerCase();
    return extensions.includes(ext);
  }

  // Adiciona novos arquivos ao array
  function addFiles(newFiles) {
    const validFiles = Array.from(newFiles).filter(validExtension);
    if (!validFiles.length) return;
    files = multiple ? files.concat(validFiles) : [validFiles[0]];
    updateInputFiles();
    onChange(files);
  }

  // Listener no input[file]
  if (input) {
    input.addEventListener('change', e => {
      addFiles(e.target.files);
      input.value = '';
    });
  }

  // Eventos de drag'n'drop e clique na zona
  if (dropzone) {
    dropzone.addEventListener('click', () => {
      input && input.click();
    });
    dropzone.addEventListener('dragover', e => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });
    dropzone.addEventListener('dragleave', () => {
      dropzone.classList.remove('dragover');
    });
    dropzone.addEventListener('drop', e => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      addFiles(e.dataTransfer.files);
    });
  }

  return {
    getFiles: () => files.slice(),
    removeFile,
    moveFile,
    clear: () => {
      files = [];
      updateInputFiles();
      onChange(files);
    }
  };
}