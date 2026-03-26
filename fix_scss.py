import pathlib

BASE = pathlib.Path(r'c:\Users\Caio-PC\Desktop\Projeto Ma Alpha')

# ── 1. _merge-thumbs.scss ─────────────────────────────────────────────────
mt = BASE / 'app/static/scss/components/_merge-thumbs.scss'
txt = mt.read_text(encoding='utf-8')

START = "content: '\u2295' !important;"   # ⊕
END   = "font-weight: 800; font-size: 16px; line-height: 1;"

i_start = txt.find(START)
i_end   = txt.find(END, i_start)

if i_start == -1 or i_end == -1:
    print("ERRO: marcadores nao encontrados em _merge-thumbs.scss")
    print("  i_start =", i_start, "  i_end =", i_end)
else:
    # Substitui tudo entre o fim do START e o inicio do END por uma newline
    # Resultado: "content: '⊕' !important;\n        font-weight: ..."
    fixed = txt[:i_start + len(START)] + '\n        ' + txt[i_end:]
    mt.write_text(fixed, encoding='utf-8')
    removed = i_end - (i_start + len(START))
    print(f"_merge-thumbs.scss: removidos {removed} chars de prosa. OK")

# ── 2. _page_editor.scss ─────────────────────────────────────────────────
pe = BASE / 'app/static/scss/components/_page_editor.scss'
txt2 = pe.read_text(encoding='utf-8')

# O bloco problemático: declarações após @supports dentro de .pe-modal
# Queremos mover background/color/border/etc. para ANTES do @supports
OLD = (
    "  @supports (height: 1svh) {\n"
    "    height: calc(100svh - var(--gv-header-h) - var(--pe-modal-gap));\n"
    "  }\n"
    "\n"
    "  background: var(--pe-surface);\n"
    "  color: var(--pe-text);\n"
    "  border: 1px solid var(--pe-bd);\n"
    "  border-radius: 16px;\n"
    "  box-shadow: 0 30px 60px rgba(0, 0, 0, .45);\n"
    "  display: grid;\n"
    "  grid-template-rows: auto 1fr auto;\n"
    "  overflow: hidden;\n"
    "  position: relative;\n"
    "  z-index: var(--z-modal-ui);\n"
)

NEW = (
    "  background: var(--pe-surface);\n"
    "  color: var(--pe-text);\n"
    "  border: 1px solid var(--pe-bd);\n"
    "  border-radius: 16px;\n"
    "  box-shadow: 0 30px 60px rgba(0, 0, 0, .45);\n"
    "  display: grid;\n"
    "  grid-template-rows: auto 1fr auto;\n"
    "  overflow: hidden;\n"
    "  position: relative;\n"
    "  z-index: var(--z-modal-ui);\n"
    "\n"
    "  @supports (height: 1svh) {\n"
    "    height: calc(100svh - var(--gv-header-h) - var(--pe-modal-gap));\n"
    "  }\n"
)

if OLD in txt2:
    fixed2 = txt2.replace(OLD, NEW, 1)
    pe.write_text(fixed2, encoding='utf-8')
    print("_page_editor.scss: mixed-decls corrigido. OK")
else:
    print("AVISO: padrao exato nao encontrado em _page_editor.scss — verificando variantes...")
    # Tenta localizar a linha do @supports para diagnóstico
    for i, line in enumerate(txt2.splitlines(), 1):
        if '@supports' in line or 'background: var(--pe-surface)' in line:
            print(f"  linha {i}: {line!r}")

print("Concluido.")
