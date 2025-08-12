// app/static/js/compress.js
document.addEventListener('DOMContentLoaded', () => {
  const sel  = document.getElementById('profile');
  const hint = document.getElementById('profile-hint');
  if (!sel || !hint) return;

  // define o texto inicial a partir da option selecionada
  const applyHint = () => {
    const opt = sel.selectedOptions && sel.selectedOptions[0];
    hint.textContent = opt?.dataset?.hint || '';
  };

  applyHint();
  sel.addEventListener('change', applyHint);
});