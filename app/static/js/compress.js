/* ========================================================================
   compress.js — interface de tela única com blocos de estado
   Compatível com utils.js UMD (window.getCSRFToken exposto globalmente).
   NÃO usa ES Module import — carregado como <script defer> clássico.
   ======================================================================== */
'use strict';

function readCSRFToken() {
  if (typeof window.getCSRFToken === 'function') return window.getCSRFToken();
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

console.debug('[compress] módulo carregado');

const __GV_COMPRESS = (window.__GV_COMPRESS = window.__GV_COMPRESS || {});
const _AState = {
  analyseId: null,
  pages: [],
  filter: 'all',
  inflight: false,
  mode: 'target_size',
  targetSizeMb: 5,
  allowGrayscale: false,
  uploadedSizeBytes: null,
};

/* ── Blocos de estado ───────────────────────────────────────────────────── */
function _setBlockState(id, state) {
  const el = document.getElementById(id); if (!el) return;
  el.classList.remove('cz-block--empty', 'cz-block--loading', 'cz-block--ready');
  el.classList.add(`cz-block--${state}`);
}

function _resetAllBlocks() {
  _setBlockState('cz-summary',         'empty');
  _setBlockState('cz-controls',        'empty');
  _setBlockState('page-analysis-grid', 'empty');
  ['global-quality', 'global-dpi', 'filter-all', 'filter-large'].forEach(id => {
    const el = document.getElementById(id); if (el) el.disabled = true;
  });
  ['filter-all-count', 'filter-large-count', 'selected-count', 'total-count'].forEach(id => {
    const el = document.getElementById(id); if (el) el.textContent = '—';
  });
  ['cz-filename', 'cz-pages', 'cs-original-val', 'cs-baseline-val', 'cs-adjusted-val'].forEach(id => {
    const el = document.getElementById(id); if (el) el.textContent = '—';
  });
  const adjLabelEl = document.getElementById('cs-adjusted-label');
  if (adjLabelEl) adjLabelEl.textContent = 'Estimativa inicial';
  const estimateNote = document.getElementById('cs-estimate-note');
  if (estimateNote) {
    estimateNote.textContent = 'Valor aproximado; o resultado real pode variar.';
  }
  const badge = document.getElementById('cs-badge');
  if (badge) { badge.textContent = ''; badge.className = 'cs-badge'; }
  const grid = document.getElementById('page-analysis-grid');
  if (grid) {
    grid.innerHTML = `
      <div class="cz-skeleton-grid" aria-hidden="true">
        <div class="cz-skeleton-card"></div>
        <div class="cz-skeleton-card"></div>
        <div class="cz-skeleton-card"></div>
      </div>`;
  }
}

function _clearResultCard() {
  const container = document.getElementById('compress-result');
  if (container) {
    container.innerHTML = '';
    container.hidden = true;
    container.classList.remove('result-ready');
  }

  if (__GV_COMPRESS._resultUrl) {
    try { URL.revokeObjectURL(__GV_COMPRESS._resultUrl); } catch (_) {}
    __GV_COMPRESS._resultUrl = '';
  }
}

function _resetCompressFlow() {
  _AState.analyseId = null;
  _AState.pages = [];
  _AState.filter = 'all';
  _AState.mode = 'target_size';
  _AState.targetSizeMb = 5;
  _AState.allowGrayscale = false;
  _AState.uploadedSizeBytes = null;
  __GV_COMPRESS.inputBound = false;

  const input = document.getElementById('input-compress');
  if (input) input.value = '';
  const targetInput = document.getElementById('target-size-mb');
  if (targetInput) targetInput.value = '5';
  const grayscaleInput = document.getElementById('allow-grayscale');
  if (grayscaleInput) grayscaleInput.checked = false;

  clearTimeout(__GV_COMPRESS._resetTimer);
  _clearResultCard();
  _resetAllBlocks();
  _syncCompressionModeUI();
  _clearFeedback();
  _resetProgress();
  _setSteps([]);
  bindUploadOnce();
}

function _showResultCard({
  blob,
  resultUrl,
  filename,
  sizeBytes,
  feedbackMsg,
  feedbackType,
  presentation,
}) {
  const container = document.getElementById('compress-result');
  const renderer = window.VitalResultCard;

  if (!container || !renderer?.render) return;

  if (__GV_COMPRESS._resultUrl && __GV_COMPRESS._resultUrl !== resultUrl) {
    try { URL.revokeObjectURL(__GV_COMPRESS._resultUrl); } catch (_) {}
  }

  const url = resultUrl || URL.createObjectURL(blob);
  __GV_COMPRESS._resultUrl = url;

  const result = {
    name: filename,
    size: presentation?.sizeText || sizeBytes,
    downloadUrl: url,
    viewUrl: url,
    mimeType: 'application/pdf',
    title: presentation?.title,
    subtitle: presentation?.subtitle,
    variant: presentation?.variant,
    detailRows: presentation?.detailRows,
    messages: presentation?.messages,
    downloadLabel: presentation?.downloadLabel,
    status: presentation ? '' : (feedbackMsg || ''),
    statusType: presentation ? 'info' : feedbackType,
    nextAction: true,
    nextActionLabel: 'Fazer outra ação',
    onNextAction: _resetCompressFlow,
  };

  renderer.render(container, result, { focus: true });
}

window.addEventListener('beforeunload', _clearResultCard);

/* ── Barra de progresso do topo ─────────────────────────────────────────────
   Controla #cz-top-progress independentemente do progress-container inferior.
   Estados: normal (0-100%), indeterminado, erro, done (desaparece suave).   */
function _topShow(label) {
  const el = document.getElementById('cz-top-progress'); if (!el) return;
  el.hidden = false;
  el.classList.remove('is-indeterminate', 'is-error', 'is-done');
  _topLabel(label || '');
}
function _topLabel(text) {
  const el = document.getElementById('cz-top-label'); if (!el) return;
  el.textContent = text || '';
}
function _setProgressElementValue(element, pct) {
  if (!element) return;
  const value = Math.min(100, Math.max(0, Number(pct) || 0));
  element.value = value;
  element.setAttribute('value', String(value));
  element.setAttribute('aria-valuenow', String(Math.round(value)));
}
function _topPct(pct) {
  const bar = document.getElementById('cz-top-bar');
  const wrap = document.getElementById('cz-top-progress');
  if (!bar || !wrap) return;
  wrap.classList.remove('is-indeterminate');
  _setProgressElementValue(bar, pct);
}
function _topIndeterminate(label) {
  const wrap = document.getElementById('cz-top-progress'); if (!wrap) return;
  const bar = document.getElementById('cz-top-bar');
  wrap.classList.remove('is-error', 'is-done');
  wrap.classList.add('is-indeterminate');
  if (bar) {
    bar.removeAttribute('value');
    bar.removeAttribute('aria-valuenow');
  }
  _topLabel(label || '');
}
function _topError(label) {
  const wrap = document.getElementById('cz-top-progress'); if (!wrap) return;
  const bar = document.getElementById('cz-top-bar');
  wrap.classList.remove('is-indeterminate', 'is-done');
  wrap.classList.add('is-error');
  _setProgressElementValue(bar, 100);
  _topLabel(label || 'Falha ao processar o arquivo');
}
function _topDone(label) {
  const bar  = document.getElementById('cz-top-bar');
  const wrap = document.getElementById('cz-top-progress');
  if (!bar || !wrap) return;
  wrap.classList.remove('is-indeterminate', 'is-error');
  wrap.classList.add('is-done');
  _setProgressElementValue(bar, 100);
  _topLabel(label || '');
  setTimeout(() => {
    wrap.hidden = true;
    _setProgressElementValue(bar, 0);
    wrap.classList.remove('is-done');
  }, 900);
}
function _topReset() {
  const wrap = document.getElementById('cz-top-progress');
  const bar  = document.getElementById('cz-top-bar');
  if (wrap) { wrap.hidden = true; wrap.classList.remove('is-indeterminate','is-error','is-done'); }
  _setProgressElementValue(bar, 0);
  _topLabel('');
}

/* ── Feedback / progresso ───────────────────────────────────────────────── */
function _setFeedback(msg, type) {
  const el = document.getElementById('mensagem-feedback'); if (!el) return;
  el.textContent = msg;
  // Remove apenas os modificadores de estado anteriores — preserva classes estruturais
  // que possam estar no HTML (ex.: 'hidden', classes de layout).
  // Antes: el.className = ... sobrescrevia tudo, incluindo classes do HTML.
  el.classList.remove('feedback--success', 'feedback--error', 'feedback--info');
  if (type) el.classList.add(`feedback--${type}`);
  el.classList.remove('hidden');
}
function _clearFeedback() {
  const el = document.getElementById('mensagem-feedback');
  if (el) { el.textContent = ''; el.classList.add('hidden'); }
}
function _setProgress(pct) {
  // barra inferior (já existia)
  const c = document.getElementById('progress-container');
  const b = document.getElementById('progress-bar');
  if (c && b) {
    c.classList.remove('hidden');
    _setProgressElementValue(b, pct);
  }
  // barra do topo — espelha o mesmo valor
  _topPct(pct);
}
function _resetProgress() {
  const c = document.getElementById('progress-container');
  const b = document.getElementById('progress-bar');
  if (c) c.classList.add('hidden');
  _setProgressElementValue(b, 0);
  _topReset();
}
function _setSpinner(on) {
  const s = document.getElementById('spinner-compress'); if (!s) return;
  s.classList.toggle('hidden', !on);
}
function _setSteps(steps) {
  const el = document.getElementById('loading-steps'); if (!el) return;
  if (!steps || !steps.length) { el.innerHTML = ''; el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  el.innerHTML = steps.map(s => {
    const icon = s.status === 'done' ? '✓' : s.status === 'current' ? '→' : '○';
    return `<li class="ls-step ls-step--${s.status || 'pending'}">${icon} ${s.text}</li>`;
  }).join('');
}

/* ── Botão Limpar ───────────────────────────────────────────────────────── */
function bindClearButton() {
  if (document.__compressClearBound) return;
  document.__compressClearBound = true;
  document.addEventListener('click', ev => {
    if (!ev.target.closest('#btn-clear-all')) return;
    _resetCompressFlow();
  });
}

/* ── Estimativa de tamanho ──────────────────────────────────────────────── */
function _fmtKB(kb) {
  return kb >= 1024 ? `${(kb / 1024).toFixed(2)} MiB` : `${kb.toFixed(1)} KiB`;
}
function _fmtDecimalMB(bytes) {
  return `${(bytes / 1000000).toLocaleString('pt-BR', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} MB`;
}
function _fmtBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '—';
  if (bytes >= 1000000) return _fmtDecimalMB(bytes);
  return `${(bytes / 1000).toLocaleString('pt-BR', {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })} KB`;
}
function _resolveTargetAchieved(headerValue, targetMode, targetSizeBytes, finalBytes) {
  const normalized = String(headerValue || '').trim().toLowerCase();
  if (normalized === 'true') return true;
  if (normalized === 'false') return false;

  return Boolean(
    targetMode
    && targetSizeBytes > 0
    && finalBytes <= targetSizeBytes
  );
}

const _COMPRESS_WARNING_PRESENTATIONS = Object.freeze([
  Object.freeze({
    code: 'recompressao_jpeg_agressiva',
    text: 'Recompressão agressiva aplicada. Confira textos pequenos e imagens antes de enviar.',
    type: 'warning',
  }),
  Object.freeze({
    code: 'tons_de_cinza_aplicados',
    text: 'Tons de cinza aplicados. As cores do documento foram removidas.',
    type: 'warning',
  }),
  Object.freeze({
    code: 'target_not_achieved',
    text: 'Meta de tamanho não atingida.',
    type: 'warning',
  }),
  Object.freeze({
    code: 'interactive_content_preserved',
    text: 'Formulários, anotações ou assinaturas visuais foram preservados.',
    type: 'info',
  }),
  Object.freeze({
    code: 'Compressao pesada ignorada para preservar formularios, anotacoes ou assinaturas visuais.',
    text: 'Formulários, anotações ou assinaturas visuais foram preservados.',
    type: 'info',
  }),
  Object.freeze({
    code: 'Paginas com conteudo interativo foram preservadas; as demais foram comprimidas quando seguro.',
    text: 'Paginas interativas foram preservadas e apenas as paginas seguras foram recomprimidas.',
    type: 'info',
  }),
  Object.freeze({
    code: 'A aparencia visual foi preservada, mas qualquer regravacao pode invalidar a assinatura digital criptografica.',
    text: 'A aparência visual foi preservada, mas uma nova gravação pode invalidar a assinatura digital criptográfica.',
    type: 'warning',
  }),
  Object.freeze({
    code: 'Redimensionamento A4 ainda nao disponivel; configuracao ignorada.',
    text: 'O redimensionamento para A4 não foi aplicado para preservar o conteúdo interativo.',
    type: 'info',
  }),
  Object.freeze({
    code: 'compression_fallback:gs_larger',
    text: 'A versão recomprimida ficaria maior, então a menor versão válida foi mantida.',
    type: 'warning',
  }),
  Object.freeze({
    code: 'selected_baseline',
    text: 'A menor versão válida foi mantida.',
    type: 'warning',
  }),
  Object.freeze({
    code: 'group_original',
    text: 'Um ou mais grupos foram preservados sem recompressão.',
    type: 'warning',
  }),
]);

const _COMPRESS_FALLBACK_PRESENTATIONS = Object.freeze({
  preserved_interactive: Object.freeze({
    title: 'Conteúdo interativo preservado',
    subtitle: 'Não aplicamos compressão pesada porque este PDF possui formulários, anotações ou assinaturas que poderiam ser alterados.',
    feedback: 'Conteúdo interativo preservado. O arquivo preservado está disponível para download.',
    topLabel: 'Conteúdo interativo preservado',
    progressLabel: 'Arquivo preservado pronto.',
    downloadLabel: 'Baixar arquivo preservado',
    excludedWarningCodes: Object.freeze([
      'interactive_content_preserved',
      'Compressao pesada ignorada para preservar formularios, anotacoes ou assinaturas visuais.',
    ]),
  }),
  selected_baseline: Object.freeze({
    title: 'Melhor versão preservada',
    subtitle: 'O arquivo comprimido ficaria maior que a versão preparada, então mantivemos a menor versão válida.',
    feedback: 'Melhor versão preservada. A menor versão válida está disponível para download.',
    topLabel: 'Melhor versão preservada',
    progressLabel: 'Melhor versão pronta para download.',
    downloadLabel: 'Baixar melhor versão',
    excludedWarningCodes: Object.freeze(['selected_baseline', 'compression_fallback:gs_larger']),
  }),
  final_original: Object.freeze({
    title: 'Melhor versão preservada',
    subtitle: 'O arquivo comprimido ficaria maior que a versão preparada, então mantivemos a menor versão válida.',
    feedback: 'Melhor versão preservada. A menor versão válida está disponível para download.',
    topLabel: 'Melhor versão preservada',
    progressLabel: 'Melhor versão pronta para download.',
    downloadLabel: 'Baixar melhor versão',
    excludedWarningCodes: Object.freeze(['selected_baseline', 'compression_fallback:gs_larger']),
  }),
  gs_larger: Object.freeze({
    title: 'Melhor versão preservada',
    subtitle: 'O arquivo comprimido ficaria maior que a versão preparada, então mantivemos a menor versão válida.',
    feedback: 'Melhor versão preservada. A menor versão válida está disponível para download.',
    topLabel: 'Melhor versão preservada',
    progressLabel: 'Melhor versão pronta para download.',
    downloadLabel: 'Baixar melhor versão',
    excludedWarningCodes: Object.freeze(['selected_baseline', 'compression_fallback:gs_larger']),
  }),
  group_original: Object.freeze({
    title: 'Compressão não aplicada',
    subtitle: 'Um ou mais grupos foram preservados porque a recompressão não produziu um resultado válido ou menor.',
    feedback: 'Compressão não aplicada. O arquivo preservado está disponível para download.',
    topLabel: 'Compressão não aplicada',
    progressLabel: 'Arquivo preservado pronto.',
    downloadLabel: 'Baixar arquivo preservado',
    excludedWarningCodes: Object.freeze(['group_original']),
  }),
  partial: Object.freeze({
    title: 'Comprimido parcialmente',
    subtitle: 'Parte do documento foi comprimida; alguns trechos precisaram ser preservados.',
    feedback: 'Documento comprimido parcialmente. O resultado está disponível para download.',
    topLabel: 'Comprimido parcialmente',
    progressLabel: 'Resultado parcial pronto para download.',
    downloadLabel: 'Baixar resultado',
    excludedWarningCodes: Object.freeze([]),
  }),
  partial_interactive_preservation: Object.freeze({
    title: 'Compressao seletiva concluida',
    subtitle: 'As paginas interativas foram preservadas; somente as paginas seguras foram recomprimidas.',
    feedback: 'Compressao seletiva concluida. O conteudo interativo foi preservado.',
    topLabel: 'Compressao seletiva concluida',
    progressLabel: 'Resultado seletivo pronto para download.',
    downloadLabel: 'Baixar resultado',
    excludedWarningCodes: Object.freeze([
      'Paginas com conteudo interativo foram preservadas; as demais foram comprimidas quando seguro.',
    ]),
  }),
});

function _appendUniquePresentationMessage(messages, message) {
  if (!message?.text || messages.some(item => item.text === message.text)) return;
  messages.push({ text: message.text, type: message.type || 'info' });
}

function _buildHumanWarningMessages(rawWarnings, excludedCodes = []) {
  const source = String(rawWarnings || '');
  const excluded = new Set(excludedCodes);
  const messages = [];

  _COMPRESS_WARNING_PRESENTATIONS.forEach((warning) => {
    if (!excluded.has(warning.code) && source.includes(warning.code)) {
      _appendUniquePresentationMessage(messages, warning);
    }
  });
  return messages;
}

function _buildPresentationMessages(rawWarnings, {
  targetNotAchieved = false,
  excludedCodes = [],
} = {}) {
  const messages = [];
  if (targetNotAchieved) {
    _appendUniquePresentationMessage(messages, {
      text: 'Meta de tamanho não atingida.',
      type: 'warning',
    });
  }
  _buildHumanWarningMessages(rawWarnings, [
    'target_not_achieved',
    ...excludedCodes,
  ]).forEach(message => _appendUniquePresentationMessage(messages, message));
  return messages;
}

function _buildResultDetailRows({
  targetMode,
  targetAchieved,
  requestedTargetBytes,
  finalBytes,
}) {
  const finalText = _fmtDecimalMB(finalBytes);
  if (!targetMode) return [{ label: 'Tamanho final', value: finalText }];
  return [
    { label: 'Meta solicitada', value: _fmtDecimalMB(requestedTargetBytes) },
    {
      label: targetAchieved ? 'Tamanho final' : 'Melhor resultado seguro',
      value: finalText,
    },
  ];
}

function _buildTargetResultPresentation({
  targetAchieved,
  requestedTargetBytes,
  finalBytes,
  warnings = '',
}) {
  const targetText = _fmtDecimalMB(requestedTargetBytes);
  const finalText = _fmtDecimalMB(finalBytes);
  const aboveTargetText = _fmtDecimalMB(
    Math.max(0, finalBytes - requestedTargetBytes)
  );
  if (targetAchieved) {
    const messages = _buildHumanWarningMessages(warnings, ['target_not_achieved']);
    return {
      title: 'Meta atingida',
      subtitle: 'O arquivo ficou dentro do tamanho solicitado.',
      variant: 'success',
      sizeText: finalText,
      detailRows: [
        { label: 'Meta solicitada', value: targetText },
        { label: 'Tamanho final', value: finalText },
      ],
      messages,
      downloadLabel: 'Baixar arquivo',
      feedbackMsg: `Meta atingida. Meta solicitada: ${targetText}. Tamanho final: ${finalText}.`,
      feedbackType: 'success',
    };
  }

  const subtitle = `Não foi possível atingir ${targetText} sem comprometer ainda mais o documento.`;
  return {
    title: 'Meta não atingida',
    subtitle,
    variant: 'warning',
    sizeText: finalText,
    detailRows: [
      { label: 'Melhor resultado seguro', value: finalText },
      { label: 'Acima da meta em', value: aboveTargetText },
    ],
    messages: _buildHumanWarningMessages(warnings, ['target_not_achieved']),
    downloadLabel: 'Baixar melhor resultado',
    feedbackMsg: `${subtitle} Melhor resultado seguro: ${finalText}. `
               + `Acima da meta em: ${aboveTargetText}.`,
    feedbackType: 'info',
  };
}

function _buildCompressResultPresentation({
  fallback,
  targetMode,
  targetAchieved,
  requestedTargetBytes,
  finalBytes,
  warnings = '',
  reductionPct = 0,
}) {
  const fallbackCode = String(fallback || 'none').trim().toLowerCase();
  const targetNotAchieved = targetMode && targetAchieved !== true;
  const fallbackCopy = _COMPRESS_FALLBACK_PRESENTATIONS[fallbackCode];

  if (fallbackCopy) {
    const messages = _buildPresentationMessages(warnings, {
      targetNotAchieved,
      excludedCodes: fallbackCopy.excludedWarningCodes,
    });
    return {
      title: fallbackCopy.title,
      subtitle: fallbackCopy.subtitle,
      variant: 'warning',
      sizeText: _fmtDecimalMB(finalBytes),
      detailRows: _buildResultDetailRows({
        targetMode,
        targetAchieved: !targetNotAchieved,
        requestedTargetBytes,
        finalBytes,
      }),
      messages,
      downloadLabel: fallbackCopy.downloadLabel,
      feedbackMsg: fallbackCopy.feedback
        + (targetNotAchieved ? ' Meta de tamanho não atingida.' : ''),
      feedbackType: 'info',
      topLabel: fallbackCopy.topLabel,
      progressLabel: fallbackCopy.progressLabel,
    };
  }

  if (targetMode) {
    const resolvedTargetAchieved = fallbackCode === 'target_not_achieved'
      ? false
      : targetAchieved;
    const targetPresentation = _buildTargetResultPresentation({
      targetAchieved: resolvedTargetAchieved,
      requestedTargetBytes,
      finalBytes,
      warnings,
    });
    if (resolvedTargetAchieved) {
      const messages = [{ text: 'Meta de tamanho atingida.', type: 'success' }];
      targetPresentation.messages.forEach(
        message => _appendUniquePresentationMessage(messages, message)
      );
      return {
        ...targetPresentation,
        title: 'Arquivo comprimido',
        subtitle: 'Seu PDF foi comprimido com sucesso.',
        messages,
        downloadLabel: 'Baixar arquivo comprimido',
        feedbackMsg: `PDF comprimido com sucesso. Meta atingida: ${_fmtDecimalMB(requestedTargetBytes)}. Tamanho final: ${_fmtDecimalMB(finalBytes)}.`,
        topLabel: 'PDF comprimido com sucesso',
        progressLabel: 'PDF comprimido e pronto para download.',
      };
    }
    const subtitle = 'Não foi possível atingir o tamanho solicitado mantendo os limites de qualidade e segurança.';
    return {
      ...targetPresentation,
      title: 'Arquivo processado',
      subtitle,
      detailRows: _buildResultDetailRows({
        targetMode: true,
        targetAchieved: false,
        requestedTargetBytes,
        finalBytes,
      }),
      messages: _buildPresentationMessages(warnings, {
        targetNotAchieved: true,
      }),
      feedbackMsg: `Meta de tamanho não atingida. ${subtitle} Melhor resultado seguro: ${_fmtDecimalMB(finalBytes)}.`,
      topLabel: 'Melhor resultado seguro',
      progressLabel: 'Melhor resultado pronto para download.',
    };
  }

  if (!['none', 'final_compressed'].includes(fallbackCode)) {
    return {
      title: 'Arquivo processado',
      subtitle: 'O arquivo foi processado com segurança e está disponível para download.',
      variant: 'warning',
      sizeText: _fmtDecimalMB(finalBytes),
      detailRows: _buildResultDetailRows({
        targetMode: false,
        targetAchieved: false,
        requestedTargetBytes: 0,
        finalBytes,
      }),
      messages: _buildPresentationMessages(warnings),
      downloadLabel: 'Baixar arquivo',
      feedbackMsg: 'Arquivo processado com segurança e disponível para download.',
      feedbackType: 'info',
      topLabel: 'Arquivo processado',
      progressLabel: 'Arquivo pronto para download.',
    };
  }

  return {
    title: 'Arquivo comprimido',
    subtitle: 'Seu PDF foi comprimido com sucesso.',
    variant: 'success',
    sizeText: _fmtDecimalMB(finalBytes),
    detailRows: _buildResultDetailRows({
      targetMode: false,
      targetAchieved: true,
      requestedTargetBytes: 0,
      finalBytes,
    }),
    messages: _buildPresentationMessages(warnings),
    downloadLabel: 'Baixar arquivo comprimido',
    feedbackMsg: reductionPct > 0
      ? `PDF comprimido com sucesso. Redução de ${reductionPct}%.`
      : 'PDF comprimido com sucesso.',
    feedbackType: 'success',
    topLabel: reductionPct > 0
      ? `PDF pronto — −${reductionPct}%`
      : 'PDF comprimido com sucesso',
    progressLabel: 'PDF comprimido e pronto para download.',
  };
}
function _readTargetSizeMb() {
  const input = document.getElementById('target-size-mb');
  const value = Number(input?.value);
  const valid = Number.isFinite(value) && value >= 0.20 && value <= 50;
  if (input) input.setAttribute('aria-invalid', valid ? 'false' : 'true');
  return valid ? value : null;
}
function _syncCompressionModeUI() {
  const wrapper = document.querySelector('.compress-wrapper');
  const targetPanel = document.getElementById('target-size-controls');
  const manualPanel = document.getElementById('manual-controls');
  if (wrapper) wrapper.dataset.compressMode = _AState.mode;
  if (targetPanel) targetPanel.hidden = _AState.mode !== 'target_size';
  if (manualPanel) manualPanel.hidden = _AState.mode !== 'manual';
  document.querySelectorAll('input[name="compress-mode"]').forEach(input => {
    input.checked = input.value === _AState.mode;
  });
  _updateSummary();
}
function _estimateSize(page) {
  if (!page.include) return 0;
  if (page.keep_original) return parseFloat(page.estimated_size_kb);

  const orig = parseFloat(page.estimated_size_kb);
  const q    = Math.max(20, Math.min(100, Number(page.quality) || 80));
  const dpi  = Math.max(50, Math.min(300, Number(page.dpi) || 100));

  // size_factor vem do backend (enrich_page_analysis).
  // sf > 1 → página maior que a média → menos ganho marginal esperado.
  const sf  = parseFloat(page.size_factor || 1.0);
  const sfF = Math.min(1, Math.max(0.75, 1 / Math.pow(sf, 0.20)));

  // Placeholder monotônico em toda a faixa dos controles:
  // qualidade/DPI menores estimam compressão mais forte, sem piso absoluto
  // que achate configurações diferentes no mesmo resultado.
  const qF   = 0.72 + ((q - 20) / 80) * 0.26;       // 20→0.72; 100→0.98
  const dpiF = 0.84 + ((dpi - 50) / 250) * 0.155;   // 50→0.84; 300→0.995

  // O efeito geométrico do A4 depende do conteúdo e só é medido no resultado real.
  const estimated = orig * qF * dpiF * sfF;

  // Nunca promete arquivo maior que o original; a calibração real fica para
  // uma etapa posterior e o fallback do backend continua sendo a autoridade.
  return Math.min(orig, Math.max(0, estimated));
}

/* ── Resumo e contadores ────────────────────────────────────────────────── */
function _updateSummary() {
  const pages    = _AState.pages;
  const totOrig  = pages.reduce((s, p) => s + (p.include ? parseFloat(p.estimated_size_kb) : 0), 0);
  const totAdj   = pages.reduce((s, p) => s + _estimateSize(p), 0);
  const pct      = totOrig > 0 ? (((totOrig - totAdj) / totOrig) * 100).toFixed(1) : 0;
  const selCount = pages.filter(p => p.include).length;

  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set('cs-original-val', _fmtBytes(_AState.uploadedSizeBytes));
  set('cs-baseline-val', pages.length && totOrig > 0 ? `~${_fmtKB(totOrig)}` : '—');
  set('selected-count',  selCount);
  set('total-count',     pages.length);
  const adjLabelEl = document.getElementById('cs-adjusted-label');
  const estimateNote = document.getElementById('cs-estimate-note');
  const targetSizeMb = _AState.mode === 'target_size'
    ? _readTargetSizeMb()
    : null;
  if (_AState.mode === 'target_size') {
    if (adjLabelEl) adjLabelEl.textContent = 'Meta máxima';
    set(
      'cs-adjusted-val',
      pages.length && targetSizeMb !== null
        ? `≤ ${_fmtDecimalMB(targetSizeMb * 1000000)}`
        : '—'
    );
    if (estimateNote) {
      estimateNote.textContent = 'A busca usa MB decimal e margem operacional de 1%.';
    }
  } else {
    if (adjLabelEl) adjLabelEl.textContent = 'Estimativa inicial';
    set('cs-adjusted-val', totAdj > 0 ? `~${_fmtKB(totAdj)}` : '—');
    if (estimateNote) {
      estimateNote.textContent = 'Valor aproximado; o resultado real pode variar.';
    }
  }
  const badge = document.getElementById('cs-badge');
  if (badge) {
    // Badge textual removido do analyze — não classificamos "leve/moderada/forte"
    // porque a prévia não controla fallback real, grupos ou comportamento do GS.
    // O badge só é usado após o processamento real (resultado exato do backend).
    badge.textContent = '';
    badge.className   = 'cs-badge';
  }

  _updateProcessButtonState();

  document.querySelectorAll('.pac-size-adjusted').forEach(el => {
    const pn   = parseInt(el.closest('[data-page-number]')?.dataset?.pageNumber || '0', 10);
    const page = pages.find(p => p.page_number === pn); if (!page) return;
    const adj  = _estimateSize(page);
    el.textContent = `≈ ${_fmtKB(adj)}`;
    const bdg = el.closest('.pac-sizes')?.querySelector('.pac-reduction');
    if (bdg) {
      const original = parseFloat(page.estimated_size_kb);
      const reduction = original > 0 ? (original - adj) / original * 100 : 0;
      const visible = page.include && !page.keep_original && reduction > 0;
      bdg.textContent = visible ? `${reduction.toFixed(0)}% menor` : '';
      bdg.hidden = !visible;
    }
  });
}

function _updateProcessButtonState() {
  const button = document.getElementById('btn-process-with-settings');
  if (!button) return false;

  const hasAnalysis = (
    typeof _AState.analyseId === 'string'
    && _AState.analyseId.trim().length > 0
  );
  const hasIncludedPage = _AState.pages.some(page => page.include === true);
  const hasValidTarget = (
    _AState.mode !== 'target_size'
    || _readTargetSizeMb() !== null
  );
  const disabled = (
    _AState.inflight
    || !hasAnalysis
    || !hasIncludedPage
    || !hasValidTarget
  );

  button.disabled = disabled;
  button.setAttribute('aria-disabled', String(disabled));
  return !disabled;
}

/* ── Cards de página ────────────────────────────────────────────────────── */
const _THUMBNAIL_ROTATION_CLASSES = Object.freeze([
  'pac-rotation--0',
  'pac-rotation--90',
  'pac-rotation--180',
  'pac-rotation--270',
]);

function _normalizeThumbnailRotation(rotation) {
  const normalized = ((parseInt(rotation || 0, 10) % 360) + 360) % 360;
  return [0, 90, 180, 270].includes(normalized) ? normalized : 0;
}

function _setThumbnailRotation(image, rotation) {
  if (!image) return;
  const normalized = _normalizeThumbnailRotation(rotation);
  image.dataset.rotation = String(normalized);
  image.classList.remove(..._THUMBNAIL_ROTATION_CLASSES);
  image.classList.add('pac-thumbnail-image', `pac-rotation--${normalized}`);
}

function _buildPageCard(page) {
  const card = document.createElement('article');
  card.className = `page-analysis-card${page.is_large ? ' pac--large' : ''}${!page.include ? ' pac--excluded' : ''}`;
  card.setAttribute('data-page-number', String(page.page_number));
  card.setAttribute('role', 'listitem');

  const adjKB  = _estimateSize(page);
  const origKB = parseFloat(page.estimated_size_kb);
  const redPct = origKB > 0 ? (((origKB - adjKB) / origKB) * 100).toFixed(0) : 0;
  const dis    = cond => cond ? 'disabled' : '';
  const rotation = _normalizeThumbnailRotation(page.rotation);
  page.rotation = rotation;

  card.innerHTML = `
    <div class="pac-card-actions">
      <button type="button" class="pac-btn-toggle" data-action="toggle-include"
              title="${page.include ? 'Excluir página' : 'Incluir página'}"
              aria-label="${page.include ? 'Excluir página' : 'Incluir página'}">
        ${page.include
          ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
          : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
        }
      </button>
      <button type="button" class="pac-btn-rotate" data-action="rotate-cw"
              title="Girar 90° (horário)" aria-label="Girar página 90° no sentido horário">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 2v6h-6"/><path d="M21 13a9 9 0 1 1-3-7.7L21 8"/>
        </svg>
      </button>
    </div>
    <div class="pac-thumb" draggable="true">
      <img src="${page.thumbnail}" alt="Página ${page.page_number}" loading="lazy"
           decoding="async" data-rotation="${rotation}"
           width="240" height="338">
      ${page.is_large ? '<span class="pac-badge pac-badge--large">⚠ GRANDE</span>' : ''}
    </div>
    <div class="pac-info">
      <h4 class="pac-title">Pág. ${page.page_number}</h4>
      <p class="pac-dims">${page.width} × ${page.height} pt</p>
      <div class="pac-sizes">
        <span class="pac-size-original">≈ ${_fmtKB(origKB)}</span>
        <span class="pac-arrow" aria-hidden="true">→</span>
        <span class="pac-size-adjusted">≈ ${_fmtKB(adjKB)}</span>
        <span class="pac-reduction" ${parseFloat(redPct) > 0 ? '' : 'hidden'}>${redPct}% menor</span>
      </div>
    </div>
    <div class="pac-controls">
      <label class="pac-check">
        <input type="checkbox" data-field="resize_to_a4" ${page.resize_to_a4 ? 'checked' : ''} ${dis(!page.include || page.keep_original)}>
        <span>📐 Redimensionar para A4</span>
      </label>
      <label class="pac-check">
        <input type="checkbox" data-field="keep_original" ${page.keep_original ? 'checked' : ''} ${dis(!page.include)}>
        <span>🔒 Manter original</span>
      </label>
      <label class="pac-range">
        <span>Qualidade: <strong class="pac-quality-val">${page.quality}%</strong></span>
        <input type="range" data-field="quality" min="20" max="100" value="${page.quality}" ${dis(!page.include || page.keep_original)}>
      </label>
      <label class="pac-range">
        <span>DPI: <strong class="pac-dpi-val">${page.dpi}</strong></span>
        <input type="range" data-field="dpi" min="72" max="300" value="${page.dpi}" ${dis(!page.include || page.keep_original)}>
      </label>
    </div>`;
  _setThumbnailRotation(card.querySelector('.pac-thumb img'), rotation);
  return card;
}

function _refreshCardControls(card, page) {
  card.classList.toggle('pac--excluded', !page.include);
  const toggleBtn = card.querySelector('[data-action="toggle-include"]');
  if (toggleBtn) {
    toggleBtn.title     = page.include ? 'Excluir página' : 'Incluir página';
    toggleBtn.ariaLabel = toggleBtn.title;
    toggleBtn.innerHTML = page.include
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
  }
  card.querySelectorAll('[data-field="resize_to_a4"]').forEach(el => {
    el.checked = page.resize_to_a4 === true;
    el.disabled = !page.include || page.keep_original;
  });
  card.querySelectorAll('[data-field="keep_original"]').forEach(el => { el.disabled = !page.include; });
  card.querySelectorAll('[data-field="quality"], [data-field="dpi"]').forEach(el => { el.disabled = !page.include || page.keep_original; });
}

function _bindCardEvents(grid) {
  if (grid.__analysisEventsBound) return;
  grid.__analysisEventsBound = true;

  let _dragSrc = null;

  grid.addEventListener('dragstart', ev => {
    if (!ev.target.closest('.pac-thumb')) { ev.preventDefault(); return; }
    const card = ev.target.closest('[data-page-number]'); if (!card) return;
    _dragSrc = card;
    card.classList.add('pac--dragging');
    ev.dataTransfer.effectAllowed = 'move';
    ev.dataTransfer.setData('text/plain', card.dataset.pageNumber);
  });

  grid.addEventListener('dragend', ev => {
    const card = ev.target.closest('[data-page-number]'); if (!card) return;
    card.classList.remove('pac--dragging');
    grid.querySelectorAll('.pac--dragover').forEach(el => el.classList.remove('pac--dragover'));
    _dragSrc = null;
  });

  grid.addEventListener('dragover', ev => {
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'move';
    const card = ev.target.closest('[data-page-number]');
    if (!card || card === _dragSrc) return;
    grid.querySelectorAll('.pac--dragover').forEach(el => el.classList.remove('pac--dragover'));
    card.classList.add('pac--dragover');
  });

  grid.addEventListener('dragleave', ev => {
    const card = ev.target.closest('[data-page-number]');
    if (card && !card.contains(ev.relatedTarget)) card.classList.remove('pac--dragover');
  });

  grid.addEventListener('drop', ev => {
    ev.preventDefault();
    const target = ev.target.closest('[data-page-number]');
    if (!target || !_dragSrc || target === _dragSrc) return;
    target.classList.remove('pac--dragover');
    const allCards = [...grid.querySelectorAll('[data-page-number]')];
    const srcIdx   = allCards.indexOf(_dragSrc);
    const tgtIdx   = allCards.indexOf(target);
    if (srcIdx < tgtIdx) grid.insertBefore(_dragSrc, target.nextSibling);
    else                 grid.insertBefore(_dragSrc, target);
    const srcPN = parseInt(_dragSrc.dataset.pageNumber, 10);
    const tgtPN = parseInt(target.dataset.pageNumber,   10);
    const pages = _AState.pages;
    const si    = pages.findIndex(p => p.page_number === srcPN);
    const ti    = pages.findIndex(p => p.page_number === tgtPN);
    if (si !== -1 && ti !== -1) { const [moved] = pages.splice(si, 1); pages.splice(ti, 0, moved); }
  });

  grid.addEventListener('click', ev => {
    const btn  = ev.target.closest('[data-action]'); if (!btn) return;
    const card = btn.closest('[data-page-number]');  if (!card) return;
    const pn   = parseInt(card.dataset.pageNumber, 10);
    const page = _AState.pages.find(p => p.page_number === pn); if (!page) return;
    if (btn.dataset.action === 'toggle-include') {
      page.include = !page.include;
      _refreshCardControls(card, page);
      _updateSummary();
    }
    if (btn.dataset.action === 'rotate-cw') {
      const img = card.querySelector('.pac-thumb img');
      if (img) {
        const cur  = parseInt(page.rotation || 0, 10);
        const next = (cur + 90) % 360;
        page.rotation = next;
        _setThumbnailRotation(img, next);
      }
    }
  });
  // 'input' dispara continuamente durante o arraste do range (tempo real).
  // 'change' só disparava ao soltar — causava UI congelada ao mover o slider de quality/dpi.
  grid.addEventListener('input', ev => {
    const input = ev.target; if (!input.matches('input[type="range"]')) return;
    const card  = input.closest('[data-page-number]'); if (!card) return;
    const pn    = parseInt(card.dataset.pageNumber, 10);
    const page  = _AState.pages.find(p => p.page_number === pn); if (!page) return;
    const field = input.dataset.field;
    const val   = parseInt(input.value, 10);
    page[field] = val;
    if (field === 'quality') card.querySelector('.pac-quality-val').textContent = `${val}%`;
    if (field === 'dpi')     card.querySelector('.pac-dpi-val').textContent     = String(val);
    _updateSummary();
  }, { passive: true });

  // 'change' mantido exclusivamente para checkboxes (resize_to_a4 / keep_original / include).
  grid.addEventListener('change', ev => {
    const input = ev.target; if (!input.matches('input[type="checkbox"]')) return;
    const card  = input.closest('[data-page-number]'); if (!card) return;
    const pn    = parseInt(card.dataset.pageNumber, 10);
    const page  = _AState.pages.find(p => p.page_number === pn); if (!page) return;
    const field = input.dataset.field;
    page[field] = input.checked;
    if (field === 'keep_original' && input.checked) page.resize_to_a4 = false;
    _refreshCardControls(card, page);
    _updateSummary();
  }, { passive: true });
}

function _renderPageGrid() {
  const grid  = document.getElementById('page-analysis-grid'); if (!grid) return;
  const pages = _AState.filter === 'large'
    ? _AState.pages.filter(p => p.is_large)
    : _AState.pages;
  grid.innerHTML = '';
  pages.forEach(p => grid.appendChild(_buildPageCard(p)));
  _bindCardEvents(grid);
  _updateSummary();
}

/* ── Analyze ────────────────────────────────────────────────────────────── */
async function _runAnalyze(file) {
  if (_AState.inflight) return;
  _AState.analyseId = null;
  _AState.pages = [];
  _AState.uploadedSizeBytes = null;
  _AState.inflight = true;
  _updateProcessButtonState();
  _clearFeedback(); _resetProgress(); _setSpinner(true);
  _topShow('Enviando arquivo…'); _topPct(5);

  _setBlockState('cz-summary',         'loading');
  _setBlockState('cz-controls',        'loading');
  _setBlockState('page-analysis-grid', 'loading');
  ['global-quality', 'global-dpi', 'filter-all', 'filter-large'].forEach(id => {
    const el = document.getElementById(id); if (el) el.disabled = true;
  });

  const fileMB   = file.size / 1048576;
  const estPages = Math.max(3, Math.round(fileMB * 10));
  const steps = [
    { text: 'Enviando arquivo…',                       status: 'current' },
    { text: 'Extraindo metadados…',                    status: '' },
    { text: `Gerando miniaturas (≈${estPages} pág.)…`, status: '' },
    { text: 'Pronto!',                                 status: '' },
  ];
  _setSteps(steps);
  let _tickPct = 15;
  const _ticker = setInterval(() => {
    if (_tickPct < 88) {
      _tickPct += _tickPct < 50 ? 3 : _tickPct < 75 ? 1.5 : 0.5;
      _setProgress(_tickPct);
      // Atualiza label do topo conforme o progresso avança
      if (_tickPct < 50)      _topLabel(`Enviando arquivo… ${Math.round(_tickPct)}%`);
      else if (_tickPct < 80) _topLabel('Analisando páginas…');
      else                    _topLabel('Gerando miniaturas…');
    }
  }, 400);

  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    _setProgress(15);
    steps[0].status = 'done'; steps[1].status = 'current'; _setSteps(steps);
    _topLabel('Enviando arquivo… 15%');

    const resp = await fetch('/api/compress/analyze', {
      method: 'POST',
      headers: { 'X-CSRFToken': readCSRFToken() },
      body: fd,
    });

    steps[1].status = 'done'; steps[2].status = 'current'; _setSteps(steps);
    _topIndeterminate('Processando PDF…');

    if (!resp.ok) {
      let msg = `Erro ${resp.status}`;
      try { msg = (await resp.json()).error || msg; } catch (_) {}
      if (resp.status === 413) msg = 'Arquivo muito grande.';
      if (resp.status === 429) msg = 'Muitas requisições.';
      throw new Error(msg);
    }

    const analysis = await resp.json();
    clearInterval(_ticker);
    _setProgress(95);

    steps[2].status = 'done';
    steps[3].status = 'done';
    steps[3].text   = `✅ ${analysis.total_pages} páginas analisadas!`;
    _setSteps(steps);

    _AState.analyseId = analysis.analyse_id;
    _AState.uploadedSizeBytes = Number(analysis.uploaded_size_bytes) || file.size;
    _AState.pages     = analysis.pages.map(page => ({
      ...page,
      resize_to_a4: false,
      resize_to_a4_suggested: false,
      rotation: 0,
    }));
    _AState.filter    = 'all';

    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('cz-filename', analysis.filename);
    set('cz-pages', `${analysis.total_pages} páginas · ${_fmtBytes(_AState.uploadedSizeBytes)}` +
      (analysis.has_large_pages ? ' · ⚠ páginas grandes' : ''));

    const largeCnt = analysis.pages.filter(p => p.is_large).length;
    set('filter-all-count',   String(analysis.total_pages));
    set('filter-large-count', String(largeCnt));
    ['global-quality', 'global-dpi', 'filter-all', 'filter-large'].forEach(id => {
      const el = document.getElementById(id); if (el) el.disabled = false;
    });

    _setProgress(100);
    _topDone(`${analysis.total_pages} páginas prontas`);
    _renderPageGrid();

    _setBlockState('cz-summary',         'ready');
    _setBlockState('cz-controls',        'ready');
    _setBlockState('page-analysis-grid', 'ready');

  } catch (err) {
    clearInterval(_ticker);
    console.error('[compress] erro na análise:', err);
    _topError('Falha ao analisar o arquivo');
    _setFeedback('Erro ao analisar: ' + err.message, 'error');
    _setSteps([]);
    _setBlockState('cz-summary',         'empty');
    _setBlockState('cz-controls',        'empty');
    _setBlockState('page-analysis-grid', 'empty');  } finally {
    clearInterval(_ticker);
    _AState.inflight = false;
    _updateProcessButtonState();
    _setSpinner(false);
    // Limpa apenas a barra inferior após 2 s. O topo já foi tratado por
    // _topDone() (fade 900 ms) no sucesso ou _topError() no catch —
    // não chamamos _topReset() aqui para não apagar o estado do topo cedo.
    setTimeout(() => {
      const c = document.getElementById('progress-container');
      const b = document.getElementById('progress-bar');
      if (c) c.classList.add('hidden');
      _setProgressElementValue(b, 0);
      _setSteps([]);
    }, 2000);
  }
}

