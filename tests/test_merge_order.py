from PyPDF2 import PdfReader, PdfWriter

from app import create_app
from app.services.merge_service import merge_selected_pdfs


def _write_pdf(path, width, height):
    writer = PdfWriter()
    writer.add_blank_page(width=width, height=height)
    with open(path, 'wb') as stream:
        writer.write(stream)


def test_merge_selected_pdfs_respects_file_order(tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    first = tmp_path / 'a.pdf'
    second = tmp_path / 'b.pdf'
    _write_pdf(first, 10, 20)
    _write_pdf(second, 30, 40)

    with app.app_context():
        output, warnings = merge_selected_pdfs(
            file_paths=[str(second), str(first)],
            normalize='off',
        )

    reader = PdfReader(output)
    sizes = [
        (float(page.mediabox.width), float(page.mediabox.height))
        for page in reader.pages
    ]
    assert sizes == [(30, 40), (10, 20)]
    assert warnings == []
