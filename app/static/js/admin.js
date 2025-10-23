// app/static/js/admin.js
(function () {
  const $ = (s) => document.querySelector(s);

  // ===== Refs =====
  const tokenInput = $("#adm-token"), msg = $("#msg"), btn = $("#btn-reload");
  const rangeSel = $("#range"), autoSel = $("#autorf");

  const cardsWrap = $("#summary-cards");
  const donutWrap = $("#status-donut"), legend = $("#status-legend");
  const narrative = $("#chart-narrative"), tooltip = $("#chart-tooltip");
  const chipSuccess = $("#chip-success"), chip4xx = $("#chip-4xx"), chip5xx = $("#chip-5xx"), chipTotal = $("#chip-total");
  const toolsGrid = $("#tools-grid");
  const bVer = $("#badge-version"), bEnv = $("#badge-env"), bBuild = $("#badge-build");
  const trendSection = $("#trend-section"), spark = $("#req-sparkline"), sparkLegend = $("#spark-legend");
  const errSection = $("#errors-section"), errTable = $("#errors-table tbody");

  // Logs (viewer)
  const logsWrap = $("#logs-container"), logsMeta = $("#logs-meta");
  const logsLevel = $("#logs-level"), logsTail = $("#logs-tail"), logsReload = $("#logs-refresh");
  // Logs (envio)
  const logMsgInput = $("#logs-msg"), logLevelAdd = $("#logs-level-add"), logSendBtn = $("#logs-send");

  // ===== Storage keys =====
  const LS_TOKEN = "gv_admin_token", LS_RANGE = "gv_admin_range", LS_AUTORF = "gv_admin_autorf";

  // ===== Utils =====
  const clamp = (v,a,b)=>Math.max(a,Math.min(b,v));
  const pct = (n,d)=> d? (n/d)*100 : 0;
  const fmtPct = v => `${clamp(v,0,100).toFixed(0)}%`;
  const qs = (o)=>{ const p=new URLSearchParams(); Object.entries(o).forEach(([k,v])=>{ if(v!==undefined&&v!==null&&v!=="") p.set(k,String(v));}); return p.toString(); };
  const esc = (s)=> String(s||"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;");

  function getToken(){
    const v = (tokenInput?.value || "").trim();
    if (v) { sessionStorage.setItem("ADMIN_TOKEN", v); localStorage.setItem(LS_TOKEN, v); return v; }
    return sessionStorage.getItem("ADMIN_TOKEN") || localStorage.getItem(LS_TOKEN) || "";
  }

  function animateNumber(el,to){
    const start=Number(el.textContent)||0, end=Number(to)||0, t0=performance.now(), dur=420;
    const step=(t)=>{ const p=Math.min(1,(t-t0)/dur); el.textContent=String(Math.round(start+(end-start)*p)); if(p<1) requestAnimationFrame(step);};
    requestAnimationFrame(step);
  }

  // ===== KPIs =====
  function makeCard(label, value){
    if (!cardsWrap) return;
    const div=document.createElement("div");
    div.className="stat-card";
    div.innerHTML=`<div class="kpi">0</div><div class="label">${label}</div>`;
    cardsWrap.appendChild(div);
    animateNumber(div.querySelector(".kpi"), value);
  }

  // ===== Donut =====
  function drawDonut(segments, successRate){
    if (!donutWrap) return;
    donutWrap.innerHTML="";
    const segs=segments.filter(s=>(s.value||0)>0);
    const total=segs.reduce((a,s)=>a+s.value,0)||1;
    const r=84, c=2*Math.PI*r;
    const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("viewBox","0 0 240 240"); svg.classList.add("donut");

    const base=document.createElementNS(svg.namespaceURI,"circle");
    base.setAttribute("cx","120"); base.setAttribute("cy","120"); base.setAttribute("r",String(r));
    base.setAttribute("fill","transparent"); base.setAttribute("class","donut-base");
    svg.appendChild(base);

    let offset=0;
    segs.forEach((s)=>{
      const len=c*(s.value/total);
      const circle=document.createElementNS(svg.namespaceURI,"circle");
      circle.setAttribute("cx","120"); circle.setAttribute("cy","120"); circle.setAttribute("r",String(r));
      circle.setAttribute("fill","transparent");
      circle.setAttribute("stroke", getComputedStyle(document.documentElement).getPropertyValue(s.cssVar).trim());
      circle.setAttribute("stroke-width","22");
      circle.setAttribute("stroke-dasharray", `${len} ${c-len}`);
      circle.setAttribute("stroke-dashoffset", String(c/4 - offset));
      circle.setAttribute("stroke-linecap","round");
      circle.classList.add("seg");
      circle.addEventListener("mouseenter",()=>{
        if (!tooltip) return;
        const rect=donutWrap.getBoundingClientRect();
        tooltip.style.opacity="1";
        tooltip.textContent=`${s.long}: ${s.value} (${fmtPct((s.value/total)*100)})`;
        tooltip.style.left=(rect.width/2 - tooltip.offsetWidth/2) + "px";
        tooltip.style.top=(rect.height/2 - r - 26) + "px";
      });
      circle.addEventListener("mouseleave",()=>{ if(tooltip) tooltip.style.opacity="0"; });
      svg.appendChild(circle); offset+=len;
    });

    const t1=document.createElementNS(svg.namespaceURI,"text");
    t1.setAttribute("x","120"); t1.setAttribute("y","112");
    t1.setAttribute("text-anchor","middle"); t1.setAttribute("class","donut-center-title"); t1.textContent="Sucesso (2xx)";
    const t2=document.createElementNS(svg.namespaceURI,"text");
    t2.setAttribute("x","120"); t2.setAttribute("y","144");
    t2.setAttribute("text-anchor","middle"); t2.setAttribute("class","donut-center-value"); t2.textContent=fmtPct(successRate);

    svg.appendChild(t1); svg.appendChild(t2); donutWrap.appendChild(svg);
  }

  function renderStatus(status){
    const s2=status["2xx"]||0, s4=status["4xx"]||0, s5=status["5xx"]||0;
    const total=s2+s4+s5; const rate=pct(s2,total||1);

    drawDonut([
      {label:"2xx", long:"Sucesso (2xx)", value:s2, cssVar:"--ok"},
      {label:"4xx", long:"Erros do cliente (4xx)", value:s4, cssVar:"--warn"},
      {label:"5xx", long:"Erros do servidor (5xx)", value:s5, cssVar:"--err"},
    ], rate);

    if (legend){
      legend.innerHTML="";
      [
        {txt:"Sucesso (2xx)", v:s2, var:"--ok"},
        {txt:"Erros do cliente (4xx)", v:s4, var:"--warn"},
        {txt:"Erros do servidor (5xx)", v:s5, var:"--err"},
      ].forEach(({txt,v,var:vr})=>{
        const li=document.createElement("li");
        li.innerHTML=`<span class="dot" style="background:var(${vr})"></span>${txt}: <b>${v}</b> <span class="muted">(${fmtPct(pct(v,total||1))})</span>`;
        legend.appendChild(li);
      });
    }

    if (narrative){
      narrative.textContent = total===0
        ? "Sem dados ainda. Gere algumas requisições para ver o gráfico."
        : `De ${total} requisiç${total===1?"ão":"ões"} consideradas, ${s2} foram sucesso (2xx, ${fmtPct(rate)}), ${s4} retornaram erros do cliente (4xx) e ${s5} erros do servidor (5xx).`;
    }

    if (chipSuccess) chipSuccess.textContent=`Sucesso ${fmtPct(rate)}`;
    if (chip4xx)     chip4xx.textContent=`4xx ${fmtPct(pct(s4,total||1))}`;
    if (chip5xx)     chip5xx.textContent=`5xx ${fmtPct(pct(s5,total||1))}`;
    if (chipTotal)   chipTotal.textContent=`Total ${total}`;

    return {total, s2, s4, s5};
  }

  // ===== Sparkline & Erros =====
  function renderSparkline(series){
    if (!trendSection || !spark){ return; }
    if (!Array.isArray(series)||!series.length){ trendSection.hidden=true; return; }
    trendSection.hidden=false; spark.innerHTML="";
    const W=spark.clientWidth||760, H=120, P=10;
    const max=Math.max(...series.map(d=>d.count),1);
    const xs=(i)=> P+(i*(W-2*P))/Math.max(1,series.length-1);
    const ys=(v)=> H-P-(v/max)*(H-2*P);
    const svg=document.createElementNS("http://www.w3.org/2000/svg","svg");
    svg.setAttribute("viewBox",`0 0 ${W} ${H}`); svg.setAttribute("preserveAspectRatio","none");
    let d=""; series.forEach((p,i)=>{ d+=(i?" L":"M")+xs(i)+" "+ys(p.count); });
    const path=document.createElementNS(svg.namespaceURI,"path");
    path.setAttribute("d",d); path.setAttribute("fill","none"); path.setAttribute("stroke","var(--brand)"); path.setAttribute("stroke-width","2");
    const area=document.createElementNS(svg.namespaceURI,"path");
    area.setAttribute("d",`${d} L ${xs(series.length-1)} ${H-P} L ${xs(0)} ${H-P} Z`); area.setAttribute("fill","rgba(0,153,93,.15)");
    svg.appendChild(area); svg.appendChild(path); spark.appendChild(svg);
    const first=series[0]?.ts||"-", last=series[series.length-1]?.ts||"-";
    if (sparkLegend) sparkLegend.textContent=`${series.length} pontos • pico ${max} req/min • ${first} → ${last}`;
  }

  function renderErrors(items){
    if (!errSection || !errTable) return;
    if (!Array.isArray(items) || !items.length){ errSection.hidden=true; return; }
    errSection.hidden=false; errTable.innerHTML="";
    items.slice(0,5).forEach(e=>{
      const tr=document.createElement("tr");
      tr.innerHTML=`<td>${e.time||"-"}</td><td>${e.path||"-"}</td><td>${e.status||"-"}</td><td>${esc((e.message||"").slice(0,140))}</td>`;
      errTable.appendChild(tr);
    });
  }

  // ===== Stats =====
  async function loadStats(){
    const token=getToken();
    if (!token){ if(msg) msg.textContent="Informe o token."; return; }
    if (rangeSel) localStorage.setItem(LS_RANGE, rangeSel.value);
    if (msg) msg.textContent="Carregando…";
    try{
      const res=await fetch(`/api/admin/stats?${qs({range:rangeSel?.value})}`, {headers:{"X-Admin-Token":token}});
      if(!res.ok) throw new Error(`${res.status}`);
      const data=await res.json();

      if (bVer)   bVer.textContent=`v${data.app?.version||"-"}`;
      if (bEnv)   bEnv.textContent=data.app?.env||"-";
      if (bBuild) bBuild.textContent=data.app?.build||"-";

      const {total, s2, s4, s5}=renderStatus(data.status||{});
      if (cardsWrap){
        cardsWrap.innerHTML="";
        makeCard("Requisições", total);
        makeCard("Sucesso (2xx)", s2);
        makeCard("Erros do cliente (4xx)", s4);
        makeCard("Erros do servidor (5xx)", s5);
      }
      if (toolsGrid){
        toolsGrid.innerHTML="";
        const t=data.tools||{};
        ["compress","merge","split","convert","edit","organize"].forEach(n=>{
          const ok=t[`${n}_ok`]||0, err=t[`${n}_err`]||0;
          const card=document.createElement("div"); card.className="tool-card";
          const tot=Math.max(1,ok+err), rate=Math.round((ok/tot)*100);
          card.innerHTML=`<div class="tool-title">${n[0].toUpperCase()+n.slice(1)}</div>
            <div class="tool-kpis"><div><span class="ok">✓</span> <b>${ok}</b></div><div><span class="err">✗</span> <b>${err}</b></div></div>
            <div class="bar"><span class="okbar" style="width:${rate}%"></span></div><div class="bar-legend">${rate}% sucesso</div>`;
          toolsGrid.appendChild(card);
        });
      }
      renderSparkline(data.timeseries?.requests_per_min || data.timeseries || []);
      renderErrors(data.recent_errors || []);
      if (msg){ msg.textContent="Ok"; setTimeout(()=>{ if(msg) msg.textContent=""; },1400); }

      // Atualiza logs junto (se existir seção)
      if (logsWrap) loadLogs();
    }catch(e){
      if (msg) msg.textContent="Erro "+(e.message||e);
    }
  }

  // ===== Logs: GET =====
  function renderLogLine(item){
    const lvl=(item.level||"").toUpperCase();
    const cls=`log-line level-${lvl}`;
    const where=item.where?` <span class="where">${esc(item.where)}</span>`:"";
    const req=item.req?` <span class="req">${esc(item.req)}</span>`:"";
    const m=item.msg||item.raw||"";
    return `<div class="${cls}"><span class="ts">${esc(item.ts)}</span><span class="level">${lvl}</span>${where}${req}<span class="msg">${esc(m)}</span></div>`;
  }

  async function loadLogs(){
    if (!logsWrap || !logsMeta) return;
    const token=getToken(); if(!token){ logsMeta.textContent="Informe o token para ver os logs."; return; }
    const url=`/api/admin/logs?${qs({tail: logsTail?.value || "400", level: logsLevel?.value || ""})}`;
    logsWrap.setAttribute("aria-busy","true"); logsMeta.textContent="Carregando…";
    try{
      const r=await fetch(url,{headers:{"X-Admin-Token":token}});
      if(!r.ok) throw new Error(`HTTP ${r.status}`);
      const data=await r.json();
      logsMeta.textContent=`${data.count} linhas • arquivo: ${data.file}`;
      logsWrap.innerHTML = Array.isArray(data.items) && data.items.length
        ? data.items.map(renderLogLine).join("")
        : '<div class="log-line">Nenhuma linha.</div>';
      logsWrap.scrollTop=logsWrap.scrollHeight;
    }catch(e){
      logsMeta.textContent=`Falha ao carregar logs: ${e.message || e}`;
    }finally{
      logsWrap.setAttribute("aria-busy","false");
    }
  }

  // ===== Logs: POST =====
  async function sendLog(){
    if (!logMsgInput) return;
    const token=getToken(); if(!token){ alert("Informe o token do admin."); return; }
    const message=logMsgInput.value.trim(); if(!message){ logMsgInput.focus(); return; }
    const level=(logLevelAdd?.value || "INFO").toUpperCase();
    try{
      const r=await fetch("/api/admin/log",{method:"POST", headers:{"Content-Type":"application/json","X-Admin-Token":token}, body:JSON.stringify({level,message})});
      if(!r.ok) throw new Error(`HTTP ${r.status}`);
      logMsgInput.value=""; loadLogs();
    }catch(e){ alert("Erro ao registrar log: "+(e.message||e)); }
  }

  // ===== Auto-refresh =====
  let timer=null;
  function applyAutoRefresh(){
    const ms=parseInt(autoSel?.value||"0",10)||0;
    localStorage.setItem(LS_AUTORF, String(ms)); // <-- corrigido
    if (timer){ clearInterval(timer); timer=null; }
    if (ms>0){ timer=setInterval(loadStats, ms); }
  }

  // ===== Eventos =====
  if (btn)       btn.addEventListener("click", loadStats);
  if (rangeSel)  rangeSel.addEventListener("change", loadStats);
  if (autoSel)   autoSel.addEventListener("change", applyAutoRefresh);

  if (logsReload) logsReload.addEventListener("click", loadLogs);
  if (logsLevel)  logsLevel.addEventListener("change", loadLogs);
  if (logsTail)   logsTail.addEventListener("change", loadLogs);

  if (logSendBtn) logSendBtn.addEventListener("click", sendLog);
  if (logMsgInput) logMsgInput.addEventListener("keydown", (e)=>{ if(e.key==="Enter" && (e.ctrlKey||e.metaKey)) sendLog(); });

  // ===== Restore + boot =====
  const savedTok=localStorage.getItem(LS_TOKEN), savedRange=localStorage.getItem(LS_RANGE), savedAuto=localStorage.getItem(LS_AUTORF);
  if (savedTok && tokenInput) tokenInput.value=savedTok;
  if (savedTok) sessionStorage.setItem("ADMIN_TOKEN", savedTok);
  if (savedRange && rangeSel) rangeSel.value=savedRange;
  if (savedAuto && autoSel)   autoSel.value=savedAuto;
  applyAutoRefresh();

  if (savedTok) loadStats();
  if (savedTok && logsWrap && !btn) loadLogs();
})();