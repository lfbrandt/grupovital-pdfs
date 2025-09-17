// app/static/js/home-changelog.js
// Preenche os painéis "Patches Recentes" e "Planejamento" a partir de
// /static/meta/updates.json. Fallback: deixa o HTML estático se o fetch falhar.
(() => {
  "use strict";
  const DATA_URL = "/static/meta/updates.json";

  function el(tag, cls, text){
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function renderList(targetSel, items){
    const ul = document.querySelector(targetSel);
    if (!ul || !Array.isArray(items)) return;

    ul.innerHTML = ""; // zera conteúdo antigo (se houver)
    for (const it of items){
      const li = el("li", "item");
      const title = el("div", "title", it.title || "");
      const meta = el("div", "meta");

      if (it.date || it.eta) meta.appendChild(el("span", "date", it.date || it.eta));
      if (it.tag) {
        const tag = el("span", "tag", it.tag);
        meta.appendChild(tag);
      }

      li.appendChild(title);
      li.appendChild(meta);

      if (it.desc) li.appendChild(el("div", "desc", it.desc));
      ul.appendChild(li);
    }
  }

  async function init(){
    try{
      const res = await fetch(DATA_URL, { credentials: "same-origin", headers: { "Accept":"application/json" } });
      if (!res.ok) return;
      const data = await res.json();
      renderList("#patches-list", data?.recent);
      renderList("#roadmap-list", data?.planned);
    }catch(_e){
      // silencioso: mantemos o HTML estático
    }
  }

  if (document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", init, { once:true });
  }else{
    init();
  }
})();