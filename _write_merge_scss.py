# -*- coding: utf-8 -*-
content = """\
// filepath: app/static/scss/pages/_merge.scss
/* ==========================================================================
   /merge — layout full-width sem sidebar visível
   ========================================================================== */

:root {
  --layout-gap: clamp(8px, 1.2vw, 16px);

  /* ── Fonte de verdade única para tokens de thumb (lida por _merge-thumbs.scss) ── */
  --gv-thumb-w: 220px;
  --gv-thumb-h: 220px;
  --gv-thumb-gap: 14px;
}

#merge-page { width: 100%; }

/* Shell — centralizado com max-width confortável */
#merge-page .merge-main {
  max-width: 1400px;
  margin: 0 auto;
  box-sizing: border-box;
  padding: 16px clamp(12px, 2vw, 28px);
  padding-bottom: calc(var(--footer-h, 56px) + 24px + env(safe-area-inset-bottom));
  min-height: calc(100vh - var(--header-h, 64px) - var(--footer-h, 56px));
}

/* Remove limites herdados de .container/.card */
#merge-page .container,
#merge-page .card,
#merge-page > .container,
#merge-page main > .container {
  max-width: none !important;
  width: 100% !important;
  padding: 0;
  margin: 0;
}

/* Layout: coluna única — sem sidebar visível */
#merge-page .merge-layout {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Sidebar oculta permanentemente (presente apenas para o JS) */
#merge-page .merge-sidebar--ghost,
#merge-page #sidebar.tool__sidebar {
  display: none !important;
  visibility: hidden !important;
  pointer-events: none !important;
}

/* ── Workspace (upload + ações + grid) ─────────────────────────────── */
.workspace {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-height: 0;
}

/* Moldura: upload + botões lado a lado (padrão /compress) */
.workspace-header {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) max-content;
  align-items: center;
  gap: 16px;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--radius, 12px);
  box-shadow: var(--shadow-1, 0 4px 16px rgba(0,0,0,.08));
  padding: 16px 20px;
}

.actions.actions--inline {
  display: inline-flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}

/* Grid de miniaturas — moldura visual */
#preview-merge {
  background: var(--bg-subtle, #f1f5f9);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--radius, 12px);
  min-height: 120px;
}

/* ── Aviso informativo elegante ─────────────────────────────────────── */
.merge-info-callout {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  background: color-mix(in srgb, var(--primary, #268451) 8%, var(--surface, #fff));
  border: 1px solid color-mix(in srgb, var(--primary, #268451) 22%, transparent);
  border-radius: var(--radius, 12px);
  padding: 12px 16px;
  font-size: .85rem;
  color: var(--text, #1f2428);
  line-height: 1.5;

  &__icon {
    width: 18px; height: 18px;
    flex-shrink: 0;
    margin-top: 1px;
    color: var(--primary, #268451);
  }

  strong { color: var(--primary-strong, #1f473f); }
}

@supports not (background: color-mix(in srgb, #000 8%, #fff)) {
  .merge-info-callout {
    background: #edf7f2;
    border-color: #c1e8c2;
  }
}

.merge-info-callout--steps {
  flex-direction: column;
  gap: 6px;
}

/* ── Dicas de uso ───────────────────────────────────────────────────── */
.mini-steps {
  margin: 0;
  padding: 0 0 0 1.1rem;
  display: flex;
  flex-direction: column;
  gap: 4px;

  li { font-size: .84rem; }
  strong { color: var(--primary-strong, #1f473f); }
}

.more--inline {
  font-size: .82rem;
  color: var(--muted, #6b7280);
  summary { cursor: pointer; user-select: none; }
}

/* ── Responsivo ─────────────────────────────────────────────────────── */
@media (max-width: 840px) {
  .workspace-header {
    grid-template-columns: 1fr;
    align-items: stretch;
  }
  .actions.actions--inline { justify-content: flex-start; }
}
"""

with open(
    r'c:\Users\Caio-PC\Desktop\Projeto Ma Alpha\app\static\scss\pages\_merge.scss',
    'w', encoding='utf-8'
) as f:
    f.write(content)
print('_merge.scss reescrito com sucesso.')
