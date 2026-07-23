// app/static/js/admin.js
(function () {
  'use strict';

  if (window.__vitalAdminReady) return;
  window.__vitalAdminReady = true;

  const $ = (selector) => document.querySelector(selector);

  // Estrutura e autenticação
  const adminPage = $('.admin-page');
  const accessForm = $('#admin-access-form');
  const tokenInput = $('#adm-token');
  const msg = $('#msg');
  const btn = $('#btn-reload');
  const rangeSel = $('#range');
  const autoSel = $('#autorf');
  const gateState = $('#admin-gate-state');
  const gateTitle = $('#admin-gate-title');
  const gateMessage = $('#admin-gate-message');
  const dashboard = $('#admin-dashboard');

  // Visão geral
  const cardsWrap = $('#summary-cards');
  const donutWrap = $('#status-donut');
  const legend = $('#status-legend');
  const narrative = $('#chart-narrative');
  const tooltip = $('#chart-tooltip');
  const chipSuccess = $('#chip-success');
  const chip4xx = $('#chip-4xx');
  const chip5xx = $('#chip-5xx');
  const chipTotal = $('#chip-total');
  const toolsGrid = $('#tools-grid');
  const bVer = $('#badge-version');
  const bEnv = $('#badge-env');
  const bBuild = $('#badge-build');
  const trendSection = $('#trend-section');
  const spark = $('#req-sparkline');
  const sparkLegend = $('#spark-legend');
  const errSection = $('#errors-section');
  const errTable = $('#errors-table tbody');

  // Feedbacks
  const feedbackWrap = $('#feedback-container');
  const feedbackMeta = $('#feedback-meta');
  const feedbackLimit = $('#feedback-limit');
  const feedbackReload = $('#feedback-refresh');

  // Logs
  const logsWrap = $('#logs-container');
  const logsMeta = $('#logs-meta');
  const logsLevel = $('#logs-level');
  const logsTail = $('#logs-tail');
  const logsReload = $('#logs-refresh');
  const logMsgInput = $('#logs-msg');
  const logLevelAdd = $('#logs-level-add');
  const logSendBtn = $('#logs-send');

  if (!adminPage || !accessForm || !tokenInput || !dashboard || !gateState) return;

  const LS_TOKEN = 'gv_admin_token';
  const LS_RANGE = 'gv_admin_range';
  const LS_AUTORF = 'gv_admin_autorf';
  const TOOL_DEFINITIONS = [
    ['compress', 'Compressão'],
    ['merge', 'Junção'],
    ['split', 'Divisão'],
    ['convert', 'Conversão'],
    ['edit', 'Edição'],
    ['organize', 'Organização'],
  ];

  let timer = null;
  let isLoadingDashboard = false;
  let isLoadingFeedback = false;
  let isLoadingLogs = false;

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const pct = (value, total) => total ? (value / total) * 100 : 0;
  const fmtPct = (value) => `${clamp(value, 0, 100).toFixed(0)}%`;
  const qs = (values) => {
    const params = new URLSearchParams();
    Object.entries(values).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        params.set(key, String(value));
      }
    });
    return params.toString();
  };

  function setMessage(element, text, type = '') {
    if (!element) return;
    element.textContent = text || '';
    element.classList.remove('is-success', 'is-error');
    if (type) element.classList.add(type === 'error' ? 'is-error' : 'is-success');
  }

  function getToken() {
    const typed = tokenInput.value.trim();
    if (typed) {
      sessionStorage.setItem('ADMIN_TOKEN', typed);
      localStorage.setItem(LS_TOKEN, typed);
      return typed;
    }
    return sessionStorage.getItem('ADMIN_TOKEN')
      || localStorage.getItem(LS_TOKEN)
      || '';
  }

  function getCSRFToken() {
    if (typeof window.getCSRFToken === 'function') {
      try {
        return window.getCSRFToken() || '';
      } catch (_) {
        return '';
      }
    }
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
  }

  function isAuthFailure(response) {
    return response.status === 401 || response.status === 403;
  }

  function clearAutoRefreshTimer() {
    if (!timer) return;
    clearInterval(timer);
    timer = null;
  }

  function startAutoRefreshIfNeeded() {
    if (timer || dashboard.hidden) return;
    const ms = Number.parseInt(autoSel?.value || '0', 10) || 0;
    if (ms > 0) {
      timer = window.setInterval(
        () => loadStats({ refreshSecondary: false }),
        ms
      );
    }
  }

  function resetApplicationMeta() {
    if (bVer) bVer.textContent = 'v—';
    if (bEnv) bEnv.textContent = 'ambiente—';
    if (bBuild) bBuild.textContent = 'build—';
  }

  function clearDashboardData() {
    cardsWrap?.replaceChildren();
    donutWrap?.replaceChildren();
    legend?.replaceChildren();
    toolsGrid?.replaceChildren();
    feedbackWrap?.replaceChildren();
    logsWrap?.replaceChildren();
    errTable?.replaceChildren();
    spark?.replaceChildren();

    if (errSection) errSection.hidden = true;
    if (trendSection) trendSection.hidden = true;
    if (sparkLegend) sparkLegend.textContent = '';
    if (narrative) narrative.textContent = 'Aguardando dados do período.';
    if (chipSuccess) chipSuccess.textContent = 'Sucesso';
    if (chip4xx) chip4xx.textContent = '4xx';
    if (chip5xx) chip5xx.textContent = '5xx';
    if (chipTotal) chipTotal.textContent = 'Total';
    if (tooltip) {
      tooltip.textContent = '';
      tooltip.classList.remove('is-visible');
    }
    setMessage(feedbackMeta, '');
    setMessage(logsMeta, '');
    resetApplicationMeta();
  }

  function showGate({
    title = 'Dashboard protegido',
    message = 'Informe o token administrativo para carregar o dashboard.',
    type = '',
    clear = false,
  } = {}) {
    gateTitle.textContent = title;
    gateMessage.textContent = message;
    gateState.classList.remove('is-loading', 'is-error');
    if (type === 'loading') gateState.classList.add('is-loading');
    if (type === 'error') gateState.classList.add('is-error');
    gateState.hidden = false;
    dashboard.hidden = true;
    dashboard.setAttribute('aria-busy', 'false');
    if (clear) clearDashboardData();
  }

  function showDashboard() {
    gateState.hidden = true;
    dashboard.hidden = false;
    dashboard.setAttribute('aria-busy', 'false');
  }

  function showLoadingState(hasVisibleDashboard) {
    if (hasVisibleDashboard) {
      dashboard.setAttribute('aria-busy', 'true');
      return;
    }
    showGate({
      title: 'Carregando dashboard',
      message: 'Validando o acesso e buscando os dados administrativos.',
      type: 'loading',
      clear: true,
    });
  }

  function handleAuthFailure() {
    clearAutoRefreshTimer();
    showGate({
      title: 'Acesso não autorizado',
      message: 'Token administrativo inválido. Verifique o valor informado e tente novamente.',
      type: 'error',
      clear: true,
    });
    setMessage(msg, 'Token administrativo inválido.', 'error');
  }

  function showRequestFailure(message) {
    showGate({
      title: 'Dashboard indisponível',
      message,
      type: 'error',
      clear: true,
    });
    setMessage(msg, message, 'error');
  }

  function appendEmptyState(container, message, isError = false) {
    if (!container) return;
    const empty = document.createElement('p');
    empty.className = `admin-empty${isError ? ' is-error' : ''}`;
    empty.textContent = message;
    container.appendChild(empty);
  }

  function animateNumber(element, target) {
    if (!element) return;
    const start = Number(element.textContent) || 0;
    const end = Number(target) || 0;
    const startedAt = performance.now();
    const duration = 420;

    const step = (time) => {
      const progress = Math.min(1, (time - startedAt) / duration);
      element.textContent = String(Math.round(start + (end - start) * progress));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function makeCard(label, value) {
    if (!cardsWrap) return;
    const card = document.createElement('article');
    card.className = 'stat-card';
    const number = document.createElement('p');
    number.className = 'kpi';
    number.textContent = '0';
    const caption = document.createElement('p');
    caption.className = 'label';
    caption.textContent = label;
    card.append(number, caption);
    cardsWrap.appendChild(card);
    animateNumber(number, value);
  }

  function drawDonut(segments, successRate) {
    if (!donutWrap) return;
    donutWrap.replaceChildren();

    const visibleSegments = segments.filter((segment) => (segment.value || 0) > 0);
    const total = visibleSegments.reduce((sum, segment) => sum + segment.value, 0) || 1;
    const radius = 84;
    const circumference = 2 * Math.PI * radius;
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 240 240');
    svg.classList.add('donut');

    const base = document.createElementNS(svg.namespaceURI, 'circle');
    base.setAttribute('cx', '120');
    base.setAttribute('cy', '120');
    base.setAttribute('r', String(radius));
    base.setAttribute('fill', 'transparent');
    base.setAttribute('class', 'donut-base');
    svg.appendChild(base);

    let offset = 0;
    visibleSegments.forEach((segment) => {
      const length = circumference * (segment.value / total);
      const circle = document.createElementNS(svg.namespaceURI, 'circle');
      circle.setAttribute('cx', '120');
      circle.setAttribute('cy', '120');
      circle.setAttribute('r', String(radius));
      circle.setAttribute('fill', 'transparent');
      circle.setAttribute(
        'stroke',
        getComputedStyle(adminPage).getPropertyValue(segment.cssVar).trim()
      );
      circle.setAttribute('stroke-width', '22');
      circle.setAttribute('stroke-dasharray', `${length} ${circumference - length}`);
      circle.setAttribute('stroke-dashoffset', String(circumference / 4 - offset));
      circle.setAttribute('stroke-linecap', 'round');
      circle.classList.add('seg');
      circle.addEventListener('mouseenter', () => {
        if (!tooltip) return;
        tooltip.textContent = `${segment.long}: ${segment.value} (${fmtPct((segment.value / total) * 100)})`;
        tooltip.classList.add('is-visible');
      });
      circle.addEventListener('mouseleave', () => tooltip?.classList.remove('is-visible'));
      svg.appendChild(circle);
      offset += length;
    });

    const title = document.createElementNS(svg.namespaceURI, 'text');
    title.setAttribute('x', '120');
    title.setAttribute('y', '112');
    title.setAttribute('text-anchor', 'middle');
    title.setAttribute('class', 'donut-center-title');
    title.textContent = 'Sucesso (2xx)';

    const value = document.createElementNS(svg.namespaceURI, 'text');
    value.setAttribute('x', '120');
    value.setAttribute('y', '144');
    value.setAttribute('text-anchor', 'middle');
    value.setAttribute('class', 'donut-center-value');
    value.textContent = fmtPct(successRate);

    svg.append(title, value);
    donutWrap.appendChild(svg);
  }

  function renderLegendItem({ label, value, percentage, tone }) {
    const item = document.createElement('li');
    item.className = 'legend-item';
    const dot = document.createElement('span');
    dot.className = `legend-dot legend-dot--${tone}`;
    dot.setAttribute('aria-hidden', 'true');
    const text = document.createElement('span');
    text.textContent = `${label} (${fmtPct(percentage)})`;
    const number = document.createElement('strong');
    number.className = 'legend-value';
    number.textContent = String(value);
    item.append(dot, text, number);
    return item;
  }

  function renderStatus(status) {
    const s2 = Number(status['2xx']) || 0;
    const s4 = Number(status['4xx']) || 0;
    const s5 = Number(status['5xx']) || 0;
    const total = s2 + s4 + s5;
    const rate = pct(s2, total);

    const segments = [
      { label: '2xx', long: 'Sucesso (2xx)', value: s2, cssVar: '--admin-success', tone: 'success' },
      { label: '4xx', long: 'Erros do cliente (4xx)', value: s4, cssVar: '--admin-warning', tone: 'warning' },
      { label: '5xx', long: 'Erros do servidor (5xx)', value: s5, cssVar: '--admin-danger', tone: 'error' },
    ];
    drawDonut(segments, rate);

    if (legend) {
      legend.replaceChildren(...segments.map((segment) => renderLegendItem({
        label: segment.long,
        value: segment.value,
        percentage: pct(segment.value, total),
        tone: segment.tone,
      })));
    }

    if (narrative) {
      narrative.textContent = total === 0
        ? 'Nenhuma requisição registrada no período selecionado.'
        : `De ${total} requisições consideradas, ${s2} foram sucesso, ${s4} retornaram erros do cliente e ${s5} erros do servidor.`;
    }

    if (chipSuccess) chipSuccess.textContent = `Sucesso ${fmtPct(rate)}`;
    if (chip4xx) chip4xx.textContent = `4xx ${fmtPct(pct(s4, total))}`;
    if (chip5xx) chip5xx.textContent = `5xx ${fmtPct(pct(s5, total))}`;
    if (chipTotal) chipTotal.textContent = `Total ${total}`;
    return { total, s2, s4, s5 };
  }

  function renderSparkline(series) {
    if (!trendSection || !spark) return;
    const points = Array.isArray(series) ? series : [];
    const hasActivity = points.some((point) => (Number(point?.count) || 0) > 0);
    if (!points.length || !hasActivity) {
      trendSection.hidden = true;
      spark.replaceChildren();
      return;
    }

    trendSection.hidden = false;
    spark.replaceChildren();
    const width = spark.clientWidth || 760;
    const height = 120;
    const padding = 10;
    const max = Math.max(...points.map((point) => Number(point.count) || 0), 1);
    const x = (index) => padding + (index * (width - 2 * padding)) / Math.max(1, points.length - 1);
    const y = (count) => height - padding - (count / max) * (height - 2 * padding);
    const pathData = points
      .map((point, index) => `${index ? 'L' : 'M'}${x(index)} ${y(Number(point.count) || 0)}`)
      .join(' ');

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    const area = document.createElementNS(svg.namespaceURI, 'path');
    area.setAttribute('class', 'sparkline-area');
    area.setAttribute(
      'd',
      `${pathData} L ${x(points.length - 1)} ${height - padding} L ${x(0)} ${height - padding} Z`
    );
    const path = document.createElementNS(svg.namespaceURI, 'path');
    path.setAttribute('class', 'sparkline-path');
    path.setAttribute('d', pathData);
    svg.append(area, path);
    spark.appendChild(svg);

    const first = points[0]?.ts || '—';
    const last = points[points.length - 1]?.ts || '—';
    if (sparkLegend) {
      sparkLegend.textContent = `${points.length} pontos · pico ${max} req/min · ${first} → ${last}`;
    }
  }

  function renderErrors(items) {
    if (!errSection || !errTable) return;
    if (!Array.isArray(items) || !items.length) {
      errSection.hidden = true;
      errTable.replaceChildren();
      return;
    }

    errSection.hidden = false;
    const rows = items.slice(0, 5).map((item) => {
      const row = document.createElement('tr');
      [
        item?.time || '—',
        item?.path || '—',
        item?.status || '—',
        String(item?.message || 'Sem detalhe').slice(0, 140),
      ].forEach((content) => {
        const cell = document.createElement('td');
        cell.textContent = String(content);
        row.appendChild(cell);
      });
      return row;
    });
    errTable.replaceChildren(...rows);
  }

  function renderTools(tools) {
    if (!toolsGrid) return;
    toolsGrid.replaceChildren();
    const values = tools || {};
    const activeTools = TOOL_DEFINITIONS.map(([key, label]) => {
      const ok = Number(values[`${key}_ok`]) || 0;
      const errors = Number(values[`${key}_err`]) || 0;
      return { key, label, ok, errors, total: ok + errors };
    }).filter((tool) => tool.total > 0);

    if (!activeTools.length) {
      appendEmptyState(toolsGrid, 'Nenhum uso registrado no período.');
      return;
    }

    activeTools.forEach((tool) => {
      const rate = Math.round((tool.ok / tool.total) * 100);
      const card = document.createElement('article');
      card.className = 'tool-card';

      const header = document.createElement('div');
      header.className = 'tool-card__header';
      const title = document.createElement('h3');
      title.className = 'tool-title';
      title.textContent = tool.label;
      const rateLabel = document.createElement('span');
      rateLabel.className = 'tool-rate';
      rateLabel.textContent = `${rate}% sucesso`;
      header.append(title, rateLabel);

      const counts = document.createElement('div');
      counts.className = 'tool-kpis';
      const success = document.createElement('span');
      success.className = 'tool-kpi tool-kpi--success';
      success.textContent = 'Sucessos ';
      const successValue = document.createElement('strong');
      successValue.textContent = String(tool.ok);
      success.appendChild(successValue);
      const failure = document.createElement('span');
      failure.className = 'tool-kpi tool-kpi--error';
      failure.textContent = 'Erros ';
      const failureValue = document.createElement('strong');
      failureValue.textContent = String(tool.errors);
      failure.appendChild(failureValue);
      counts.append(success, failure);

      const progress = document.createElement('progress');
      progress.className = 'tool-meter';
      progress.max = 100;
      progress.value = rate;
      progress.setAttribute('aria-label', `Taxa de sucesso de ${tool.label}`);
      progress.textContent = `${rate}%`;

      card.append(header, counts, progress);
      toolsGrid.appendChild(card);
    });
  }

  function feedbackValue(value, fallback = '—') {
    const text = String(value ?? '').trim();
    return text || fallback;
  }

  function feedbackTimestamp(value) {
    const raw = feedbackValue(value);
    const date = new Date(raw);
    return Number.isNaN(date.getTime()) ? raw : date.toLocaleString('pt-BR');
  }

  function makeFeedbackMeta(label, value) {
    const entry = document.createElement('span');
    entry.className = 'admin-feedback-item__meta-entry';
    const strong = document.createElement('strong');
    strong.textContent = `${label}: `;
    const text = document.createElement('span');
    text.textContent = feedbackValue(value);
    entry.append(strong, text);
    return entry;
  }

  function renderFeedbackItems(items) {
    if (!feedbackWrap) return;
    feedbackWrap.replaceChildren();
    if (!Array.isArray(items) || !items.length) {
      appendEmptyState(feedbackWrap, 'Nenhum feedback recebido.');
      return;
    }

    items.forEach((item) => {
      const article = document.createElement('article');
      article.className = 'admin-feedback-item';
      const header = document.createElement('div');
      header.className = 'admin-feedback-item__header';
      const type = document.createElement('strong');
      type.className = 'admin-feedback-item__type';
      type.textContent = feedbackValue(item?.type, 'feedback');
      const timestamp = document.createElement('time');
      timestamp.className = 'admin-feedback-item__timestamp';
      timestamp.textContent = feedbackTimestamp(item?.timestamp);
      header.append(type, timestamp);

      const message = document.createElement('p');
      message.className = 'admin-feedback-item__message';
      message.textContent = feedbackValue(item?.message);

      const meta = document.createElement('div');
      meta.className = 'admin-feedback-item__meta';
      meta.append(
        makeFeedbackMeta('Página', item?.page),
        makeFeedbackMeta('Versão', item?.app_version),
        makeFeedbackMeta('Request ID', item?.request_id)
      );
      article.append(header, message, meta);
      feedbackWrap.appendChild(article);
    });
  }

  async function loadFeedback(token = getToken()) {
    if (!feedbackWrap || !feedbackMeta || isLoadingFeedback) return;
    if (!token) {
      showGate({ clear: true });
      return;
    }

    isLoadingFeedback = true;
    feedbackWrap.setAttribute('aria-busy', 'true');
    setMessage(feedbackMeta, 'Carregando feedbacks…');
    try {
      const url = `/api/admin/feedback?${qs({ limit: feedbackLimit?.value || '50' })}`;
      const response = await fetch(url, {
        headers: { Accept: 'application/json', 'X-Admin-Token': token },
      });
      if (isAuthFailure(response)) {
        handleAuthFailure();
        return;
      }
      const data = await response.json().catch(() => null);
      if (!response.ok || !data || data.ok !== true || !Array.isArray(data.items)) {
        throw new Error(`HTTP ${response.status}`);
      }
      renderFeedbackItems(data.items);
      const count = Number(data.count) || 0;
      setMessage(
        feedbackMeta,
        `${count} feedback${count === 1 ? '' : 's'} recebido${count === 1 ? '' : 's'}.`,
        'success'
      );
    } catch (_) {
      feedbackWrap.replaceChildren();
      appendEmptyState(feedbackWrap, 'Não foi possível carregar os feedbacks.', true);
      setMessage(feedbackMeta, 'Falha ao consultar os feedbacks.', 'error');
    } finally {
      isLoadingFeedback = false;
      feedbackWrap.setAttribute('aria-busy', 'false');
    }
  }

  function createLogLine(item) {
    const levelValue = String(item?.level || '').toUpperCase();
    const line = document.createElement('div');
    line.className = `log-line level-${levelValue}`;
    const parts = [
      ['ts', item?.ts || '—'],
      ['level', levelValue || '—'],
      ['where', item?.where || ''],
      ['req', item?.req || ''],
      ['msg', item?.msg || item?.raw || ''],
    ];
    parts.forEach(([className, value]) => {
      const span = document.createElement('span');
      span.className = className;
      span.textContent = String(value);
      line.appendChild(span);
    });
    return line;
  }

  async function loadLogs(token = getToken()) {
    if (!logsWrap || !logsMeta || isLoadingLogs) return;
    if (!token) {
      showGate({ clear: true });
      return;
    }

    isLoadingLogs = true;
    logsWrap.setAttribute('aria-busy', 'true');
    setMessage(logsMeta, 'Carregando logs…');
    try {
      const url = `/api/admin/logs?${qs({
        tail: logsTail?.value || '400',
        level: logsLevel?.value || '',
      })}`;
      const response = await fetch(url, {
        headers: { Accept: 'application/json', 'X-Admin-Token': token },
      });
      if (isAuthFailure(response)) {
        handleAuthFailure();
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      logsWrap.replaceChildren();
      const items = Array.isArray(data.items) ? data.items : [];
      if (items.length) {
        logsWrap.append(...items.map(createLogLine));
      } else {
        appendEmptyState(logsWrap, 'Nenhuma linha de log encontrada.');
      }
      setMessage(
        logsMeta,
        `${Number(data.count) || 0} linha${Number(data.count) === 1 ? '' : 's'} carregada${Number(data.count) === 1 ? '' : 's'}.`,
        'success'
      );
      logsWrap.scrollTop = logsWrap.scrollHeight;
    } catch (_) {
      logsWrap.replaceChildren();
      appendEmptyState(logsWrap, 'Não foi possível carregar os logs.', true);
      setMessage(logsMeta, 'Falha ao consultar os logs.', 'error');
    } finally {
      isLoadingLogs = false;
      logsWrap.setAttribute('aria-busy', 'false');
    }
  }

  async function sendLog() {
    if (!logMsgInput || !logSendBtn) return;
    const token = getToken();
    if (!token) {
      showGate({ clear: true });
      setMessage(msg, 'Informe o token administrativo.', 'error');
      tokenInput.focus();
      return;
    }
    const message = logMsgInput.value.trim();
    if (!message) {
      setMessage(logsMeta, 'Escreva uma linha de log antes de enviar.', 'error');
      logMsgInput.focus();
      return;
    }

    logSendBtn.disabled = true;
    setMessage(logsMeta, 'Registrando linha de log…');
    try {
      const response = await fetch('/api/admin/log', {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Admin-Token': token,
          'X-CSRFToken': getCSRFToken(),
        },
        body: JSON.stringify({
          level: (logLevelAdd?.value || 'INFO').toUpperCase(),
          message,
        }),
      });
      if (isAuthFailure(response)) {
        handleAuthFailure();
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      logMsgInput.value = '';
      setMessage(logsMeta, 'Linha registrada. Atualizando logs…', 'success');
      await loadLogs(token);
    } catch (_) {
      setMessage(logsMeta, 'Não foi possível registrar a linha de log.', 'error');
    } finally {
      logSendBtn.disabled = false;
    }
  }

  async function loadStats({ refreshSecondary = false } = {}) {
    if (isLoadingDashboard) return;
    const token = getToken();
    if (!token) {
      clearAutoRefreshTimer();
      showGate({ clear: true });
      setMessage(msg, 'Informe o token administrativo para carregar o dashboard.');
      return;
    }

    isLoadingDashboard = true;
    const hadVisibleDashboard = !dashboard.hidden;
    const selectedRange = rangeSel?.value || '15m';
    showLoadingState(hadVisibleDashboard);
    if (rangeSel) localStorage.setItem(LS_RANGE, selectedRange);
    if (btn) btn.disabled = true;
    setMessage(msg, 'Carregando dashboard…');

    try {
      const response = await fetch(`/api/admin/stats?${qs({ range: selectedRange })}`, {
        headers: { Accept: 'application/json', 'X-Admin-Token': token },
      });
      if (isAuthFailure(response)) {
        handleAuthFailure();
        return;
      }
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        if (response.status === 400) {
          const errorMessage = typeof data?.error === 'string'
            ? data.error.slice(0, 160)
            : 'O período selecionado não é aceito pela API.';
          if (!hadVisibleDashboard) {
            showGate({
              title: 'Período inválido',
              message: errorMessage,
              type: 'error',
            });
          }
          setMessage(msg, errorMessage, 'error');
          return;
        }
        throw new Error(`HTTP ${response.status}`);
      }
      if (!data || data.range !== selectedRange) {
        const rangeMessage = 'A resposta da API não corresponde ao período selecionado.';
        if (!hadVisibleDashboard) {
          showGate({
            title: 'Período inconsistente',
            message: rangeMessage,
            type: 'error',
          });
        }
        setMessage(msg, rangeMessage, 'error');
        return;
      }

      if (bVer) bVer.textContent = `v${data.app?.version || '—'}`;
      if (bEnv) bEnv.textContent = data.app?.env || 'ambiente—';
      if (bBuild) bBuild.textContent = data.app?.build || 'build—';

      const { total, s2, s4, s5 } = renderStatus(data.status || {});
      if (cardsWrap) {
        cardsWrap.replaceChildren();
        makeCard('Requisições', data.requests_total ?? total);
        makeCard('Sucesso (2xx)', s2);
        makeCard('Erros do cliente (4xx)', s4);
        makeCard('Erros do servidor (5xx)', s5);
      }
      renderTools(data.tools || {});
      renderSparkline(data.timeseries?.requests_per_min || data.timeseries || []);
      renderErrors(data.recent_errors || []);
      showDashboard();
      setMessage(msg, 'Dashboard atualizado.', 'success');
      startAutoRefreshIfNeeded();

      if (refreshSecondary) {
        await Promise.all([loadLogs(token), loadFeedback(token)]);
      }
    } catch (_) {
      showRequestFailure('Não foi possível carregar o dashboard agora. Tente novamente.');
    } finally {
      isLoadingDashboard = false;
      dashboard.setAttribute('aria-busy', 'false');
      if (btn) btn.disabled = false;
    }
  }

  function applyAutoRefresh() {
    clearAutoRefreshTimer();
    const ms = Number.parseInt(autoSel?.value || '0', 10) || 0;
    localStorage.setItem(LS_AUTORF, String(ms));
    if (ms > 0 && !dashboard.hidden) {
      timer = window.setInterval(
        () => loadStats({ refreshSecondary: false }),
        ms
      );
    }
  }

  accessForm.addEventListener('submit', (event) => {
    event.preventDefault();
    loadStats({ refreshSecondary: true });
  });
  rangeSel?.addEventListener(
    'change',
    () => loadStats({ refreshSecondary: false })
  );
  autoSel?.addEventListener('change', applyAutoRefresh);
  logsReload?.addEventListener('click', () => loadLogs());
  logsLevel?.addEventListener('change', () => loadLogs());
  logsTail?.addEventListener('change', () => loadLogs());
  feedbackReload?.addEventListener('click', () => loadFeedback());
  feedbackLimit?.addEventListener('change', () => loadFeedback());
  logSendBtn?.addEventListener('click', sendLog);
  logMsgInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) sendLog();
  });

  const savedToken = localStorage.getItem(LS_TOKEN);
  const savedRange = localStorage.getItem(LS_RANGE);
  const savedAuto = localStorage.getItem(LS_AUTORF);
  if (savedToken) {
    tokenInput.value = savedToken;
    sessionStorage.setItem('ADMIN_TOKEN', savedToken);
  }
  if (savedRange && rangeSel) rangeSel.value = savedRange;
  if (savedAuto && autoSel) autoSel.value = savedAuto;

  if (savedToken) {
    loadStats({ refreshSecondary: true });
  } else {
    showGate({ clear: true });
  }
})();
