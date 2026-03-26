const O=500,L='-'.repeat(50);
const est=(q,d,sf=1)=>{const sfF=1/Math.pow(sf,.2),qF=.40+(q/100)*.55,dpiF=Math.pow(d/150,1.3),raw=O*qF*dpiF*sfF;return Math.min(O,Math.max(10,raw)).toFixed(1);};
console.log(L+'\nNOVA formula — quality sweep | dpi=100\n'+L);
[20,40,60,80,100].forEach(q=>console.log('  q='+q+' -> '+est(q,100)+' KB'));
console.log(L+'\nNOVA formula — DPI sweep | quality=80\n'+L);
[50,72,100,150,200,300].forEach(d=>console.log('  dpi='+d+' -> '+est(80,d)+' KB'));
