/* global pdfjsLib */

(() => {
  const prefix = "organize";
  const $file = document.getElementById(`input-${prefix}`);
  const $grid = document.getElementById(`grid-${prefix}`);
  const $btnExport = document.getElementById(`btn-export-${prefix}`);
  const $spinner = document.getElementById(`spinner-${prefix}`);
  const $alert = document.getElementById(`alert-${prefix}`);

  // ===== CSP-safe CSS (sem inline) =====
  function getNonce() {
    const meta = document.querySelector('meta[name="csp-nonce"]');
    if (meta?.content) return meta.content;
    const s = document.querySelector('script[nonce]');
    return s?.nonce || s?.getAttribute?.('nonce') || '';
  }
  function ensureStyles() {
    if (document.getElementById('organize-style')) return;
    const styleEl = document.createElement('style');
    styleEl.id = 'organize-style';
    const nonce = getNonce();
    if (nonce) styleEl.setAttribute('nonce', nonce);
    styleEl.textContent = `
      /* Rotação visual por classe (evita inline style) */
      .thumb.rot-0   .thumb__canvas { transform: rotate(0deg); }
      .thumb.rot-90  .thumb__canvas { transform: rotate(90deg); }
      .thumb.rot-180 .thumb__canvas { transform: rotate(180deg); }
      .thumb.rot-270 .thumb__canvas { transform: rotate(270deg); }
      /* Estado de DnD */
      .thumb.dragging { opacity: .9; }
      /* Thumb básica */
      .thumb { list-style: none; }
      .thumb.selected { outline: 2px solid var(--brand, #00995d); }
      .thumb.excluded { opacity: .45; }
      .thumb__actions { display:flex; gap:6px; margin-bottom:6px; }
      .thumb__btn { border:1px solid rgba(0,0,0,.15); background:rgba(255,255,255,.85); padding:2px 6px; border-radius:6px; }
      .thumb-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap:12px; }
      .thumb__canvas { display:block; width:100%; height:auto; background:#fff; border-radius:6px; }
    `;
    document.head.appendChild(styleEl);
  }
  ensureStyles();

  // CSRF do meta
  const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

  // pdf.js worker (se necessário, já está carregado nos <script> do template)
  if (pdfjsLib?.GlobalWorkerOptions) {
    // se você usar pdf.js local, pode setar:
    // pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/pdfjs/pdf.worker.min.js";
  }

  let currentFile = null;
  let pageCount = 0;

  function showAlert(msg, type = "error") {
    $alert.textContent = msg;
    $alert.hidden = false;
    $alert.className = `alert alert--${type}`;
  }

  function hideAlert() {
    $alert.hidden = true;
  }

  function setBusy(busy) {
    $spinner.setAttribute("aria-hidden", String(!busy));
    $spinner.classList.toggle("is-busy", !!busy);
    $btnExport.disabled = busy || !$grid.querySelector(".thumb.selected");
  }

  function enableExportIfAnySelected() {
    $btnExport.disabled = !$grid.querySelector(".thumb.selected");
  }

  // util: aplica classe de rotação (0/90/180/270)
  function applyRotationClass(li, deg) {
    li.classList.remove("rot-0", "rot-90", "rot-180", "rot-270");
    li.classList.add(`rot-${deg}`);
  }

  // Renderiza miniaturas
  async function renderGrid(file) {
    hideAlert();
    $grid.innerHTML = "";
    pageCount = 0;
    currentFile = file;
    $btnExport.disabled = true;

    const url = URL.createObjectURL(file);
    const pdf = await pdfjsLib.getDocument({ url }).promise;
    pageCount = pdf.numPages;

    for (let i = 1; i <= pageCount; i++) {
      const li = document.createElement("li");
      li.className = "thumb rot-0";
      li.tabIndex = 0;
      li.draggable = true;
      li.dataset.page = String(i);        // índice 1-based original
      li.dataset.rotation = "0";          // rotação atual daquela página
      li.dataset.selected = "false";

      // Header com ações
      const actions = document.createElement("div");
      actions.className = "thumb__actions";

      const rotateBtn = document.createElement("button");
      rotateBtn.type = "button";
      rotateBtn.className = "thumb__btn thumb__btn--rotate";
      rotateBtn.title = "Girar 90°";
      rotateBtn.textContent = "⟳";

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "thumb__btn thumb__btn--remove";
      removeBtn.title = "Excluir do resultado";
      removeBtn.textContent = "×";

      actions.appendChild(rotateBtn);
      actions.appendChild(removeBtn);

      const canvas = document.createElement("canvas");
      canvas.className = "thumb__canvas";

      li.appendChild(actions);
      li.appendChild(canvas);
      $grid.appendChild(li);

      // Eventos
      li.addEventListener("click", (ev) => {
        if (ev.target.closest(".thumb__btn")) return; // ignora clicks nos botões
        const selected = li.dataset.selected === "true";
        li.dataset.selected = String(!selected);
        li.classList.toggle("selected", !selected);
        enableExportIfAnySelected();
      });

      rotateBtn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        const r = (parseInt(li.dataset.rotation, 10) + 90) % 360;
        li.dataset.rotation = String(r);
        applyRotationClass(li, r);
        // re-render opcional para ajustar bounding box (mantemos leve: só gira visualmente)
      });

      removeBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        li.dataset.selected = "false";
        li.classList.remove("selected");
        li.classList.add("excluded");
        enableExportIfAnySelected();
      });

      // DnD
      li.addEventListener("dragstart", (e) => {
        try { e.dataTransfer.setData("text/plain", li.dataset.page); } catch {}
        li.classList.add("dragging");
      });
      li.addEventListener("dragend", () => li.classList.remove("dragging"));
      li.addEventListener("dragover", (e) => e.preventDefault());
      li.addEventListener("drop", (e) => {
        e.preventDefault();
        const dragging = $grid.querySelector(".dragging");
        if (!dragging || dragging === li) return;
        $grid.insertBefore(dragging, li);
      });

      // Render canvas
      const page = await pdf.getPage(i);
      const viewport = page.getViewport({ scale: 0.2 }); // leve
      const ctx = canvas.getContext("2d", { alpha: false });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      await page.render({ canvasContext: ctx, viewport }).promise;
    }
  }

  // Exporta: coleta seleção na ordem atual da grid
  async function exportSelection() {
    hideAlert();
    if (!currentFile) return showAlert("Selecione um PDF primeiro.");

    const selectedNodes = Array.from($grid.querySelectorAll(".thumb.selected"))
      .filter(li => !li.classList.contains("excluded"));

    if (selectedNodes.length === 0) return showAlert("Nenhuma página selecionada.");

    // Páginas na ORDEM atual da grid
    const pages = [];
    const rotations = {};
    Array.from($grid.children).forEach((li) => {
      if (!li.classList.contains("selected") || li.classList.contains("excluded")) return;
      const idx1b = parseInt(li.dataset.page, 10);
      pages.push(idx1b);
      const rot = parseInt(li.dataset.rotation, 10) || 0;
      if (rot) rotations[String(idx1b)] = rot;
    });

    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", currentFile, currentFile.name || "input.pdf");
      fd.append("pages", JSON.stringify(pages));
      fd.append("rotations", JSON.stringify(rotations));

      const resp = await fetch("/api/organize", {
        method: "POST",
        headers: { "X-CSRFToken": csrfToken },
        body: fd,
      });

      if (!resp.ok) {
        const msg = await resp.text();
        showAlert(msg || "Falha ao exportar.");
        return;
      }

      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "organizado.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      showAlert("Erro inesperado ao exportar.");
    } finally {
      setBusy(false);
    }
  }

  // Listeners
  if ($file) {
    $file.addEventListener("change", () => {
      const f = $file.files?.[0] || null;
      if (!f) return;
      const isPdf = /application\/pdf/i.test(f.type) || /\.pdf$/i.test(f.name);
      if (!isPdf) return showAlert("Envie um arquivo PDF válido.");
      setBusy(true);
      renderGrid(f).finally(() => setBusy(false));
    });
  }

  if ($btnExport) $btnExport.addEventListener("click", exportSelection);
})();