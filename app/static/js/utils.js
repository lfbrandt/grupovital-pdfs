export function getCSRFToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

export function mostrarMensagem(msg, tipo = 'sucesso') {
  const el = document.getElementById('mensagem-feedback');
  if (!el) return;
  el.textContent = msg;
  el.classList.remove('sucesso', 'erro', 'hidden');
  el.classList.add(tipo);
  setTimeout(() => el.classList.add('hidden'), 5000);
}

export function mostrarLoading(show = true) {
  const el = document.getElementById('loading-spinner');
  if (!el) return;
  el.classList[show ? 'remove' : 'add']('hidden');
}

export function atualizarProgresso(percent) {
  const container = document.getElementById('progress-container');
  const bar = document.getElementById('progress-bar');
  if (!container || !bar) return;
  container.classList.remove('hidden');
  bar.style.width = percent + '%';
}

export function resetarProgresso() {
  const container = document.getElementById('progress-container');
  const bar = document.getElementById('progress-bar');
  if (!container || !bar) return;
  bar.style.width = '0%';
  container.classList.add('hidden');
}
