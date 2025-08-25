// app/static/js/ses-guard.js
// Silencia apenas ruídos do lockdown/SES (throw null e logs específicos),
// sem mascarar erros reais da aplicação.

(function () {
  // 1) Filtro de mensagens no console
  const orig = {
    error: console.error.bind(console),
    warn:  console.warn.bind(console),
    log:   console.log.bind(console),
  };

  function isSesNoise(args) {
    try {
      const [first] = args || [];
      const s = String(first || '');
      // Mensagens típicas do lockdown/SES
      if (s.startsWith('SES_UNCAUGHT_EXCEPTION')) return true;
      if (s.includes('Removing unpermitted intrinsics')) return true;
      return false;
    } catch (_) {
      return false;
    }
  }

  console.error = function (...args) {
    if (isSesNoise(args)) return;
    return orig.error(...args);
  };
  console.warn = function (...args) {
    if (isSesNoise(args)) return;
    return orig.warn(...args);
  };
  console.log = function (...args) {
    if (isSesNoise(args)) return;
    return orig.log(...args);
  };

  // 2) Guards para exceções/rejeições com valor null/undefined
  window.addEventListener(
    'error',
    (e) => {
      if (e && (e.error === null || e.error === undefined)) e.preventDefault();
    },
    true
  );

  window.addEventListener(
    'unhandledrejection',
    (e) => {
      if (e && (e.reason === null || e.reason === undefined)) e.preventDefault();
    },
    true
  );
})();