// Calibração de _estimateSize com ponto real:
// orig=5.81 MB, result=4.01 MB, quality=77, dpi=100
// 54 páginas com páginas grandes → sf médio ~1.2 (estimativa conservadora)

const ORIG = 5.81 * 1024; // KB
const REAL = 4.01 * 1024; // KB
const Q    = 77;
const DPI  = 100;
const SF   = 1.2; // estimativa sf médio com páginas grandes

const L = '─'.repeat(60);

// ── Fórmula ATUAL (após patch anterior) ──────────────────────────────────
function estAtual(orig, q, dpi, sf = 1.0) {
  const sfF  = 1 / Math.pow(sf, 0.20);
  const qF   = 0.40 + (q / 100) * 0.55;
  const dpiF = Math.pow(dpi / 150, 1.3);
  const raw  = orig * qF * dpiF * sfF;
  return Math.min(orig, Math.max(10, raw));
}

const atual = estAtual(ORIG, Q, DPI, SF);
const mult_atual = atual / ORIG;
const mult_real  = REAL  / ORIG;

console.log(L);
console.log('PONTO DE CALIBRAÇÃO REAL');
console.log(L);
console.log(`orig      = ${ORIG.toFixed(1)} KB`);
console.log(`real      = ${REAL.toFixed(1)} KB`);
console.log(`mult_real = ${mult_real.toFixed(4)}  (${(mult_real*100).toFixed(1)}%)`);
console.log(L);
console.log('FÓRMULA ATUAL → resultado e multiplicador');
console.log(L);
console.log(`sf=${SF}, q=${Q}, dpi=${DPI}`);
const sfF_a = 1/Math.pow(SF,0.20);
const qF_a  = 0.40 + (Q/100)*0.55;
const dpiF_a= Math.pow(DPI/150,1.3);
console.log(`  sfF  = ${sfF_a.toFixed(4)}`);
console.log(`  qF   = ${qF_a.toFixed(4)}`);
console.log(`  dpiF = ${dpiF_a.toFixed(4)}`);
console.log(`  raw  = ${(ORIG*qF_a*dpiF_a*sfF_a).toFixed(1)} KB`);
console.log(`  prev = ${atual.toFixed(1)} KB  (${(mult_atual*100).toFixed(1)}% do orig)`);
console.log(`  erro = ${(atual - REAL).toFixed(1)} KB vs real`);
console.log(`  REAL = ${REAL.toFixed(1)} KB  (${(mult_real*100).toFixed(1)}% do orig)`);

// ── Análise: o que precisa mudar ─────────────────────────────────────────
console.log(L);
console.log('DIAGNÓSTICO: por que estima tão baixo?');
console.log(L);
// Com sf=1.0 (página normal), o mult seria:
const sfF_1 = 1/Math.pow(1.0,0.20);
const raw_sf1 = ORIG * qF_a * dpiF_a * sfF_1;
console.log(`Com sf=1.0: prev=${raw_sf1.toFixed(1)} KB (${(raw_sf1/ORIG*100).toFixed(1)}%)`);
// O problema principal: qF muito baixo puxando o resultado para baixo
// Meta: mult final ≈ 0.691 com q=77, dpi=100, sf≈1.0–1.2
// Equação: orig * qF_new * dpiF * sfF = REAL
// qF_new = REAL / (orig * dpiF * sfF)
const qF_needed_sf10 = mult_real / (dpiF_a * sfF_1);
const qF_needed_sf12 = mult_real / (dpiF_a * sfF_a);
console.log(`qF necessário para bater REAL com sf=1.0: ${qF_needed_sf10.toFixed(4)}`);
console.log(`qF necessário para bater REAL com sf=1.2: ${qF_needed_sf12.toFixed(4)}`);
// qF = piso + (q/100)*amplitude  →  piso = qF_needed - (q/100)*amplitude
// Com amplitude=0.55 e q=77: piso = qF_needed - 0.77*0.55 = qF_needed - 0.4235

const A = 0.55; // amplitude — queremos manter qF(100)=0.95 fixo para não inflacionar
// Se qF(100) = piso + 1.00*A = 0.95  →  piso = 0.95 - A
// Com A=0.55 →  piso = 0.40  (exatamente o atual)
// Problema: amplitude 0.55 é muito grande, puxa q=77 para baixo demais
// Alternativa: reduzir amplitude e subir piso, mantendo qF(100)=0.95
// qF(q) = piso + (q/100) * A   com qF(100) = 0.95  →  piso = 0.95 - A
// Queremos qF(77) ≈ qF_needed_sf10 ≈ 1.17  → impossível: qF > 1 inflaria
// Conclusão: o problema NÃO é a fórmula de qF. É que dpiF(100) = 0.59 é
// o fator dominante que "amassa" o resultado — dpi=100 já reduz 41%.
// Para bater 0.69 com dpi=100, precisamos qF ≈ 1.17 — não faz sentido para
// uma estimativa de *compressão*. O backend com q=77, dpi=100 obteve 30.9%
// de redução — valor MUITO MENOR do que o que dpiF(100)=0.59 projeta (41%).

