/**
 * Dropzone leve sem dependências. Suporta duas assinaturas:
 * 1) createFileDropzone(dropEl, { multiple, onAdd, onClear, onChange, onReject, maxFiles, extensions })
 * 2) createFileDropzone({ dropzone, input, extensions, multiple, onChange, onAdd, onClear, onReject, clearButton, maxFiles })
 *
 * - Lê dinamicamente os tipos aceitos do `accept` do <input>
 *   ou do `data-extensions` do container (ex.: ".pdf,.docx,application/pdf,image/*").
 * - Filtra por extensão e/ou MIME (inclui curingas "image/*").
 * - Usa DataTransfer para manter a FileList do input sincronizada.
 * - Evita duplicados (nome+tamanho+lastModified).
 * - Suporta limite opcional de arquivos (maxFiles).
 */

export function createFileDropzone(arg1, arg2 = {}) {
  // ---- Normalização dos parâmetros (aceita 2 assinaturas) ----
  let opts = {};
  if (arg1 instanceof Element) {
    opts = { dropzone: arg1, ...arg2 };
  } else if (arg1 && typeof arg1 === 'object') {
    opts = { ...arg1 };
  } else {
    throw new Error('createFileDropzone: argumentos inválidos.');
  }

  const dropzone = opts.dropzone || null;
  const input = opts.input || (dropzone ? dropzone.querySelector('input[type="file"]') : null);
  const clearButton = opts.clearButton || document.getElementById('btn-clear-all');

  const onAdd    = typeof opts.onAdd    === 'function' ? opts.onAdd    : () => {};
  const onClear  = typeof opts.onClear  === 'function' ? opts.onClear  : () => {};
  const onChange = typeof opts.onChange === 'function' ? opts.onChange : () => {};
  const onReject = typeof opts.onReject === 'function' ? opts.onReject : () => {};

  const multiple = opts.multiple !== false; // default true
  const presetExtensions = Array.isArray(opts.extensions) ? opts.extensions : [];
  const maxFiles = Number.isFinite(opts.maxFiles)
    ? Number(opts.maxFiles)
    : (() => {
        const attr = dropzone?.getAttribute('data-max-files');
        return attr ? Number(attr) : undefined;
      })();

  if (!dropzone) throw new Error('createFileDropzone: "dropzone" não informado.');
  if (!input) throw new Error('createFileDropzone: input[type="file"] não encontrado no dropzone.');

  // Garante múltipla seleção conforme opção
  input.multiple = !!multiple;

  // ---- Estado interno ----
  /** @type {File[]} */
  let files = [];
  // Índice rápido para evitar duplicatas
  const seen = new Set(); // key = `${name}|${size}|${lastModified}`

  // ---- Helpers de accept/extensions ----
  function parseAcceptString(str) {
    // retorna array de tokens limpos (".pdf", "application/pdf", "image/*")
    return (str || '')
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
      .map(s => s.toLowerCase());
  }

  function normalizeTokens(tokens) {
    // Aceita ".pdf", "pdf" → ".pdf"
    // Mantém "application/pdf" e "image/*" como estão
    return (tokens || []).map(tok => {
      const t = String(tok || '').trim().toLowerCase();
      if (!t) return '';
      if (t.startsWith('.') || t.includes('/')) return t; // já é extensão com ponto OU MIME/wildcard
      return `.${t}`; // "pdf" -> ".pdf"
    }).filter(Boolean);
  }

  let runtimeAcceptOverride = null; // quando setAccept() for chamado

  function currentAcceptList() {
    if (runtimeAcceptOverride && runtimeAcceptOverride.length) {
      return normalizeTokens(runtimeAcceptOverride);
    }
    if (presetExtensions.length) {
      return normalizeTokens(presetExtensions);
    }
    const attrAccept = input.getAttribute('accept');
    if (attrAccept && attrAccept.trim()) return parseAcceptString(attrAccept);
    const dzExt = dropzone.getAttribute('data-extensions');
    if (dzExt && dzExt.trim()) return parseAcceptString(dzExt);
    return []; // sem restrições
  }

  function matchesAccept(file) {
    const accept = currentAcceptList();
    if (!accept.length) return true;

    const name = (file.name || '').toLowerCase();
    const ext = '.' + (name.split('.').pop() || '');
    const type = (file.type || '').toLowerCase();

    return accept.some(token => {
      if (token.startsWith('.')) {
        // por extensão
        return token === ext;
      }
      if (token.endsWith('/*')) {
        // curinga por prefixo de MIME
        const prefix = token.slice(0, -1); // mantém a barra
        return type.startsWith(prefix);
      }
      // match MIME exato
      return type === token;
    });
  }

  function fileKey(f) {
    return `${(f.name || '').toLowerCase()}|${f.size}|${f.lastModified || 0}`;
  }

  // ---- Sincronização com o <input> ----
  function updateInputFiles() {
    try {
      const dt = new DataTransfer();
      files.forEach(f => dt.items.add(f));
      input.files = dt.files;
    } catch {
      // browsers muito antigos: ignorar
    }
  }

  // ---- API pública ----
  function removeFile(index) {
    if (index < 0 || index >= files.length) return;
    const removed = files.splice(index, 1)[0];
    if (removed) seen.delete(fileKey(removed));
    updateInputFiles();
    onChange(files.slice());
  }

  function moveFile(from, to) {
    if (from === to || from < 0 || from >= files.length || to < 0 || to >= files.length) return;
    const [item] = files.splice(from, 1);
    files.splice(to, 0, item);
    updateInputFiles();
    onChange(files.slice());
  }

  function clear() {
    files = [];
    seen.clear();
    // limpa seleção e permite reescolher o mesmo arquivo
    try { input.value = ''; } catch {}
    try {
      const dt = new DataTransfer();
      input.files = dt.files;
    } catch {}
    onClear();
    onChange(files.slice());
  }

  function addFiles(fileList) {
    const list = Array.from(fileList || []);
    if (!list.length) return { accepted: [], rejected: [] };

    const accepted = [];
    const rejected = [];
    for (const f of list) {
      if (Number.isFinite(maxFiles) && files.length >= maxFiles) {
        onReject(f, 'max_files');
        rejected.push({ file: f, reason: 'max_files' });
        continue;
      }

      if (!matchesAccept(f)) {
        onReject(f, 'mime_mismatch');
        rejected.push({ file: f, reason: 'mime_mismatch' });
        continue;
      }

      const key = fileKey(f);
      if (seen.has(key)) {
        onReject(f, 'duplicate');
        rejected.push({ file: f, reason: 'duplicate' });
        continue;
      }

      if (multiple) {
        files.push(f);
      } else {
        files = [f];
        seen.clear();
      }
      seen.add(key);
      accepted.push(f);
    }

    if (!accepted.length && !rejected.length) {
      return { accepted: [], rejected: [] };
    }

    updateInputFiles();
    // callback por arquivo aceito
    accepted.forEach(onAdd);
    // callback por mudança geral
    onChange(files.slice());

    return { accepted, rejected };
  }

  // ---- Listeners ----
  function onInputChange(e) {
    addFiles(e.target.files);
    // não limpar value aqui (evita perder a FileList setada via DataTransfer)
  }

  input.addEventListener('change', onInputChange);

  // Dropzone: clique abre seletor
  dropzone.addEventListener('click', () => {
    try { input.value = ''; } catch {}
    input && input.click();
  });

  dropzone.addEventListener('dragenter', e => {
    e.preventDefault();
    dropzone.classList.add('is-dragover');
  });
  dropzone.addEventListener('dragover', e => {
    e.preventDefault();
    dropzone.classList.add('is-dragover');
  });
  dropzone.addEventListener('dragleave', () => {
    dropzone.classList.remove('is-dragover');
  });
  dropzone.addEventListener('drop', e => {
    e.preventDefault();
    dropzone.classList.remove('is-dragover');
    if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
  });

  // Botão limpar (opcional)
  if (clearButton) {
    clearButton.addEventListener('click', () => clear());
  }

  // API pública
  const api = {
    getFiles: () => files.slice(),
    removeFile,
    moveFile,
    clear,
    addFiles,
    setAccept(tokens) {
      runtimeAcceptOverride = Array.isArray(tokens) ? tokens.slice() : [];
      // Atualiza atributos visuais/semânticos também
      const normalized = normalizeTokens(runtimeAcceptOverride);
      const acceptAttr = normalized.filter(t => t.startsWith('.')).join(',');
      if (acceptAttr) input.setAttribute('accept', acceptAttr);
      dropzone.setAttribute('data-extensions', normalized.join(','));
      onChange(files.slice());
    }
  };

  // expõe p/ debug se necessário
  dropzone.__gvDropzoneApi = api;
  input.__gvDropzoneApi = api;

  return api;
}

// Compatibilidade: permitir import default ou nomeado
export default createFileDropzone;