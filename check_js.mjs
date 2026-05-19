import { readFileSync } from 'fs';

const src = readFileSync('app/static/js/merge-page.js', 'utf8');

// Valida sintaxe
try {
  new Function(src);
  console.log('[OK] Sintaxe JS valida');
} catch (e) {
  console.error('[FAIL] Sintaxe JS INVALIDA:', e.message);
  process.exit(1);
}

const lines = src.split('\n');
console.log('[OK] Linha 1:', JSON.stringify(lines[0]));

const checks = [
  ['texto solto py/markdown na linha 1', /^py\b|^```|^~~~/.test(lines[0])],
  ['listener merge:sync chamando syncStateFromDOM', /addEventListener.*merge:sync[\s\S]{0,200}syncStateFromDOM/.test(src)],
  ['double-binding btnGo (deve ser 1)', (src.match(/btnGo\.addEventListener/g) || []).length === 1],
  ['X-CSRFToken presente', /X-CSRFToken/.test(src)],
  ['Content-Type manual indevido', /headers.*Content-Type.*multipart|Content-Type.*boundary/i.test(src)],
  ['_isSubmitting guard no topo de submitMerge', /if \(_isSubmitting\) return/.test(src)],
  ['_isSubmitting = false no finally', /_isSubmitting = false/.test(src)],
  ['enableActions respeita _isSubmitting', /_isSubmitting \|\|/.test(src)],
  ['hideFeedback chamado em clearAll', /clearAll[\s\S]{1,400}hideFeedback\(\)/.test(src)],
  ['showFeedback usa textContent', /textContent = msg/.test(src)],
  ['X-Merge-Warnings lido', /X-Merge-Warnings/.test(src)],
  ['FormData sem Content-Type manual', !/fd\.append.*Content-Type/i.test(src)],
];

let allOk = true;
for (const [label, result] of checks) {
  // Para checks booleanos: true = risco encontrado vs true = feature presente
  // Encoding: checks[1,4] -> true = PROBLEMA; resto -> true = OK
  const isRisk = label.includes('solto') || label.includes('loop') ||
                 label.includes('indevido') || label.includes('listener merge:sync');
  const ok = isRisk ? !result : !!result;
  console.log((ok ? '[OK]' : '[FAIL]'), label + ':', result);
  if (!ok) allOk = false;
}

console.log('');
console.log(allOk ? '>>> TUDO OK <<<' : '>>> ATENCAO: verificar itens FAIL acima <<<');
