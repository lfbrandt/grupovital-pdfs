import pikepdf

def sanitize_pdf(in_path: str, out_path: str,
                 remove_annotations: bool = True,
                 remove_actions: bool = True,
                 remove_embedded: bool = True) -> None:
    """
    Remove JavaScript, ações (OpenAction/AA), XFA, anotações e arquivos embutidos do PDF.
    Use antes de qualquer processamento no servidor.
    """
    with pikepdf.open(in_path) as pdf:
        root = pdf.root

        # Catálogo: ações automáticas
        if remove_actions:
            for k in ("OpenAction", "AA"):
                if k in root:
                    del root[k]

        # Formulários: XFA/JS/AA
        if "/AcroForm" in root:
            acro = root.AcroForm
            if "/XFA" in acro:
                del acro["/XFA"]
            if "/JS" in acro:
                del acro["/JS"]
            if "/Fields" in acro:
                for f in list(acro["/Fields"]):
                    try:
                        if "/AA" in f:
                            del f["/AA"]
                    except Exception:
                        pass

        # Names: JavaScript
        if "/Names" in root and "/JavaScript" in root.Names:
            del root.Names["/JavaScript"]
            if not root.Names:
                del root["/Names"]

        # Anotações
        if remove_annotations:
            for page in pdf.pages:
                if "/Annots" in page:
                    page.Annots = pikepdf.Array()

        # Arquivos embutidos
        if remove_embedded and "/Names" in root and "/EmbeddedFiles" in root.Names:
            del root.Names["/EmbeddedFiles"]

        pdf.save(out_path)