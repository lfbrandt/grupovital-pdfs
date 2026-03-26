// Verificação final do candidato anchor=120, qPiso=0.68, qAmp=0.27
const ORIG = 5.81 * 1024;
const REAL = 4.01 * 1024;
const Q=77, DPI=100, SF=1.2;
const L='─'.repeat(56);

function est(orig,q,dpi,sf,anchor=120,qP=0.68,qA=0.27){
  const sfF=1/Math.pow(sf,.20), qF=qP+(q/100)*qA, dpiF=Math.pow(dpi/anchor,1.3);
  return Math.min(orig,Math.max(10,orig*qF*dpiF*sfF));
}

console.log(L+'\nCANDIDATO FINAL: anchor=120  qF=0.68+(q/100)*0.27\n'+L);
console.log('qF(20)='+(0.68+.20*.27).toFixed(3)+'  qF(77)='+(0.68+.77*.27).toFixed(3)+'  qF(80)='+(0.68+.80*.27).toFixed(3)+'  qF(100)='+(0.68+.27).toFixed(3));
const r_sf1 =(est(ORIG,Q,DPI,1.0)/1024).toFixed(2);
const r_sf12=(est(ORIG,Q,DPI,SF )/1024).toFixed(2);
console.log(`\nPONTO REAL: q=77 dpi=100  sf=1.0→${r_sf1}MB  sf=1.2→${r_sf12}MB  real=3.91MB`);
console.log('Faixa alvo: 3.9–4.2 MB → ['+(est(ORIG,Q,DPI,1.0)>=3.9*1024&&est(ORIG,Q,DPI,1.0)<=4.2*1024?'DENTRO':'FORA')+']');

console.log(L+'\nQUALITY sweep | dpi=100 sf=1.0\n'+L);
[20,40,60,77,80,100].forEach(q=>{
  const r=est(ORIG,q,100,1.0);
  const pct=((1-r/ORIG)*100).toFixed(1);
  const ri=(r/1024).toFixed(2);
  console.log(`  q=${String(q).padStart(3)} → ${ri} MB  (${pct}% redução)  qF=${(0.68+q/100*.27).toFixed(3)}`);
});

console.log(L+'\nDPI sweep | quality=77 sf=1.0\n'+L);
[50,72,100,120,150,200,300].forEach(d=>{
  const r=est(ORIG,77,d,1.0);
  const pct=((1-r/ORIG)*100).toFixed(1);
  console.log(`  dpi=${String(d).padStart(3)} → ${(r/1024).toFixed(2)} MB  (${pct}% redução)`);
});

console.log(L+'\nSF sweep | quality=77 dpi=100\n'+L);
[1.0,1.1,1.2,1.5,2.0].forEach(sf=>{
  const r=est(ORIG,77,100,sf);
  console.log(`  sf=${sf.toFixed(1)} → ${(r/1024).toFixed(2)} MB  (${((1-r/ORIG)*100).toFixed(1)}% redução)`);
});

// Sanidade: com q altíssima e dpi altíssimo não deve ultrapassar orig
const max=est(ORIG,100,300,0.5);
console.log(L);
console.log(`Sanidade: q=100 dpi=300 sf=0.5 → ${(max/1024).toFixed(2)} MB  (esperado ≤ ${(ORIG/1024).toFixed(2)} MB)`);
