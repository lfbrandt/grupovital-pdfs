from PyPDF2 import PdfReader, PdfWriter

from app import create_app
from app.services.merge_service import merge_selected_pdfs


def _write_pdf(path, page_sizes):
    writer = PdfWriter()
    for width, height in page_sizes:
        writer.add_blank_page(width=width, height=height)
    with open(path, 'wb') as stream:
        writer.write(stream)


def test_merge_selected_pdfs_respects_modern_plan(tmp_path):
    app = create_app()
    app.config['UPLOAD_FOLDER'] = tmp_path
    first = tmp_path / 'a.pdf'
    second = tmp_path / 'b.pdf'
    _write_pdf(first, [(10, 11), (12, 13), (14, 15)])
    _write_pdf(second, [(21, 22), (23, 24), (25, 26)])

    plan = [
        {'src': 0, 'page': 2, 'rotation': 0},
        {'src': 1, 'page': 1, 'rotation': 0},
        {'src': 1, 'page': 3, 'rotation': 0},
    ]
    with app.app_context():
        output, warnings = merge_selected_pdfs(
            file_paths=[str(first), str(second)],
            plan=plan,
            normalize='off',
        )

    reader = PdfReader(output)
    sizes = [
        (float(page.mediabox.width), float(page.mediabox.height))
        for page in reader.pages
    ]
    assert sizes == [(12, 13), (21, 22), (25, 26)]
    assert warnings == []
