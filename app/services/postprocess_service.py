import os
from PyPDF2 import PdfReader, PdfWriter


def aplicar_modificacoes(pdf_path, modificacoes):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        mod = modificacoes[i] if i < len(modificacoes) else {}
        if 'rotacao' in mod:
            page.rotate(mod['rotacao'])
        if 'crop' in mod:
            t, r, b, l = mod['crop'].values()
            page.mediabox.upper_right = (
                page.mediabox.upper_right[0] - r,
                page.mediabox.upper_right[1] - t
            )
            page.mediabox.lower_left = (
                page.mediabox.lower_left[0] + l,
                page.mediabox.lower_left[1] + b
            )
        writer.add_page(page)

    out = pdf_path.replace('.pdf', '_mod.pdf')
    with open(out, 'wb') as f:
        writer.write(f)
    os.remove(pdf_path)
    return out

