// Verifica comportamento do DPI acima da âncora com CLAMP
// e confirma se o slider de DPI ainda tem range visual útil
const ORIG = 5.81 * 1024;
const L = '─'.repeat(56);

function est(orig,q,dpi,sf,anchor=120,qP=0.68,qA=0.27){
  const sfF=1/Math.pow(sf,.20), qF=qP+(q/100)*qA, dpiF=Math.pow(dpi/anchor,1.3);
  return Math.min(orig,Math.max(10,orig*qF*dpiF*sfF));
}

// O slider de DPI vai de 50 a 300. O "travamento" acima da âncora é CORRETO:
// DPI alto → menos compressão → arquivo maior, mas nunca maior que o original.
// O clamp Math.min(orig,...) garante que nunca infla. Isso é o comportamento esperado.
// A questão é: o usuário consegue VER variação suficiente ao mover o DPI?

console.log(L+'\nDPI sweep completo | quality=80 sf=1.0 — visibilidade do slider\n'+L);
const dpis = [50,60,70,80,90,100,110,120,130,150,200,300];
dpis.forEach(d=>{
  const r=est(ORIG,80,d,1.0);
  const pct=((1-r/ORIG)*100).toFixed(1);
  const bar='█'.repeat(Math.round((1-r/ORIG)*30));
  console.log(`  dpi=${String(d).padStart(3)} → ${(r/1024).toFixed(2)} MB  redução=${pct.padStart(5)}%  ${bar}`);
});

// Comparação: fórmula antiga vs nova para um PDF de 2 MB (menor, mais comum)
const O2 = 2*1024;
console.log(L+'\nDPI sweep | orig=2MB quality=80 sf=1.0\n'+L);
dpis.forEach(d=>{
  const r=est(O2,80,d,1.0);
  const pct=((1-r/O2)*100).toFixed(1);
  console.log(`  dpi=${String(d).padStart(3)} → ${(r/1024).toFixed(2)} MB  (${pct}% redução)`);
});

// Quality sweep em PDF menor
console.log(L+'\nQUALITY sweep | orig=2MB dpi=100 sf=1.0\n'+L);
[20,40,60,77,80,100].forEach(q=>{
  const r=est(O2,q,100,1.0);
  const pct=((1-r/O2)*100).toFixed(1);
  console.log(`  q=${String(q).padStart(3)} → ${(r/1024).toFixed(2)} MB  (${pct}% redução)`);
});
