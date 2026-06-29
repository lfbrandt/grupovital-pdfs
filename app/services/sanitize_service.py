# app/services/sanitize_service.py
# -*- coding: utf-8 -*-
import pikepdf


def sanitize_pdf(
    input_path: str,
    output_path: str,
    remove_annotations: bool = True,
    remove_actions: bool = True,
    remove_embedded: bool = True,
    preserve_acroform: bool = False,
) -> None:
    """
    Sanitiza um PDF removendo vetores de ataque comuns.

    Parâmetros:
        remove_annotations: Remove /Annots de todas as páginas quando True.
                            Ignorado quando preserve_acroform=True.
        remove_actions:     Remove /AA, /OpenAction, /JS do catalogo.
        remove_embedded:    Remove /EmbeddedFiles do catalogo.
        preserve_acroform:  Quando True, preserva /AcroForm, /Fields e /Annots
                            (necessario para manter aparencia de assinaturas digitais).
                            Ainda remove JS/AA/XFA perigosos dentro do AcroForm.
    """
    with pikepdf.open(input_path, suppress_warnings=True) as pdf:
        root = pdf.Root

        # /AcroForm
        if "/AcroForm" in root:
            if preserve_acroform:
                acroform = root["/AcroForm"]
                if "/XFA" in acroform:
                    del acroform["/XFA"]
                if "/DR" in acroform:
                    dr = acroform["/DR"]
                    if "/JavaScript" in dr:
                        del dr["/JavaScript"]
            else:
                del root["/AcroForm"]

        # /Annots por pagina
        for page in pdf.pages:
            if "/Annots" not in page:
                continue
            if preserve_acroform:
                for annot_ref in page["/Annots"]:
                    try:
                        annot = (
                            annot_ref.get_object()
                            if hasattr(annot_ref, "get_object")
                            else annot_ref
                        )
                        for danger_key in ("/AA", "/A"):
                            if danger_key not in annot:
                                continue
                            try:
                                s = str(annot[danger_key].get("/S", ""))
                                if any(k in s for k in ("JavaScript", "Launch", "URI")):
                                    del annot[danger_key]
                            except Exception:
                                try:
                                    del annot[danger_key]
                                except Exception:
                                    pass
                    except Exception:
                        continue
            elif remove_annotations:
                page["/Annots"] = pikepdf.Array()

        # Acoes perigosas no catalogo
        if remove_actions:
            for key in ("/OpenAction", "/AA"):
                if key in root:
                    del root[key]
            if "/Names" in root:
                if "/JavaScript" in root["/Names"]:
                    del root["/Names"]["/JavaScript"]

        # Arquivos embutidos
        if remove_embedded:
            if "/Names" in root:
                if "/EmbeddedFiles" in root["/Names"]:
                    del root["/Names"]["/EmbeddedFiles"]

        # JavaScript solto no catalogo
        for key in ("/JavaScript", "/JS"):
            if key in root:
                del root[key]

        pdf.save(output_path, linearize=False)


def sanitize_pdf_preserving_content(input_path: str, output_path: str) -> None:
    """
    Sanitiza preservando formularios, widgets e anotacoes.

    Remove acoes e arquivos incorporados. Nao preserva a validade criptografica
    de assinaturas digitais reais; apenas mantem a estrutura e aparencia.
    """
    sanitize_pdf(
        input_path,
        output_path,
        remove_annotations=False,
        remove_actions=True,
        remove_embedded=True,
        preserve_acroform=True,
    )
