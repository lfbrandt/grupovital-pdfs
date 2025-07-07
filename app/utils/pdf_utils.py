import os
from PyPDF2 import PdfReader, PdfWriter
from PIL import Image


def apply_pdf_modifications(pdf_path, modificacoes):
    """Apply rotation or cropping to the given PDF file in-place."""
    if not modificacoes:
        return

    rotate = modificacoes.get('rotate')
    crop = modificacoes.get('crop')

    if rotate is None and crop is None:
        return

    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    for page in reader.pages:
        if rotate:
            page.rotate(rotate)
        if crop and len(crop) == 4:
            llx, lly, urx, ury = crop
            page.cropbox.lower_left = (llx, lly)
            page.cropbox.upper_right = (urx, ury)
            page.mediabox.lower_left = (llx, lly)
            page.mediabox.upper_right = (urx, ury)
        writer.add_page(page)

    tmp_path = pdf_path + '.tmp'
    with open(tmp_path, 'wb') as f:
        writer.write(f)
    os.replace(tmp_path, pdf_path)


def apply_image_modifications(image, modificacoes):
    """Return a PIL Image with optional rotation and cropping."""
    if not modificacoes:
        return image

    rotate = modificacoes.get('rotate')
    crop = modificacoes.get('crop')

    if rotate:
        image = image.rotate(rotate, expand=True)
    if crop and len(crop) == 4:
        image = image.crop(tuple(crop))
    return image