/* ── Upload ─────────────────────────────────────────────────────────────── */
function bindUploadOnce() {
  const input = document.getElementById('input-compress');
  if (!input || input.__czBound) return;
  input.__czBound = true;
  __GV_COMPRESS.inputBound = true;
  input.addEventListener('change', async () => {
    const file = input.files?.[0]; if (!file) return;
    _clearResultCard();
    _clearFeedback(); _resetProgress();
    await _runAnalyze(file);
  }, { passive: true });
}

/* ── Controles globais ──────────────────────────────────────────────────── */
function bindGlobalControls() {
  if (document.__gvGlobalCtrlBound) return;
  document.__gvGlobalCtrlBound = true;
  document.addEventListener('input', ev => {
    const el = ev.target;
    if (el.id === 'global-quality') {
      const v = parseInt(el.value, 10);
      const lbl = document.getElementById('global-quality-val');
      if (lbl) lbl.textContent = `${v}%`;
      _AState.pages.forEach(p => {
        if (!p.include || p.keep_original) return;
        p.quality = v;
        const card  = document.querySelector(`#page-analysis-grid [data-page-number="${p.page_number}"]`);
        if (!card) return;
        const range = card.querySelector('[data-field="quality"]');
        const label = card.querySelector('.pac-quality-val');
        if (range) range.value       = v;
        if (label) label.textContent = `${v}%`;
      });
      _updateSummary();
    }
    if (el.id === 'global-dpi') {
      const v = parseInt(el.value, 10);
      const lbl = document.getElementById('global-dpi-val');
      if (lbl) lbl.textContent = String(v);
      _AState.pages.forEach(p => {
        if (!p.include || p.keep_original) return;
        p.dpi = v;
        const card  = document.querySelector(`#page-analysis-grid [data-page-number="${p.page_number}"]`);
        if (!card) return;
        const range = card.querySelector('[data-field="dpi"]');
        const label = card.querySelector('.pac-dpi-val');
        if (range) range.value       = v;
        if (label) label.textContent = String(v);
      });
      _updateSummary();
    }
  }, { passive: true });
}

