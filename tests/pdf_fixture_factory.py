from __future__ import annotations

import io
from pathlib import Path

import fitz
import pikepdf
from PIL import Image
from pikepdf import Array, Dictionary, Name, Stream, String
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


PAGE1_TEXT = "PAGINA 1 TEXTO NORMAL GV-P1-001"
PAGE2_TEXT = "PAGINA 2 TEXTO NORMAL GV-P1-001"
FIELD_PAGE_1 = "campo_texto_pagina_1"
FIELD_PAGE_2 = "campo_texto_pagina_2"
FIELD_SIG = "assinatura_visual_teste"
FIELD_PARENT_KIDS = "campo_pai_com_kids"
VALUE_PAGE_1 = "VALOR-PREENCHIDO-PAGINA-1"
VALUE_PAGE_2 = "VALOR-PREENCHIDO-PAGINA-2"
VALUE_PARENT_KIDS = "VALOR-PAI-KIDS"
SIGNATURE_VISUAL_TEXT = "ASSINATURA VISUAL TESTE"
PLAIN_PAGE1_TEXT = "PAGINA 1 SEM FORMULARIO GV-P1-001"
PLAIN_PAGE2_TEXT = "PAGINA 2 SEM FORMULARIO GV-P1-001"
EMBEDDED_NAME = "fixture.txt"
EMBEDDED_CONTENT = b"ARQUIVO-INCORPORADO-TESTE"
SIGNATURE_RECT = (72, 420, 252, 460)


