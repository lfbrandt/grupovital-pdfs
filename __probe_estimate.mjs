// Prova matemática exata da fórmula _estimateSize do compress.js
// Rodar com: node __probe_estimate.mjs

function estimateSize(orig, q, dpi, sf = 1.0, resize_to_a4 = false, w = 595, h = 842) {
  if (q === undefined || dpi === undefined) return orig;

  const sfF  = 1 / Math.pow(sf, 0.20);
  const qF   = 0.70 + (q / 100) * 0.25;
  const dpiF = Math.pow(dpi / 150, 1.3);

  let rzF = 1;
  if (resize_to_a4) {
    const a = w * h;
    rzF = a > 0 ? Math.min(1, (595 * 842) / a) : 1;
  }

  const raw = orig * qF * dpiF * sfF * rzF;
  const clamped = Math.max(orig * 0.55, Math.min(orig, Math.max(10, raw)));
  return { raw: +raw.toFixed(2), clamped: +clamped.toFixed(2), hitClamp: raw < orig * 0.55 };
}

const ORIG = 500;  // 500 KB — página típica
const LINE = '─'.repeat(72);

// ── 1. SWEEP DE QUALITY com DPI=100 (default do backend para páginas normais) ──
console.log('\n' + LINE);
console.log('QUALITY sweep | orig=500 KB | dpi=100 (default backend) | sf=1.0');
console.log(LINE);
console.log('quality │  qF   │ dpiF  │  raw KB │ clamped KB │ hitClamp │ Δ vs q=80');
console.log(LINE);
const base80_dpi100 = estimateSize(ORIG, 80, 100).clamped;
[20, 40, 60, 80, 100].forEach(q => {
  const r = estimateSize(ORIG, q, 100);
  const qF = (0.70 + (q/100)*0.25).toFixed(4);
  const dpiF = Math.pow(100/150, 1.3).toFixed(4);
  const delta = (r.clamped - base80_dpi100).toFixed(2);
  const mark = r.hitClamp ? '  *** CLAMP ***' : '';
  console.log(
    `  q=${String(q).padStart(3)}  │ ${qF} │ ${dpiF} │ ${String(r.raw).padStart(7)} │ ${String(r.clamped).padStart(10)} │ ${String(r.hitClamp).padStart(8)} │ ${delta.padStart(7)} KB${mark}`
  );
});

// ── 2. SWEEP DE DPI com quality=80 (default backend para páginas normais) ──
console.log('\n' + LINE);
console.log('DPI sweep     | orig=500 KB | quality=80 (default backend) | sf=1.0');
console.log(LINE);
console.log('dpi    │ dpiF   │  qF   │  raw KB │ clamped KB │ hitClamp │ Δ vs dpi=100');
console.log(LINE);
const base_dpi100_q80 = estimateSize(ORIG, 80, 100).clamped;
[50, 72, 100, 150, 200, 300].forEach(d => {
  const r = estimateSize(ORIG, 80, d);
  const dpiF = Math.pow(d/150, 1.3).toFixed(4);
  const qF = (0.70 + (80/100)*0.25).toFixed(4);
  const delta = (r.clamped - base_dpi100_q80).toFixed(2);
  const mark = r.hitClamp ? '  *** CLAMP ***' : '';
  console.log(
    `dpi=${String(d).padStart(3)} │ ${dpiF} │ ${qF} │ ${String(r.raw).padStart(7)} │ ${String(r.clamped).padStart(10)} │ ${String(r.hitClamp).padStart(8)} │ ${delta.padStart(8)} KB${mark}`
  );
});

// ── 3. CLAMP FLOOR: quanto precisa ser o raw para escapar do clamp com dpi=100 ──
console.log('\n' + LINE);
console.log('CLAMP analysis | dpi=100 | sf=1.0 | clamp_floor = orig * 0.55');
console.log(LINE);
const dpiF100 = Math.pow(100/150, 1.3);
const clampFloor = ORIG * 0.55;
console.log(`clamp_floor = ${ORIG} * 0.55 = ${clampFloor} KB`);
console.log(`dpiF(100) = ${dpiF100.toFixed(6)}`);
console.log(`Para escapar do clamp com dpi=100: precisa qF > ${(clampFloor / (ORIG * dpiF100)).toFixed(4)}`);
const minQF = clampFloor / (ORIG * dpiF100);
const minQ  = (minQF - 0.70) / 0.25 * 100;
console.log(`Ou seja: quality > ${minQ.toFixed(1)}  (todo o range de quality fica ABAIXO do clamp com dpi=100)`);