function bindCompressionModeControls() {
  if (document.__gvCompressionModeBound) return;
  document.__gvCompressionModeBound = true;

  document.addEventListener('change', ev => {
    const modeInput = ev.target.closest?.('input[name="compress-mode"]');
    if (modeInput) {
      _AState.mode = modeInput.value === 'manual' ? 'manual' : 'target_size';
      _syncCompressionModeUI();
      return;
    }
    if (ev.target.id === 'allow-grayscale') {
      _AState.allowGrayscale = ev.target.checked === true;
    }
  });

  document.addEventListener('input', ev => {
    if (ev.target.id !== 'target-size-mb') return;
    const targetSizeMb = _readTargetSizeMb();
    _AState.targetSizeMb = targetSizeMb;
    _updateSummary();
  });

  document.addEventListener('click', ev => {
    const shortcut = ev.target.closest?.('[data-target-size-mb]');
    if (!shortcut) return;
    const targetSizeMb = Number(shortcut.dataset.targetSizeMb);
    const input = document.getElementById('target-size-mb');
    if (input) input.value = String(targetSizeMb);
    _AState.targetSizeMb = targetSizeMb;
    _updateSummary();
  });
}

/* ── Filtros ────────────────────────────────────────────────────────────── */
function bindFilterButtons() {
  if (document.__gvFilterBound) return;
  document.__gvFilterBound = true;
  document.addEventListener('click', ev => {
    const btn = ev.target.closest('[data-filter]'); if (!btn) return;
    _AState.filter = btn.dataset.filter;
    document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active', b === btn));
    _renderPageGrid();
  });
}