def make_synthetic_pdf(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    base = target.with_name(f"{target.stem}.base.pdf")

    width, height = letter
    doc = canvas.Canvas(str(base), pagesize=letter)
    doc.drawString(72, height - 72, PAGE1_TEXT)
    doc.acroForm.textfield(
        name=FIELD_PAGE_1,
        tooltip="Campo pagina 1",
        x=72,
        y=height - 140,
        width=240,
        height=20,
        value=VALUE_PAGE_1,
        borderStyle="solid",
        forceBorder=True,
    )
    doc.showPage()
    doc.drawString(72, height - 72, PAGE2_TEXT)
    doc.acroForm.textfield(
        name=FIELD_PAGE_2,
        tooltip="Campo pagina 2",
        x=72,
        y=height - 140,
        width=240,
        height=20,
        value=VALUE_PAGE_2,
        borderStyle="solid",
        forceBorder=True,
    )
    doc.save()

    with pikepdf.open(base) as pdf:
        pdf.attachments[EMBEDDED_NAME] = EMBEDDED_CONTENT
        root = pdf.Root
        root["/OpenAction"] = Dictionary(
            {"/S": Name("/JavaScript"), "/JS": String("app.alert('GV-P1-001');")}
        )
        names = root.get("/Names", Dictionary())
        names["/JavaScript"] = Dictionary(
            {
                "/Names": Array(
                    [
                        String("GV_SYNTHETIC_JS"),
                        pdf.make_indirect(
                            Dictionary(
                                {
                                    "/S": Name("/JavaScript"),
                                    "/JS": String("app.alert('GV-P1-001-NAME');"),
                                }
                            )
                        ),
                    ]
                )
            }
        )
        root["/Names"] = names

        for idx, page in enumerate(pdf.pages, start=1):
            annots = page.get("/Annots", Array())
            annots.append(
                pdf.make_indirect(
                    Dictionary(
                        {
                            "/Type": Name("/Annot"),
                            "/Subtype": Name("/Square"),
                            "/Rect": Array([72, 500, 180, 535]),
                            "/C": Array([1, 0, 0]),
                            "/Contents": String(f"ANNOT-PAGINA-{idx}"),
                            "/F": 4,
                        }
                    )
                )
            )
            page["/Annots"] = annots

        _add_synthetic_signature_widget(pdf)
        _set_synthetic_calculation_order(pdf)
        pdf.save(target)

    try:
        base.unlink()
    except OSError:
        pass
    return target


def make_plain_pdf(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = letter
    doc = canvas.Canvas(str(target), pagesize=letter)
    doc.drawString(72, height - 72, PLAIN_PAGE1_TEXT)
    doc.drawImage(_small_raster_image(), 72, height - 150, width=36, height=36, mask="auto")
    doc.showPage()
    doc.drawString(72, height - 72, PLAIN_PAGE2_TEXT)
    doc.drawImage(_small_raster_image(), 72, height - 150, width=36, height=36, mask="auto")
    doc.save()
    return target


def make_annotation_only_pdf(path: str | Path) -> Path:
    target = Path(path)
    base = make_plain_pdf(target.with_name(f"{target.stem}.base.pdf"))
    with pikepdf.open(base) as pdf:
        page = pdf.pages[0]
        annots = page.get("/Annots", Array())
        annots.append(
            pdf.make_indirect(
                Dictionary(
                    {
                        "/Type": Name("/Annot"),
                        "/Subtype": Name("/Square"),
                        "/Rect": Array([72, 500, 180, 535]),
                        "/C": Array([0, 0, 1]),
                        "/Contents": String("ANNOT-ONLY"),
                        "/F": 4,
                    }
                )
            )
        )
        page["/Annots"] = annots
        pdf.save(target)
    try:
        base.unlink()
    except OSError:
        pass
    return target


def make_text_field_only_pdf(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    width, height = letter
    doc = canvas.Canvas(str(target), pagesize=letter)
    doc.drawString(72, height - 72, PAGE1_TEXT)
    doc.acroForm.textfield(
        name=FIELD_PAGE_1,
        tooltip="Campo texto",
        x=72,
        y=height - 140,
        width=240,
        height=20,
        value=VALUE_PAGE_1,
        borderStyle="solid",
        forceBorder=True,
    )
    doc.save()
    return target


def _small_raster_image() -> ImageReader:
    img = Image.new("RGB", (10, 10), (230, 240, 255))
    for idx in range(10):
        img.putpixel((idx, idx), (20, 80, 160))
        img.putpixel((9 - idx, idx), (220, 60, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ImageReader(buf)


def make_parent_kids_pdf(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    base = target.with_name(f"{target.stem}.base.pdf")

    width, height = letter
    doc = canvas.Canvas(str(base), pagesize=letter)
    doc.drawString(72, height - 72, PAGE1_TEXT)
    doc.showPage()
    doc.drawString(72, height - 72, PAGE2_TEXT)
    doc.save()

    with pikepdf.open(base) as pdf:
        acroform = Dictionary(
            {
                "/DA": String("/Helv 0 Tf 0 g"),
                "/DR": Dictionary(
                    {
                        "/Font": Dictionary(
                            {
                                "/Helv": Dictionary(
                                    {
                                        "/Type": Name("/Font"),
                                        "/Subtype": Name("/Type1"),
                                        "/BaseFont": Name("/Helvetica"),
                                    }
                                )
                            }
                        )
                    }
                ),
            }
        )
        parent = pdf.make_indirect(
            Dictionary(
                {
                    "/FT": Name("/Tx"),
                    "/T": String(FIELD_PARENT_KIDS),
                    "/V": String(VALUE_PARENT_KIDS),
                    "/Kids": Array(),
                }
            )
        )
        kids = Array()
        for index, page in enumerate(pdf.pages, start=1):
            widget = _make_text_widget(
                pdf,
                page,
                parent,
                f"{VALUE_PARENT_KIDS}-{index}",
                [72, 620, 260, 646],
            )
            kids.append(widget)
            annots = page.get("/Annots", Array())
            annots.append(widget)
            page["/Annots"] = annots

        parent["/Kids"] = kids
        acroform["/Fields"] = Array([parent])
        pdf.Root["/AcroForm"] = acroform
        pdf.save(target)

    try:
        base.unlink()
    except OSError:
        pass
    return target


def _add_synthetic_signature_widget(pdf: pikepdf.Pdf) -> None:
    page = pdf.pages[0]
    x0, y0, x1, y1 = SIGNATURE_RECT
    width = x1 - x0
    height = y1 - y0
    appearance = Stream(
        pdf,
        (
            b"q 0.9 0.95 1 rg 0 0 180 40 re f "
            b"0 0 0 RG 1 w 0.5 0.5 179 39 re S "
            b"BT /Helv 10 Tf 10 18 Td (ASSINATURA VISUAL TESTE) Tj ET Q"
        ),
    )
    appearance["/Type"] = Name("/XObject")
    appearance["/Subtype"] = Name("/Form")
    appearance["/BBox"] = Array([0, 0, width, height])
    appearance["/Resources"] = Dictionary(
        {
            "/Font": Dictionary(
                {
                    "/Helv": Dictionary(
                        {
                            "/Type": Name("/Font"),
                            "/Subtype": Name("/Type1"),
                            "/BaseFont": Name("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    appearance_ref = pdf.make_indirect(appearance)
    widget = pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Annot"),
                "/Subtype": Name("/Widget"),
                "/FT": Name("/Sig"),
                "/T": String(FIELD_SIG),
                "/Rect": Array(SIGNATURE_RECT),
                "/F": 4,
                "/P": page.obj,
                "/AP": Dictionary({"/N": appearance_ref}),
            }
        )
    )
    annots = page.get("/Annots", Array())
    annots.append(widget)
    page["/Annots"] = annots

    acroform = pdf.Root.get("/AcroForm", Dictionary())
    fields = acroform.get("/Fields", Array())
    fields.append(widget)
    acroform["/Fields"] = fields
    acroform["/SigFlags"] = 3
    pdf.Root["/AcroForm"] = acroform


def _set_synthetic_calculation_order(pdf: pikepdf.Pdf) -> None:
    acroform = pdf.Root["/AcroForm"]
    fields_by_name = {
        _string(field.get("/T")): field
        for field in acroform.get("/Fields", [])
        if _string(field.get("/T"))
    }
    acroform["/CO"] = Array(
        [
            fields_by_name[FIELD_PAGE_1],
            fields_by_name[FIELD_PAGE_2],
        ]
    )


def _make_text_widget(
    pdf: pikepdf.Pdf,
    page,
    parent,
    text: str,
    rect,
):
    x0, y0, x1, y1 = rect
    width = x1 - x0
    height = y1 - y0
    appearance = Stream(
        pdf,
        (
            b"q 1 1 1 rg 0 0 188 26 re f "
            b"0 0 0 RG 1 w 0.5 0.5 187 25 re S "
            + f"BT /Helv 10 Tf 8 10 Td ({text}) Tj ET Q".encode("ascii")
        ),
    )
    appearance["/Type"] = Name("/XObject")
    appearance["/Subtype"] = Name("/Form")
    appearance["/BBox"] = Array([0, 0, width, height])
    appearance["/Resources"] = Dictionary(
        {
            "/Font": Dictionary(
                {
                    "/Helv": Dictionary(
                        {
                            "/Type": Name("/Font"),
                            "/Subtype": Name("/Type1"),
                            "/BaseFont": Name("/Helvetica"),
                        }
                    )
                }
            )
        }
    )
    appearance_ref = pdf.make_indirect(appearance)
    return pdf.make_indirect(
        Dictionary(
            {
                "/Type": Name("/Annot"),
                "/Subtype": Name("/Widget"),
                "/Rect": Array(rect),
                "/F": 4,
                "/P": page.obj,
                "/Parent": parent,
                "/AP": Dictionary({"/N": appearance_ref}),
            }
        )
    )


def inspect_pdf(path: str | Path) -> dict:
    with pikepdf.open(path, suppress_warnings=True) as pdf:
        root = pdf.Root
        fields = _collect_fields(root.get("/AcroForm"))
        pages = []
        signature_widgets = []
        for page_index, page in enumerate(pdf.pages, start=1):
            annots = list(page.get("/Annots", []))
            widgets = []
            non_widgets = []
            for annot in annots:
                subtype = _name(annot.get("/Subtype"))
                if subtype == "/Widget":
                    widget = _widget_info(annot, page_index)
                    widgets.append(widget)
                    if widget["field_type"] == "/Sig":
                        signature_widgets.append(widget)
                else:
                    non_widgets.append(
                        {
                            "subtype": subtype,
                            "contents": _string(annot.get("/Contents")),
                        }
                    )
            pages.append(
                {
                    "page_number": page_index,
                    "widget_count": len(widgets),
                    "widgets": widgets,
                    "non_widget_annots": non_widgets,
                }
            )

        return {
            "page_count": len(pdf.pages),
            "has_acroform": "/AcroForm" in root,
            "fields": fields,
            "field_names": sorted(fields.keys()),
            "calculation_order": _calculation_order(root.get("/AcroForm")),
            "page_annots": pages,
            "signature_widgets": signature_widgets,
            "has_javascript": _has_javascript(root),
            "embedded_files": _embedded_files(pdf),
        }


def extract_text(path: str | Path) -> str:
    with fitz.open(str(path)) as doc:
        return "\n".join(page.get_text() for page in doc)


def signature_region_stats(path: str | Path, page_index: int = 0) -> dict:
    with fitz.open(str(path)) as doc:
        if page_index >= doc.page_count:
            return {"non_white_ratio": 0.0, "variance": 0.0}
        page = doc[page_index]
        x0, y0, x1, y1 = SIGNATURE_RECT
        clip = fitz.Rect(x0, page.rect.height - y1, x1, page.rect.height - y0)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False, annots=True)
        samples = pix.samples
        if not samples:
            return {"non_white_ratio": 0.0, "variance": 0.0}
        total = pix.width * pix.height
        non_white = 0
        luminance = []
        for i in range(0, len(samples), 3):
            r, g, b = samples[i], samples[i + 1], samples[i + 2]
            if not (r > 245 and g > 245 and b > 245):
                non_white += 1
            luminance.append((r + g + b) / 3)
        mean = sum(luminance) / len(luminance)
        variance = sum((value - mean) ** 2 for value in luminance) / len(luminance)
        return {"non_white_ratio": non_white / total, "variance": variance}


def _collect_fields(acroform) -> dict:
    if not acroform or "/Fields" not in acroform:
        return {}
    fields = {}
    for field in acroform.get("/Fields", []):
        for resolved in _walk_fields(field):
            name = _string(resolved.get("/T"))
            if not name:
                continue
            fields[name] = {
                "type": _name(resolved.get("/FT")),
                "value": _string(resolved.get("/V")),
                "has_ap_n": _has_ap_n(resolved),
            }
    return fields


def _walk_fields(field):
    yield field
    for kid in field.get("/Kids", []):
        yield from _walk_fields(kid)


def _calculation_order(acroform) -> list[str]:
    if not acroform or "/CO" not in acroform:
        return []
    return [_string(field.get("/T")) for field in acroform["/CO"] if _string(field.get("/T"))]


def _widget_info(widget, page_number: int) -> dict:
    parent = widget.get("/Parent", Dictionary())
    field_type = _name(widget.get("/FT")) or _name(parent.get("/FT"))
    field_name = _string(widget.get("/T")) or _string(parent.get("/T"))
    field_value = _string(widget.get("/V")) or _string(parent.get("/V"))
    return {
        "page_number": page_number,
        "field_name": field_name,
        "field_type": field_type,
        "value": field_value,
        "has_ap_n": _has_ap_n(widget),
        "rect": [float(value) for value in widget.get("/Rect", [])],
    }


def _has_ap_n(obj) -> bool:
    ap = obj.get("/AP")
    return bool(ap and "/N" in ap)


def _has_javascript(root) -> bool:
    if any(key in root for key in ("/OpenAction", "/AA", "/JavaScript", "/JS")):
        return True
    names = root.get("/Names")
    return bool(names and "/JavaScript" in names)


def _embedded_files(pdf: pikepdf.Pdf) -> list[dict]:
    files = []
    for name in sorted(pdf.attachments.keys()):
        attached = pdf.attachments[name].get_file()
        files.append({"name": name, "content": attached.read_bytes()})
    return files


def _name(value) -> str:
    return str(value) if value is not None else ""


def _string(value) -> str:
    return str(value) if value is not None else ""
