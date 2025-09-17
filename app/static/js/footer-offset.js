(function () {
  'use strict';
  function px(n){ return `${Math.max(0, Math.round(n || 0))}px`; }
  function measure(){
    const root=document.documentElement;
    const header=document.querySelector('header');
    const footer=document.querySelector('footer');
    const h = header ? header.getBoundingClientRect().height : 72;
    const f = footer ? footer.getBoundingClientRect().height : 56;
    root.style.setProperty('--header-h', px(h));
    root.style.setProperty('--footer-h', px(f));
  }
  const ro = 'ResizeObserver' in window ? new ResizeObserver(measure) : null;
  function init(){
    measure();
    const header=document.querySelector('header');
    const footer=document.querySelector('footer');
    if(ro){ header && ro.observe(header); footer && ro.observe(footer); }
  }
  if(document.readyState==='loading'){ document.addEventListener('DOMContentLoaded', init, {once:true}); } else { init(); }
  window.addEventListener('resize', measure, {passive:true});
  if(document.fonts && document.fonts.ready){ document.fonts.ready.then(measure).catch(()=>{}); }
})();