// ── 4. Qual DPI mínimo para que quality ESCAPE do clamp com q=20? ──
console.log('\n' + LINE);
console.log('Com qual DPI mínimo o quality=20 escapa do clamp?');
console.log(LINE);
const qF20 = 0.70 + (20/100)*0.25;
// raw = orig * qF20 * dpiF * sfF >= orig * 0.55
// dpiF >= 0.55 / qF20
// dpiF = Math.pow(dpi/150, 1.3) >= 0.55/qF20
// dpi >= 150 * (0.55/qF20)^(1/1.3)
const minDpiF = 0.55 / qF20;
const minDpiForQ20 = 150 * Math.pow(minDpiF, 1/1.3);
console.log(`qF(q=20) = ${qF20.toFixed(4)}`);
console.log(`Min dpiF para escapar clamp com q=20 = ${minDpiF.toFixed(4)}`);
console.log(`Min DPI para escapar clamp com q=20  = ${minDpiForQ20.toFixed(1)}`);

// ── 5. SIMULAÇÃO DO QUE O USUÁRIO VÊ: mover quality 20→100 com dpi=100 ──
console.log('\n' + LINE);
console.log('O QUE O USUÁRIO VÊ ao mover quality de 20 a 100 com dpi=100:');
console.log(LINE);
console.log(`Todos os valores ficam travados no clamp_floor = ${clampFloor} KB`);
console.log('Não existe variação perceptível. A UI parece "travada" mesmo que o state mude.');
console.log('Isso é INDEPENDENTE do binding/evento — é a fórmula que mata o efeito.');

// ── 6. PROPOSTA: nova fórmula com range útil ──
console.log('\n' + LINE);
console.log('PROPOSTA DE CORREÇÃO — nova fórmula com quality tendo range real:');
console.log(LINE);
// Nova abordagem: o clamp passa a depender de quality
// floor dinâmico: em vez de 0.55 fixo, usa qF como piso
// qF(q=20)=0.75 → floor=0.75*orig  (30% menos redução máxima possível)
// qF(q=80)=0.90 → floor=0.90*orig  (só 10%)
// Mas isso inverte o sentido. A ideia correta é:
//   resultado = orig * qF * dpiF * sfF * rzF
//   sem clamp que mate a quality, ou com clamp que siga a quality
// Nova proposta: ampliar range de qF: piso 0.40, amplitude 0.55
// q=100→0.95  q=80→0.84  q=60→0.73  q=40→0.62  q=20→0.51
function estimateSizeNew(orig, q, dpi, sf = 1.0, resize_to_a4 = false, w = 595, h = 842) {
  const sfF  = 1 / Math.pow(sf, 0.20);
  const qF   = 0.40 + (q / 100) * 0.55;   // ← NOVO: q=20→0.51  q=80→0.84  q=100→0.95
  const dpiF = Math.pow(dpi / 150, 1.3);
  let rzF = 1;
  if (resize_to_a4) {
    const a = w * h;
    rzF = a > 0 ? Math.min(1, (595 * 842) / a) : 1;
  }
  const raw = orig * qF * dpiF * sfF * rzF;
  // Clamp dinâmico: piso segue qF para que quality baixa possa reduzir mais
  const floorFactor = Math.max(0.30, qF * dpiF * sfF * rzF * 0.95); // não infla
  const clamped = Math.max(orig * Math.min(0.55, floorFactor), Math.min(orig, Math.max(10, raw)));
  return { raw: +raw.toFixed(2), clamped: +clamped.toFixed(2) };
}

console.log('quality │  qF(new) │ dpiF  │  raw KB │ clamped KB │ Δ vs q=80');
console.log(LINE);
const newBase80 = estimateSizeNew(ORIG, 80, 100).clamped;
[20, 40, 60, 80, 100].forEach(q => {
  const r = estimateSizeNew(ORIG, q, 100);
  const qF = (0.40 + (q/100)*0.55).toFixed(4);
  const dpiF = Math.pow(100/150, 1.3).toFixed(4);
  const delta = (r.clamped - newBase80).toFixed(2);
  console.log(
    `  q=${String(q).padStart(3)}  │   ${qF} │ ${dpiF} │ ${String(r.raw).padStart(7)} │ ${String(r.clamped).padStart(10)} │ ${delta.padStart(7)} KB`
  );
});
