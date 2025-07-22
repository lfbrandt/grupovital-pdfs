import os
import sys
import threading
import time
from werkzeug.serving import make_server
import pytest

try:
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    sync_playwright = None
from PyPDF2 import PdfWriter, PdfReader

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from app import create_app  # noqa: E402


def _make_pdf(path):
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.add_blank_page(width=20, height=20)
    with open(path, "wb") as f:
        writer.write(f)


def _start_server(tmpdir):
    app = create_app()
    app.config["UPLOAD_FOLDER"] = str(tmpdir)
    app.config["WTF_CSRF_ENABLED"] = False
    server = make_server("127.0.0.1", 5001, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(1)
    return server, thread


def test_merge_flow(tmp_path):
    if sync_playwright is None:
        pytest.skip("playwright not installed")
    pdf_path = tmp_path / "test.pdf"
    _make_pdf(pdf_path)
    server, thread = _start_server(tmp_path)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto("http://127.0.0.1:5001/merge")
        page.set_input_files("input#input-merge", str(pdf_path))
        page.wait_for_selector('canvas[data-page="1"]')
        page.click('.page-wrapper[data-page="1"]')
        with page.expect_download() as dl_info:
            page.click("#btn-merge")
        download = dl_info.value
        out_file = tmp_path / "out.pdf"
        download.save_as(out_file)
        browser.close()

    server.shutdown()
    thread.join()
    reader = PdfReader(str(out_file))
    assert len(reader.pages) == 1
