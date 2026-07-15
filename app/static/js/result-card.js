(function () {
  'use strict';

  const DEFAULT_TITLE = 'Arquivo pronto';
  const DEFAULT_DOWNLOAD_LABEL = 'Baixar arquivo';
  const DEFAULT_VIEW_LABEL = 'Visualizar';
  const DEFAULT_NEXT_LABEL = 'Fazer outra ação';

  function resolveContainer(target) {
    if (!target) return null;
    if (typeof target === 'string') return document.querySelector(target);
    return target;
  }

  function formatSize(value) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return 'Não informado';

    const units = ['B', 'KB', 'MB', 'GB'];
    let size = value;
    let unitIndex = 0;

    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }

    const precision = unitIndex === 0 ? 0 : size >= 10 ? 1 : 2;
    return `${size.toFixed(precision)} ${units[unitIndex]}`;
  }

  function normalizeUrl(rawUrl) {
    return typeof rawUrl === 'string' ? rawUrl.trim() : '';
  }

  function toDownloadUrl(rawUrl) {
    const url = normalizeUrl(rawUrl);
    if (!url) return '';
    if (url.startsWith('#') || url.startsWith('blob:')) return url;

    try {
      const parsed = new URL(url, window.location.origin);
      if (parsed.origin === window.location.origin) {
        parsed.searchParams.set('download', '1');
        return `${parsed.pathname}${parsed.search}${parsed.hash}`;
      }
    } catch (_) {
      return url;
    }

    return url;
  }

  function toViewUrl(rawUrl) {
    const url = normalizeUrl(rawUrl);
    if (!url || url.startsWith('#') || url.startsWith('blob:')) return url;

    try {
      const parsed = new URL(url, window.location.origin);
      if (parsed.origin === window.location.origin) {
        parsed.searchParams.delete('download');
        return `${parsed.pathname}${parsed.search}${parsed.hash}`;
      }
    } catch (_) {
      return url;
    }

    return url;
  }

  function filenameFromUrl(rawUrl) {
    const url = normalizeUrl(rawUrl);
    if (!url || url.startsWith('blob:')) return '';

    try {
      const parsed = new URL(url, window.location.origin);
      const segment = parsed.pathname.split('/').filter(Boolean).pop() || '';
      return decodeURIComponent(segment);
    } catch (_) {
      return '';
    }
  }

  function resultName(result) {
    return result.name || result.filename || result.downloadName || filenameFromUrl(result.downloadUrl || result.download_url || result.url) || 'arquivo';
  }

  function isPdfResult(result, name, viewUrl) {
    const mime = String(result.mimeType || result.mime_type || result.type || '').toLowerCase();
    const candidate = `${name || ''} ${viewUrl || ''}`.toLowerCase();
    return mime.includes('application/pdf') || candidate.includes('.pdf') || candidate.startsWith('blob:');
  }

  function appendText(parent, tagName, className, text) {
    const el = document.createElement(tagName);
    el.className = className;
    el.textContent = text;
    parent.appendChild(el);
    return el;
  }

  function appendAction(parent, result, action) {
    if (!action) return null;

    const label = action.label || DEFAULT_NEXT_LABEL;
    let el;

    if (action.href) {
      el = document.createElement('a');
      el.href = action.href;
      if (action.download) el.download = action.download;
      if (action.target) {
        el.target = action.target;
        el.rel = action.rel || 'noopener noreferrer';
      }
    } else {
      el = document.createElement('button');
      el.type = 'button';
    }

    el.className = action.className || 'btn btn-secondary';
    el.textContent = label;

    if (typeof action.onClick === 'function') {
      el.addEventListener('click', (event) => action.onClick(event, result));
    }

    parent.appendChild(el);
    return el;
  }

  function create(resultInput, optionsInput) {
    const result = resultInput || {};
    const options = optionsInput || {};
    const name = resultName(result);
    const rawUrl = normalizeUrl(result.downloadUrl || result.download_url || result.url || result.href);
    const downloadUrl = normalizeUrl(result.forceDownloadUrl || result.download_url_forced) || toDownloadUrl(rawUrl);
    const viewUrl = normalizeUrl(result.viewUrl || result.previewUrl) || toViewUrl(rawUrl);
    const canView = options.showView !== false && viewUrl && isPdfResult(result, name, viewUrl);
    const status = result.status || result.message || result.warning || result.notice || '';
    const statusType = result.statusType || result.kind || 'info';

    const card = document.createElement('article');
    card.className = 'result-card';
    card.tabIndex = -1;

    const header = document.createElement('div');
    header.className = 'result-card__header';

    const marker = document.createElement('span');
    marker.className = 'result-card__marker';
    marker.setAttribute('aria-hidden', 'true');
    marker.textContent = '';

    const headingWrap = document.createElement('div');
    headingWrap.className = 'result-card__heading';

    appendText(headingWrap, 'h3', 'result-card__title', result.title || options.title || DEFAULT_TITLE);
    appendText(headingWrap, 'p', 'result-card__subtitle', result.subtitle || 'Seu arquivo foi gerado com sucesso.');

    header.appendChild(marker);
    header.appendChild(headingWrap);
    card.appendChild(header);

    const details = document.createElement('dl');
    details.className = 'result-card__details';

    appendText(details, 'dt', 'result-card__label', 'Nome');
    appendText(details, 'dd', 'result-card__value result-card__value--name', name);
    appendText(details, 'dt', 'result-card__label', 'Tamanho');
    appendText(details, 'dd', 'result-card__value', formatSize(result.size));

    card.appendChild(details);

    if (status) {
      const statusEl = appendText(card, 'p', `result-card__status result-card__status--${statusType}`, status);
      statusEl.setAttribute('role', statusType === 'error' ? 'alert' : 'status');
    }

    const actions = document.createElement('div');
    actions.className = 'result-card__actions';

    if (downloadUrl) {
      appendAction(actions, result, {
        href: downloadUrl,
        download: result.downloadName || name,
        label: result.downloadLabel || options.downloadLabel || DEFAULT_DOWNLOAD_LABEL,
        className: 'btn btn-primary result-card__action result-card__action--download',
      });
    }

    if (canView) {
      appendAction(actions, result, {
        href: viewUrl,
        label: result.viewLabel || options.viewLabel || DEFAULT_VIEW_LABEL,
        className: 'btn btn-secondary result-card__action result-card__action--view',
        target: '_blank',
      });
    }

    const nextAction = result.nextAction || options.nextAction || null;
    const nextHref = result.nextActionHref || options.nextActionHref || '';
    const onNext = result.onNextAction || options.onNextAction;

    if (nextAction || nextHref || typeof onNext === 'function') {
      appendAction(actions, result, {
        href: nextHref,
        label: result.nextActionLabel || options.nextActionLabel || DEFAULT_NEXT_LABEL,
        className: 'btn btn--soft result-card__action result-card__action--next',
        onClick: typeof onNext === 'function' ? onNext : null,
      });
    }

    if (!actions.children.length) {
      appendText(actions, 'span', 'result-card__missing-action', 'Link de download não retornado.');
    }

    card.appendChild(actions);
    return card;
  }

  function prepareContainer(container) {
    container.innerHTML = '';
    container.hidden = false;
    container.classList.remove('is-hidden', 'hidden');
    container.classList.add('result-ready');
    container.setAttribute('aria-live', 'polite');
    container.setAttribute('aria-atomic', 'true');
  }

  function appendCard(container, card) {
    const tagName = container.tagName.toLowerCase();

    if (tagName === 'ul' || tagName === 'ol') {
      const item = document.createElement('li');
      item.className = 'result-list__item';
      item.appendChild(card);
      container.appendChild(item);
      return item;
    }

    container.appendChild(card);
    return card;
  }

  function focusFirstCard(container, shouldFocus) {
    if (shouldFocus === false) return;

    const card = container.querySelector('.result-card');
    if (!card) return;

    window.requestAnimationFrame(() => {
      try {
        card.focus({ preventScroll: false });
      } catch (_) {
        card.focus();
      }
    });
  }

  function renderMany(target, resultsInput, optionsInput) {
    const container = resolveContainer(target);
    if (!container) return [];

    const results = Array.isArray(resultsInput) ? resultsInput : [resultsInput].filter(Boolean);
    const options = optionsInput || {};

    prepareContainer(container);

    if (!results.length) {
      appendText(container, 'p', 'result-card__empty', 'Nada para baixar.');
      return [];
    }

    const cards = results.map((result) => create(result, options));
    cards.forEach((card) => appendCard(container, card));
    focusFirstCard(container, options.focus);
    return cards;
  }

  function render(target, result, options) {
    return renderMany(target, [result], options)[0] || null;
  }

  window.VitalResultCard = {
    create,
    render,
    renderMany,
    formatSize,
    toDownloadUrl,
    toViewUrl,
  };
})();