/* ── Processar com configurações ────────────────────────────────────────── */
function _buildProcessPayload(rotations) {
  const payload = {
    analyse_id:    _AState.analyseId,
    mode:          _AState.mode,
    page_settings: _AState.pages.map(page => ({
      page_number: page.page_number,
      include: page.include === true,
      quality: page.quality,
      dpi: page.dpi,
      resize_to_a4: page.resize_to_a4 === true,
      keep_original: page.keep_original === true,
    })),
  };
  if (_AState.mode === 'target_size') {
    payload.target_size_mb = _AState.targetSizeMb;
    payload.allow_grayscale = _AState.allowGrayscale;
  }
  if (rotations && Object.keys(rotations).length) {
    payload.rotations = { ...rotations };
  }
  return payload;
}

function bindProcessWithSettings() {
  if (document.__gvProcessSettingsBound) return;
  document.__gvProcessSettingsBound = true;

  document.addEventListener('click', async ev => {
    if (!ev.target.closest('#btn-process-with-settings')) return;
    if (_AState.inflight) return;
    if (!_AState.analyseId) { _setFeedback('Sessão perdida. Faça upload novamente.', 'error'); return; }
    const included = _AState.pages.filter(p => p.include);
    if (!included.length) { _setFeedback('Selecione ao menos uma página.', 'error'); return; }
    if (_AState.mode === 'target_size') {
      const targetSizeMb = _readTargetSizeMb();
      if (targetSizeMb === null) {
        _setFeedback('Informe um tamanho entre 0,20 MB e 50 MB.', 'error');
        return;
      }
      _AState.targetSizeMb = targetSizeMb;
    }
    _AState.inflight = true;
    _updateProcessButtonState();
    _clearResultCard();
    _clearFeedback(); _resetProgress(); _setProgress(5);
    _topShow('Preparando…'); _topPct(5);
    const targetMode = _AState.mode === 'target_size';
    const steps = [
      { text: `Preparando ${included.length} página(s)…`, status: 'current' },
      { text: 'Enviando configurações…',                   status: '' },
      {
        text: targetMode
          ? 'Buscando o melhor perfil seguro…'
          : 'Comprimindo com Ghostscript…',
        status: '',
      },
      { text: 'Montando PDF final…',                       status: '' },
      { text: 'Arquivo pronto para download.',             status: '' },
    ];
    _setSteps(steps);

    try {      steps[0].status = 'done'; steps[1].status = 'current'; _setSteps(steps); _setProgress(20);
      _topLabel('Preparando páginas…');

      const rotMap = {};
      _AState.pages.forEach(p => {
        const deg = parseInt(p.rotation || 0, 10) || 0;
        if (deg) rotMap[String(p.page_number)] = deg;
      });

      const payload = _buildProcessPayload(rotMap);
      steps[1].status = 'done'; steps[2].status = 'current'; _setSteps(steps); _setProgress(35);
      _topIndeterminate(
        targetMode
          ? 'Buscando o melhor perfil seguro…'
          : 'Comprimindo com Ghostscript…'
      );

      const resp = await fetch('/api/compress/process-with-settings', {
        method:  'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken':  readCSRFToken(),
          'Accept':       'application/pdf',
        },
        body: JSON.stringify(payload),
      });

      steps[2].status = 'done'; steps[3].status = 'current'; _setSteps(steps); _setProgress(70);
      _topLabel('Montando PDF final…');

      if (!resp.ok) {
        let msg = `Erro ${resp.status}`;
        try { msg = (await resp.json()).error || msg; } catch (_) {}
        if (resp.status === 404) msg = 'Sessão expirada. Faça upload novamente.';
        if (resp.status === 429) msg = 'Muitas requisições.';
        throw new Error(msg);
      }

      // Headers ANTES de resp.blob() — ordem obrigatória
      const sizeUploadedBytes = parseInt(
        resp.headers.get('X-Size-Uploaded-Bytes') || '0',
        10
      );
      const sizeBaselineBytes = parseInt(
        resp.headers.get('X-Size-Baseline-Bytes') || '0',
        10
      );
      const sizeFinalBytes = parseInt(
        resp.headers.get('X-Size-Final-Bytes') || '0',
        10
      );
      const sizeOrigKB = parseFloat(resp.headers.get('X-Size-Original-KB') || '0');
      const sizeBaselineKB = parseFloat(
        resp.headers.get('X-Size-Baseline-KB') || '0'
      );
      const sizeFinalKB = parseFloat(resp.headers.get('X-Size-Final-KB') || '0');
      const redPct = parseFloat(
        resp.headers.get('X-Baseline-Reduction-Pct')
        || resp.headers.get('X-Reduction-Pct')
        || '0'
      );
      const fallback    = (resp.headers.get('X-Fallback') || 'none').trim();
      const warnings    = (resp.headers.get('X-Compress-Warnings') || '').trim();
      const targetSizeBytes = parseInt(
        resp.headers.get('X-Target-Size-Bytes') || '0',
        10
      );
      const targetAchievedHeader = resp.headers.get('X-Target-Achieved') || '';
      const compressionAttempts = parseInt(
        resp.headers.get('X-Compression-Attempts') || '0',
        10
      );
      const compressionProfile = (
        resp.headers.get('X-Compression-Profile') || ''
      ).trim();
      const compressionElapsedSec = parseFloat(
        resp.headers.get('X-Compression-Elapsed-Sec') || '0'
      );
      const preservedInteractive = fallback === 'preserved_interactive';
      const selectedBaseline = (
        fallback === 'selected_baseline'
        || fallback === 'final_original'
        || fallback === 'gs_larger'
      );
      const groupOriginal = fallback === 'group_original';
      const partialFallback = (
        fallback === 'partial'
        || fallback === 'partial_interactive_preservation'
      );
      const specialFallback = (
        preservedInteractive || selectedBaseline || groupOriginal || partialFallback
      );

      const blob = await resp.blob();
      _setProgress(95);
      if (!blob?.size) throw new Error('Servidor retornou arquivo vazio.');
      const targetAchieved = _resolveTargetAchieved(
        targetAchievedHeader,
        targetMode,
        targetSizeBytes,
        blob.size
      );

      // Se os headers X-* foram bloqueados pelo proxy (todos chegam como 0),
      // usa o tamanho real do blob como fallback para o campo "Resultado".
      const estimatedBaselineKB = _AState.pages.reduce(
        (s, p) => s + (p.include ? parseFloat(p.estimated_size_kb) : 0), 0
      );
      const effectiveUploadedBytes = sizeUploadedBytes > 0
        ? sizeUploadedBytes
        : (_AState.uploadedSizeBytes || sizeOrigKB * 1024);
      const effectiveBaselineBytes = sizeBaselineBytes > 0
        ? sizeBaselineBytes
        : (sizeBaselineKB > 0 ? sizeBaselineKB * 1024 : estimatedBaselineKB * 1024);
      const effectiveFinalBytes = blob.size;
      const effectiveRedPct = redPct > 0 ? redPct
        : (effectiveBaselineBytes > 0 && effectiveFinalBytes < effectiveBaselineBytes
            ? parseFloat(((1 - effectiveFinalBytes / effectiveBaselineBytes) * 100).toFixed(1))
            : 0);

      const resultPresentation = _buildCompressResultPresentation({
        fallback,
        targetMode,
        targetAchieved,
        requestedTargetBytes: targetMode
          ? Number(payload.target_size_mb) * 1000000
          : 0,
        finalBytes: effectiveFinalBytes,
        warnings,
        reductionPct: effectiveRedPct,
      });

      steps[3].status = 'done';
      steps[4].status = 'done';
      steps[4].text = resultPresentation.progressLabel;
      _setSteps(steps);

      const filename = 'comprimido.pdf';
      const url = URL.createObjectURL(blob);
      __GV_COMPRESS._resultUrl = url;
      const a   = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.hidden = true;
      document.body.appendChild(a);
      a.click();
      a.remove();

      _setProgress(100);

      // ── Barra do topo — concluída (desaparece após 900 ms automaticamente) ─
      _topDone(resultPresentation.topLabel);

      // ── Feedback textual ──────────────────────────────────────────────────
      const feedbackMsg = resultPresentation.feedbackMsg;
      const feedbackType = resultPresentation.feedbackType;
      _clearFeedback();
      _showResultCard({
        blob,
        resultUrl: url,
        filename,
        sizeBytes: blob.size,
        feedbackMsg,
        feedbackType,
        presentation: resultPresentation,
      });

      // ── Card superior → resultado REAL (ou melhor estimativa disponível) ─
      const origEl = document.getElementById('cs-original-val');
      if (origEl) origEl.textContent = _fmtBytes(effectiveUploadedBytes);
      const baselineEl = document.getElementById('cs-baseline-val');
      if (baselineEl) baselineEl.textContent = _fmtBytes(effectiveBaselineBytes);
      const adjLabelEl = document.getElementById('cs-adjusted-label');
      if (adjLabelEl) adjLabelEl.textContent = 'Resultado';
      const estimateNote = document.getElementById('cs-estimate-note');
      if (estimateNote) estimateNote.textContent = 'Tamanho medido pelo servidor.';
      const adjEl = document.getElementById('cs-adjusted-val');
      if (adjEl) adjEl.textContent = _fmtBytes(effectiveFinalBytes);
      const badge = document.getElementById('cs-badge');
      if (badge) {
        if (targetMode && !specialFallback) {
          badge.textContent = targetAchieved ? 'Meta atingida' : 'Acima da meta';
          badge.className = targetAchieved
            ? 'cs-badge cs-badge--good'
            : 'cs-badge';
          if (estimateNote) {
            const attemptLabel = compressionAttempts === 1
              ? '1 tentativa'
              : `${compressionAttempts} tentativas`;
            const profileLabel = compressionProfile
              ? ` · perfil ${compressionProfile.replaceAll('_', ' ')}`
              : '';
            const elapsedLabel = compressionElapsedSec > 0
              ? ` · ${compressionElapsedSec.toLocaleString('pt-BR')} s`
              : '';
            estimateNote.textContent = `Tamanho medido pelo servidor · ${attemptLabel}${profileLabel}${elapsedLabel}.`;
          }
        } else if (preservedInteractive) {
          badge.textContent = 'Interativo preservado';
          badge.className   = 'cs-badge';
        } else if (selectedBaseline) {
          badge.textContent = 'Sem ganho';
          badge.className   = 'cs-badge';
        } else if (groupOriginal) {
          badge.textContent = 'Sem compressão';
          badge.className   = 'cs-badge';
        } else if (partialFallback) {
          badge.textContent = 'Parcial';
          badge.className   = 'cs-badge';
        } else if (effectiveRedPct > 0) {
          // Indica se o valor vem dos headers reais ou do cálculo de fallback
          const suffix = sizeFinalBytes > 0 || sizeFinalKB > 0 ? 'real' : 'aprox.';
          badge.textContent = `−${effectiveRedPct}% ${suffix}`;
          badge.className   = 'cs-badge cs-badge--good';
        }
      }

      clearTimeout(__GV_COMPRESS._resetTimer);
    } catch (err) {
      console.error('[compress] erro ao processar:', err);
      _topError('Falha: ' + err.message);
      _setFeedback('Erro: ' + err.message, 'error');
      _setSteps([]);
    } finally {
      _AState.inflight = false;
      _updateProcessButtonState();
      // Não chamamos _resetProgress() aqui: _topDone já agenda o fade do topo,
      // e _topError deve permanecer visível até o próximo fluxo iniciar.
      // A barra inferior (#progress-container) fica em 100% ou no estado de erro
      // até o auto-reset de _RESET_DELAY_MS ou o próximo clique em Processar.
    }
  });
}

/* ── Init ───────────────────────────────────────────────────────────────── */
function init() {
  bindClearButton();
  bindUploadOnce();
  bindGlobalControls();
  bindCompressionModeControls();
  bindFilterButtons();
  bindProcessWithSettings();
  _syncCompressionModeUI();
}

document.addEventListener('DOMContentLoaded', () => {
  init();
  console.debug('[compress] DOMContentLoaded — init completo');
});

try { window.GV_COMPRESS_GET_STATE = () => ({ ..._AState }); } catch (_) {}
