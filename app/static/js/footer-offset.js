// Ajusta --footer-h (altura real do footer fixo) e --footer-fab-reserve (respiro no canto direito).
// SAFE_DEFAULT pode ser passado no <body data-fab-safe="240">.
// Carregar com <script src=".../footer-offset.js" defer></script>.

(function () {
  const root  = document.documentElement;
  const body  = document.body;

  const SAFE_DEFAULT =
    Number(body.dataset.fabSafe || body.getAttribute('data-fab-safe')) || 240; // px

  // Seletores compatíveis com seus templates
  const pickFooter = () =>
    document.querySelector('#site-footer') ||
    document.querySelector('.site-footer') ||
    document.querySelector('footer');

  let footer = pickFooter();
  let inner  = footer ? (footer.querySelector('.footer-inner') || footer) : null;

  function isFixedFooter(el) {
    if (!el || el.hidden) return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const pos = cs.position;
    if (pos === 'fixed') return true;
    if (pos === 'sticky') {
      const bottom = parseInt(cs.bottom || '0', 10);
      return bottom === 0;
    }
    // fallback por classe (mantém compatibilidade com seu padrão)
    if (!el.classList.contains('footer--static') && !el.classList.contains('footer--corner')) {
      // se o footer tem uma sombra/fundo e está colado no fim da viewport, considere fixo
      const rect = el.getBoundingClientRect();
      return Math.abs((window.innerHeight || 0) - rect.bottom) <= 1;
    }
    return false;
  }

  let raf = 0;
  const apply = () => {
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      footer = pickFooter();
      inner  = footer ? (footer.querySelector('.footer-inner') || footer) : null;

      // Altura real do footer fixo → --footer-h
      const h = (footer && isFixedFooter(footer))
        ? Math.ceil(footer.getBoundingClientRect().height)
        : 0;
      root.style.setProperty('--footer-h', h + 'px');

      // Reserva lateral para FAB/right widgets → --footer-fab-reserve
      if (inner) {
        const vw   = Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0);
        const rect = inner.getBoundingClientRect();
        const rightGutter = Math.max(vw - rect.right, 0);
        const needed = Math.max(SAFE_DEFAULT - rightGutter, 0);
        const reserve = Math.max(24, Math.min(260, rightGutter + needed));
        root.style.setProperty('--footer-fab-reserve', reserve + 'px');
      } else {
        root.style.setProperty('--footer-fab-reserve', '24px');
      }
    });
  };

  // Observadores
  try {
    const ro = new ResizeObserver(apply);
    footer && ro.observe(footer);
    inner  && ro.observe(inner);
  } catch (_) { /* ok em navegadores antigos */ }

  // Se o footer trocar de classe/DOM dinamicamente
  try {
    const mo = new MutationObserver(apply);
    footer && mo.observe(footer, { attributes: true, attributeFilter: ['class', 'style'] });
  } catch (_) { /* noop */ }

  // Eventos úteis
  window.addEventListener('resize', apply, { passive: true });
  window.addEventListener('orientationchange', apply, { passive: true });
  window.addEventListener('pageshow', apply); // volta do bfcache
  document.addEventListener('DOMContentLoaded', apply);

  if (document.fonts && document.fonts.addEventListener) {
    document.fonts.addEventListener('loadingdone', apply);
  }

  apply();
})();