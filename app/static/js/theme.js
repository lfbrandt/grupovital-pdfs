(function () {
  const root = document.documentElement;        // <html>
  const btn  = document.getElementById('theme-toggle');

  // 1) Aplica tema salvo (se existir)
  const saved = localStorage.getItem('gv-theme');
  if (saved === 'dark' || saved === 'light') {
    root.setAttribute('data-theme', saved);
    document.cookie = `theme=${saved}; Path=/; Max-Age=31536000; SameSite=Lax`;
    if (btn) btn.setAttribute('aria-pressed', saved === 'dark' ? 'true' : 'false');
  }

  // 2) Click -> alterna tema
  if (!btn) return;

  btn.addEventListener('click', () => {
    const current = root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    const next = current === 'dark' ? 'light' : 'dark';

    root.setAttribute('data-theme', next);
    localStorage.setItem('gv-theme', next);
    document.cookie = `theme=${next}; Path=/; Max-Age=31536000; SameSite=Lax`;
    btn.setAttribute('aria-pressed', next === 'dark' ? 'true' : 'false');
  });
})();