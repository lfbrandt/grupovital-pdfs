export function createFileDropzone(options) {
  const {
    dropzone,
    input,
    list,
    extensions = [],
    multiple = true,
    onChange = () => {}
  } = options;

  let files = [];

  function updateInputFiles() {
    if (!input) return;
    const dt = new DataTransfer();
    files.forEach(f => dt.items.add(f));
    input.files = dt.files;
  }

  function moverArquivo(index, offset) {
    const novoIndex = index + offset;
    if (novoIndex < 0 || novoIndex >= files.length) return;
    moveFile(index, novoIndex);
  }

  function moveFile(from, to) {
    if (from === to || from < 0 || from >= files.length || to < 0 || to >= files.length) return;
    const [item] = files.splice(from, 1);
    files.splice(to, 0, item);
    updateInputFiles();
    updateList();
    onChange(files);
  }

  function updateList() {
    if (!list) return;
    list.innerHTML = '';
    files.forEach((f, i) => {
      const li = document.createElement('li');

      const span = document.createElement('span');
      span.textContent = f.name;
      li.appendChild(span);

      const actions = document.createElement('div');
      actions.className = 'actions';

      const up = document.createElement('button');
      up.className = 'icon-btn';
      up.textContent = '↑';
      up.addEventListener('click', () => moverArquivo(i, -1));

      const down = document.createElement('button');
      down.className = 'icon-btn';
      down.textContent = '↓';
      down.addEventListener('click', () => moverArquivo(i, 1));

      const del = document.createElement('button');
      del.className = 'icon-btn';
      del.textContent = '🗑';
      del.addEventListener('click', () => removerArquivo(i));

      actions.appendChild(up);
      actions.appendChild(down);
      actions.appendChild(del);

      li.appendChild(actions);
      list.appendChild(li);
    });
  }

  function removerArquivo(index) {
    files.splice(index, 1);
    updateInputFiles();
    updateList();
    onChange(files);
  }

  function validExtension(file) {
    if (extensions.length === 0) return true;
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    return extensions.includes(ext);
  }

  function addFiles(newFiles) {
    const validFiles = Array.from(newFiles).filter(validExtension);
    if (multiple) {
      files = files.concat(validFiles);
    } else if (validFiles.length) {
      files = [validFiles[0]];
    }
    updateInputFiles();
    updateList();
    onChange(files);
  }

  if (input) {
    input.addEventListener('change', e => {
      addFiles(e.target.files);
      input.value = '';
    });
  }

  if (dropzone) {
    dropzone.addEventListener('click', () => { if (input) input.click(); });
    dropzone.addEventListener('dragover', e => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', e => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      addFiles(e.dataTransfer.files);
    });
  }

  return {
    getFiles: () => files.slice(),
    removeFile: removerArquivo,
    moveFile,
    clear: () => { files = []; updateInputFiles(); updateList(); onChange(files); }
  };
}
