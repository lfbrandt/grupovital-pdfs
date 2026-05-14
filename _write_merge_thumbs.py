# -*- coding: utf-8 -*-
"""Reescreve _merge-thumbs.scss com o novo visual alinhado ao /compress."""

PATH = r"app/static/scss/components/_merge-thumbs.scss"

SCSS = """\
/* ==========================================================================
   Grid de miniaturas do /merge
   Acabamento visual inspirado no /compress (pac-card-actions / pac-thumb / pac-info)
   Le tokens de _merge.scss — nao redefine --gv-thumb-* aqui.
   ========================================================================== */

// ── Paleta por origem (A..Z) → --src-color injetado no card ────────────────
$src-colors: (
  "A": #00995d, "B": #3213f9, "C": #0032bc, "D": #dd0000, "E": #475569, "F": #8e5c2e,
  "G": #16a085, "H": #2c3e50, "I": #c0392b, "J": #3f51b5, "K": #8bc34a, "L": #00bcd4,
  "M": #e67e22, "N": #9b59b6, "O": #f39c12, "P": #34495e, "Q": #1abc9c, "R": #e74c3c,
  "S": #27ae60, "T": #2980b9, "U": #d35400, "V": #7f8c8d, "W": #2ecc71, "X": #3498db,
  "Y": #f1c40f, "Z": #e84393
);
@each $letter, $color in $src-colors {
  #preview-merge .page-wrapper.page-thumb[data-source="#{$letter}"] {
    --src-color: #{$color};
  }
}

// ── Grid ────────────────────────────────────────────────────────────────────
#preview-merge {
  // Aliases locais — leem tokens de _merge.scss sem redefinir
  --thumb-w:   var(--gv-thumb-w,   210px);
  --thumb-h:   var(--gv-thumb-h,   210px);
  --thumb-gap: var(--gv-thumb-gap,  14px);

  // Alturas internas (igual ao compress: faixa no topo, rodape embaixo)
  --strip-h:  36px;   // faixa de acoes no topo  (~pac-card-actions)
  --footer-h: 28px;   // rodape com nome do arquivo (~pac-info)

  width: 100%;
  overflow: auto;
  padding: var(--thumb-gap);

  display: grid;
  grid-template-columns: repeat(auto-fill, var(--thumb-w));
  grid-auto-rows: var(--thumb-h);
  gap: var(--thumb-gap);
  align-content: start;
  justify-content: start;
  grid-auto-flow: row dense;
  box-sizing: border-box;
}

// ── Card base ────────────────────────────────────────────────────────────────
// Estrutura: [faixa de cor] [miniatura] [rodape com nome]
// Inspirado em .page-analysis-card do /compress
#preview-merge .page-wrapper.page-thumb {
  position: relative;

  width:      var(--thumb-w) !important;
  height:     var(--thumb-h) !important;
  min-width:  var(--thumb-w) !important;
  max-width:  var(--thumb-w) !important;
  min-height: var(--thumb-h) !important;
  max-height: var(--thumb-h) !important;
  aspect-ratio: auto !important;

  background:    var(--surface, #fff);
  border:        1px solid var(--border, #e2e8f0);
  border-radius: var(--radius, 12px);
  box-shadow:    var(--shadow-1, 0 1px 3px rgba(0,0,0,.06));
  overflow:      hidden;
  user-select:   none;
  z-index:       0;
  contain:       layout paint;
  cursor:        grab;

  transition: box-shadow .15s ease, border-color .15s ease, transform .13s ease;

  &:hover {
    box-shadow:   var(--shadow-2, 0 4px 16px rgba(0,0,0,.10));
    border-color: color-mix(in srgb, var(--primary, #00995d) 40%, transparent);
    transform:    translateY(-1px);
  }
  &:active    { cursor: grabbing; }
  &:focus-visible {
    outline:        2px solid var(--primary, #00995d);
    outline-offset: 2px;
  }

  &.is-dragging    { opacity: .5; box-shadow: none; cursor: grabbing; }
  &.is-drop-target { outline: 2px solid var(--primary, #00995d); outline-offset: 2px; }
  &.selected {
    border-color: var(--primary, #00995d);
    box-shadow:
      0 0 0 2px color-mix(in srgb, var(--primary, #00995d) 28%, transparent),
      var(--shadow-1, 0 1px 3px rgba(0,0,0,.06));
  }

  &[hidden] { display: none !important; }

  // ── Faixa de cor no topo (cor solida por origem — como .pac-card-actions) ─
  &[data-source]::before {
    content:  "";
    position: absolute;
    inset:    0 0 auto 0;
    height:   var(--strip-h);
    background: var(--src-color, #00995d);
    border-radius: var(--radius, 12px) var(--radius, 12px) 0 0;
    z-index: 1;
  }

  // ── Area da miniatura (entre faixa e rodape) ──────────────────────────────
  // Inspirado em .pac-thumb: flex centering, fundo sutil, sem corte
  .thumb-frame {
    position:        absolute;
    inset:           var(--strip-h) 0 var(--footer-h) 0;
    display:         flex;
    align-items:     center;
    justify-content: center;
    overflow:        hidden;
    background:      var(--bg-subtle, #f1f5f9);
    contain:         paint;
  }

  // Fallback: sem caption
  &:not(.has-caption) .thumb-frame {
    inset-bottom:  0;
    border-radius: 0 0 calc(var(--radius, 12px) - 1px) calc(var(--radius, 12px) - 1px);
  }

  // Midia centralizada — sem transform que cause blur
  .thumb-media {
    position:            absolute;
    left:                50%;
    top:                 50%;
    transform:           translate(-50%, -50%);
    transform-origin:    50% 50%;
    backface-visibility: hidden;
    image-rendering:     auto;
    transition:          transform .15s ease;
  }

  .thumb-frame > canvas,
  .thumb-frame > img,
  .thumb-media > canvas,
  .thumb-media > img {
    display:    block;
    width:      100%;
    height:     auto;
    max-height: 100%;
    background: #fff;
    margin:     0;
    border:     0;
    user-select:       none;
    -webkit-user-drag: none;
  }

  // Anti-blur para rotacao 90deg/270deg
  @supports (filter: blur(0.001px)) {
    .thumb-media[style*="rotate(90deg)"],
    .thumb-media[style*="rotate(270deg)"] { filter: blur(0.001px); }
  }

  // ── Badges na faixa (letra de origem + numero de pagina) ──────────────────
  .source-badge {
    position:       absolute;
    top:            calc((var(--strip-h) - 20px) / 2);
    left:           .5rem;
    z-index:        4;
    background:     rgba(0,0,0,.25);
    color:          #fff;
    font-weight:    800;
    font-size:      .75rem;
    line-height:    1;
    padding:        3px 7px;
    border-radius:  5px;
    pointer-events: none;
    text-shadow:    0 1px 2px rgba(0,0,0,.5);
    letter-spacing: .03em;
  }

  .page-badge {
    position:       absolute;
    top:            calc((var(--strip-h) - 20px) / 2);
    left:           calc(.5rem + 30px + .3rem);
    z-index:        4;
    background:     rgba(255,255,255,.18);
    color:          #fff;
    font-weight:    600;
    font-size:      .7rem;
    line-height:    1;
    padding:        3px 6px;
    border-radius:  4px;
    pointer-events: none;
    text-shadow:    0 1px 2px rgba(0,0,0,.35);
  }

  // ── Botoes de acao na faixa — igual a .pac-btn-toggle / .pac-btn-rotate ───
  // 1.8rem x 1.8rem, border-radius: 6px, background: rgba(fff,.15)
  .file-controls {
    position:        absolute;
    top:             calc((var(--strip-h) - 29px) / 2);
    right:           .4rem;
    display:         flex;
    gap:             .25rem;
    z-index:         4;
    pointer-events:  auto;

    button {
      display:         flex;
      align-items:     center;
      justify-content: center;
      width:           1.8rem;
      height:          1.8rem;
      border:          none;
      border-radius:   6px;
      background:      rgba(255,255,255,.15);
      color:           #fff;
      font-size:       14px;
      line-height:     1;
      cursor:          pointer;
      flex-shrink:     0;
      transition:      background .15s ease, transform .08s ease;

      svg { width: 1rem; height: 1rem; pointer-events: none; }

      &:hover  { background: rgba(255,255,255,.28); }
      &:active { transform: scale(.92); }
    }

    // Botao compacto (expande/colapsa arquivo) — sempre visivel, vem primeiro
    button.compact-toggle {
      order:           -1;
      position:        relative;
      z-index:         5;
      display:         flex !important;
      align-items:     center !important;
      justify-content: center !important;
      width:           1.8rem !important;
      height:          1.8rem !important;
      border:          none !important;
      border-radius:   6px !important;
      background:      rgba(255,255,255,.15) !important;
      color:           #fff !important;
      font-size:       0 !important;
      cursor:          pointer !important;
      opacity:         1 !important;
      visibility:      visible !important;
      transition:      background .15s ease, transform .08s ease !important;

      &::before {
        content:     "\\2295";  // ⊕
        font-size:   14px;
        font-weight: 800;
        line-height: 1;
      }
      &[aria-pressed="true"]::before { content: "\\2296"; }  // ⊖

      &:hover  { background: rgba(255,255,255,.28) !important; }
      &:active { transform: scale(.92) !important; }
    }
  }

  // ── Rodape com nome do arquivo (~pac-info) ────────────────────────────────
  // Faixa solida na base — nao pill flutuante
  .thumb-caption {
    position:   absolute;
    bottom:     0;
    left:       0;
    right:      0;
    height:     var(--footer-h);
    z-index:    2;
    pointer-events: none;

    display:        flex;
    align-items:    center;
    padding:        0 .6rem;
    background:     var(--surface, #fff);
    border-top:     1px solid var(--border, #e2e8f0);

    font-size:      .7rem;
    font-weight:    600;
    color:          var(--text, #1f2428);
    line-height:    1.2;
    white-space:    nowrap;
    overflow:       hidden;
    text-overflow:  ellipsis;
  }
}

// ── Dark mode ────────────────────────────────────────────────────────────────
:root[data-theme="dark"] #preview-merge .page-wrapper.page-thumb {
  .thumb-frame   { background: var(--bg-subtle, #1e2228); }
  .thumb-caption {
    background:   var(--surface, #161a1f);
    border-color: var(--border, rgba(255,255,255,.08));
    color:        var(--text, #dde1e8);
  }
}

// ── DnD feedback global ──────────────────────────────────────────────────────
.page-wrapper.is-dragging {
  opacity:    .5;
  box-shadow: none;
  cursor:     grabbing;
}
.page-wrapper.is-drop-target,
.file-list .file-item.drop-highlight {
  outline:        2px solid var(--primary, #00995d);
  outline-offset: 2px;
  border-radius:  var(--radius, 12px);
}
.file-list .file-item.is-dragging { opacity: .8; }
.dnd-no-select,
.dnd-no-select * { user-select: none !important; }
"""

with open(PATH, "w", encoding="utf-8") as f:
    f.write(SCSS)

print("OK — arquivo reescrito com", len(SCSS), "bytes")
