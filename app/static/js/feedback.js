(function () {
  'use strict';

  if (window.__vitalFeedbackReady) return;
  window.__vitalFeedbackReady = true;

  const panel = document.getElementById('feedback-panel');
  const form = document.getElementById('feedback-form');
  const pageInput = document.getElementById('feedback-page');
  const pageDisplay = document.getElementById('feedback-page-display');
  const typeInput = document.getElementById('feedback-type');
  const messageInput = document.getElementById('feedback-message');
  const statusEl = document.getElementById('feedback-status');
  const submitButton = document.getElementById('feedback-submit');

  if (!panel || !form || !pageInput || !pageDisplay || !typeInput || !messageInput || !statusEl || !submitButton) {
    return;
  }

  let previousFocus = null;
  let isSubmitting = false;

  function getCSRFToken() {
    if (typeof window.getCSRFToken === 'function') {
      try {
        return window.getCSRFToken() || '';
      } catch (_) {
        return '';
      }
    }
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') || '' : '';
  }

  function normalizePage(value) {
    const raw = String(value || '').replace(/[\u0000-\u001f\u007f]/g, ' ');
    return raw.replace(/\s+/g, ' ').trim().slice(0, 80) || 'desconhecida';
  }

  function getCurrentPage() {
    const pageId = document.body?.dataset?.pageId;
    return normalizePage(pageId || window.location.pathname || 'desconhecida');
  }

  function setStatus(message, type) {
    statusEl.textContent = message || '';
    statusEl.classList.remove('is-success', 'is-error');
    if (type) statusEl.classList.add(type === 'success' ? 'is-success' : 'is-error');
  }

  function syncPageFields() {
    const page = getCurrentPage();
    pageInput.value = page;
    pageDisplay.value = page;
  }

  function openPanel() {
    previousFocus = document.activeElement;
    syncPageFields();
    setStatus('', null);
    panel.hidden = false;
    panel.setAttribute('aria-hidden', 'false');
    window.requestAnimationFrame(() => {
      messageInput.focus({ preventScroll: true });
    });
  }

  function closePanel() {
    panel.hidden = true;
    panel.setAttribute('aria-hidden', 'true');
    setStatus('', null);
    if (previousFocus && typeof previousFocus.focus === 'function') {
      previousFocus.focus({ preventScroll: true });
    }
  }

  function readPayload() {
    const message = messageInput.value.replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim();
    if (message.length < 5) {
      throw new Error('Escreva pelo menos 5 caracteres.');
    }
    if (message.length > 2000) {
      throw new Error('A mensagem deve ter no máximo 2000 caracteres.');
    }

    return {
      page: getCurrentPage(),
      type: typeInput.value,
      message,
    };
  }

  async function parseJSON(response) {
    try {
      return await response.json();
    } catch (_) {
      return null;
    }
  }

  function parseError(response, data) {
    if (data && data.error === 'CSRF') {
      return 'Sua sessão expirou ou o token de segurança é inválido. Recarregue a página e tente novamente.';
    }
    if (data && typeof data.error === 'string' && data.error.trim()) return data.error;
    if (data && typeof data.message === 'string' && data.message.trim()) return data.message;
    return response.status === 429
      ? 'Muitas tentativas. Tente novamente em instantes.'
      : 'Não foi possível enviar o feedback agora.';
  }

  async function submitFeedback(event) {
    event.preventDefault();
    if (isSubmitting) return;

    let payload;
    try {
      payload = readPayload();
    } catch (error) {
      setStatus(error.message || 'Revise a mensagem antes de enviar.', 'error');
      messageInput.focus({ preventScroll: true });
      return;
    }

    isSubmitting = true;
    submitButton.disabled = true;
    submitButton.textContent = 'Enviando...';
    form.setAttribute('aria-busy', 'true');
    setStatus('Enviando feedback...', null);

    try {
      const response = await fetch('/api/feedback', {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'Accept': 'application/json',
          'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify(payload),
      });
      const data = await parseJSON(response);

      if (!response.ok) {
        throw new Error(parseError(response, data));
      }
      if (!data || data.ok !== true) {
        throw new Error('O servidor não confirmou o recebimento do feedback.');
      }

      messageInput.value = '';
      typeInput.value = 'problema';
      syncPageFields();
      setStatus('Feedback enviado. Obrigado!', 'success');
    } catch (error) {
      setStatus(error.message || 'Não foi possível enviar o feedback agora.', 'error');
    } finally {
      isSubmitting = false;
      submitButton.disabled = false;
      submitButton.textContent = 'Enviar';
      form.removeAttribute('aria-busy');
    }
  }

  document.addEventListener('click', (event) => {
    const openButton = event.target.closest('[data-feedback-open]');
    if (openButton) {
      event.preventDefault();
      openPanel();
      return;
    }

    const closeButton = event.target.closest('[data-feedback-close]');
    if (closeButton && !panel.hidden) {
      event.preventDefault();
      closePanel();
    }
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !panel.hidden) {
      closePanel();
    }
  });

  form.addEventListener('submit', submitFeedback);
  panel.setAttribute('aria-hidden', 'true');
  syncPageFields();
})();
