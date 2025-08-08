// Requer que pdf.min.js e pdf.worker.min.js já estejam carregados (você já carrega no base.html)

export async function renderPdfBlob(blob, container, opts = {}) {
  const buf = await blob.arrayBuffer();
  return renderPdfArrayBuffer(buf, container, opts);
}

export async function renderPdfArrayBuffer(arrayBuffer, container, opts = {}) {
  const pdfjsLib = window.pdfjsLib;
  if (!pdfjsLib) {
    throw new Error("PDF.js não está carregado. Confira pdf.min.js e pdf.worker.min.js.");
  }

  const maxPages = opts.maxPages ?? 3;
  const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise;

  // limpa container
  container.innerHTML = "";

  const dpr = window.devicePixelRatio || 1;
  const widthTarget = container.clientWidth > 0 ? container.clientWidth : 320;

  const pagesToRender = Math.min(pdf.numPages, maxPages);
  for (let n = 1; n <= pagesToRender; n++) {
    const page = await pdf.getPage(n);
    const viewport0 = page.getViewport({ scale: 1 });
    const scale = widthTarget / viewport0.width;

    const viewport = page.getViewport({ scale });

    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");

    canvas.width  = Math.ceil(viewport.width * dpr);
    canvas.height = Math.ceil(viewport.height * dpr);
    canvas.style.width  = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    // fundo branco para “simular” papel
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    await page.render({ canvasContext: ctx, viewport }).promise;

    const wrap = document.createElement("div");
    wrap.className = "pdf-preview-page";
    const badge = document.createElement("span");
    badge.className = "pdf-preview-badge";
    badge.textContent = `Pg ${n}`;

    wrap.appendChild(badge);
    wrap.appendChild(canvas);
    container.appendChild(wrap);
  }
}