console.log(L);
console.log('ROOT CAUSE: dpiF superestima o efeito do DPI');
console.log(L);
console.log(`dpiF(100) atual = ${dpiF_a.toFixed(4)} → projeta redução de ${((1-dpiF_a)*100).toFixed(1)}%`);
console.log(`Backend real com dpi=100, q=77 → redução real = ${((1-mult_real)*100).toFixed(1)}%`);
console.log(`Razão: dpiF expoente 1.3 com âncora 150 é agressivo demais para dpi < 150`);
// Para que a fórmula total (qF * dpiF * sfF) ≈ 0.69 com q=77, sf≈1.0:
// qF(77) com nova fórmula × dpiF_new(100) ≈ 0.69
// Se qF(77) ≈ 0.87 (fórmula conservadora), então dpiF_new(100) ≈ 0.69/0.87 ≈ 0.793
// dpiF = pow(100/150, exp) = 0.793  →  exp = log(0.793)/log(100/150) = ?
const exp_needed = Math.log(0.793) / Math.log(100/150);
console.log(`Para dpiF(100)=0.793: expoente necessário = ${exp_needed.toFixed(3)}`);
// Ou: manter expoente e ajustar âncora
// dpiF = pow(dpi/anchor, 1.3) = 0.793  →  anchor = dpi / 0.793^(1/1.3)
const anchor_needed = 100 / Math.pow(0.793, 1/1.3);
console.log(`Para dpiF(100)=0.793 com exp=1.3: âncora necessária = ${anchor_needed.toFixed(1)}`);

// ── Testar nova fórmula com abordagem CONSERVADORA ───────────────────────
// Abordagem: escalar o produto final (qF*dpiF) por um fator de conservadorismo
// que reconhece que o backend nem sempre consegue 100% do potencial teórico.
// Fator de conservadorismo: C = 0.75 (o backend real consegue ~75% do máximo teórico)
// Ou equivalentemente: mudar âncora do dpiF de 150 para ~110
console.log(L);
console.log('NOVA FÓRMULA — sweep de candidatos');
console.log(L);

function estNova(orig, q, dpi, sf, anchor, qPiso, qAmp) {
  const sfF  = 1 / Math.pow(sf, 0.20);
  const qF   = qPiso + (q / 100) * qAmp;
  const dpiF = Math.pow(dpi / anchor, 1.3);
  const raw  = orig * qF * dpiF * sfF;
  return Math.min(orig, Math.max(10, raw));
}

// Candidatos: variando âncora e qF
const candidates = [
  { anchor: 110, qPiso: 0.60, qAmp: 0.35, label: 'anchor=110 qF=0.60+0.35q' },
  { anchor: 110, qPiso: 0.65, qAmp: 0.30, label: 'anchor=110 qF=0.65+0.30q' },
  { anchor: 112, qPiso: 0.62, qAmp: 0.33, label: 'anchor=112 qF=0.62+0.33q' },
  { anchor: 115, qPiso: 0.65, qAmp: 0.30, label: 'anchor=115 qF=0.65+0.30q' },
  { anchor: 120, qPiso: 0.68, qAmp: 0.27, label: 'anchor=120 qF=0.68+0.27q' },
];

candidates.forEach(c => {
  const r = estNova(ORIG, Q, DPI, 1.0, c.anchor, c.qPiso, c.qAmp);
  const r12 = estNova(ORIG, Q, DPI, SF, c.anchor, c.qPiso, c.qAmp);
  const qF100 = c.qPiso + c.qAmp;
  const qF20  = c.qPiso + 0.20*c.qAmp;
  // range visual q=20..100 com dpi=100, sf=1.0
  const lo = estNova(ORIG, 20, 100, 1.0, c.anchor, c.qPiso, c.qAmp);
  const hi = estNova(ORIG, 100, 100, 1.0, c.anchor, c.qPiso, c.qAmp);
  const hitTarget = r >= 3.9*1024 && r <= 4.2*1024;
  console.log(
    `[${hitTarget?'✓':'✗'}] ${c.label.padEnd(32)} | sf=1.0→${(r/1024).toFixed(2)}MB  sf=1.2→${(r12/1024).toFixed(2)}MB  range_q=[${(lo/1024).toFixed(2)},${(hi/1024).toFixed(2)}]MB  qF(100)=${qF100.toFixed(2)}`
  );
});

// Melhor candidato: anchor=110, qPiso=0.65, qAmp=0.30
// qF(100)=0.95 (mantém consistência visual), qF(20)=0.71, qF(77)=0.881
const BEST = { anchor: 110, qPiso: 0.65, qAmp: 0.30 };
console.log(L);
console.log('MELHOR CANDIDATO — sweep completo de quality e DPI');
console.log(L);
console.log(`anchor=${BEST.anchor} | qF = ${BEST.qPiso} + (q/100)*${BEST.qAmp}`);
console.log(`qF(20)=${(BEST.qPiso+0.20*BEST.qAmp).toFixed(3)}  qF(77)=${(BEST.qPiso+0.77*BEST.qAmp).toFixed(3)}  qF(80)=${(BEST.qPiso+0.80*BEST.qAmp).toFixed(3)}  qF(100)=${(BEST.qPiso+BEST.qAmp).toFixed(3)}`);
console.log('quality sweep | dpi=100, sf=1.0:');
[20,40,60,77,80,100].forEach(q => {
  const r = estNova(ORIG,q,100,1.0,BEST.anchor,BEST.qPiso,BEST.qAmp);
  const pct = (1 - r/ORIG)*100;
  console.log(`  q=${String(q).padStart(3)} → ${(r/1024).toFixed(2)} MB  (${pct.toFixed(1)}% redução)`);
});
console.log('DPI sweep | quality=77, sf=1.0:');
[50,72,100,110,150,200,300].forEach(d => {
  const r = estNova(ORIG,77,d,1.0,BEST.anchor,BEST.qPiso,BEST.qAmp);
  const pct = (1 - r/ORIG)*100;
  console.log(`  dpi=${String(d).padStart(3)} → ${(r/1024).toFixed(2)} MB  (${pct.toFixed(1)}% redução)`);
});
