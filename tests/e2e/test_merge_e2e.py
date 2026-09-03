import os
import sys
import threading

import pytest
from PyPDF2 import PdfReader, PdfWriter
from werkzeug.serving import make_server

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import expect, sync_playwright
except ImportError:
    PlaywrightError = RuntimeError
    expect = None
    sync_playwright = None

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from app import create_app  # noqa: E402


def _make_pdf(path, page_sizes):
    writer = PdfWriter()
    for width, height in page_sizes:
        writer.add_blank_page(width=width, height=height)
    with open(path, 'wb') as stream:
        writer.write(stream)
    return path


def _page_sizes(path):
    reader = PdfReader(str(path))
    return [
        (
            int(round(float(pdf_page.mediabox.width))),
            int(round(float(pdf_page.mediabox.height))),
        )
        for pdf_page in reader.pages
    ]


@pytest.fixture
def merge_browser(tmp_path):
    if sync_playwright is None:
        pytest.skip('Playwright unavailable')

    app = create_app()
    app.config['TESTING'] = True
    app.config['UPLOAD_FOLDER'] = str(tmp_path)
    app.config['WTF_CSRF_ENABLED'] = False

    server = make_server('127.0.0.1', 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = 'http://127.0.0.1:' + str(server.server_port)

    try:
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except PlaywrightError:
                pytest.skip('browser unavailable')
            try:
                yield browser.new_page(), tmp_path, base_url
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _open_merge(page, base_url):
    page.goto(base_url + '/merge', wait_until='networkidle')


def _upload(page, *paths):
    page.set_input_files(
        'input#input-merge',
        [str(path) for path in paths],
    )


def _card(page, source, page_number=1):
    return page.locator(
        f'.page-wrapper[data-source={source}][data-page={page_number}]'
    )


def _wait_for_cards(page, count):
    expect(page.locator('#preview-merge .page-wrapper')).to_have_count(
        count,
        timeout=20_000,
    )


def test_compact_collapsed_x_removes_entire_pdf(merge_browser):
    page, tmp_path, base_url = merge_browser
    first = _make_pdf(tmp_path / 'first.pdf', [(101, 201), (102, 202), (103, 203)])
    second = _make_pdf(tmp_path / 'second.pdf', [(201, 301), (202, 302)])

    _open_merge(page, base_url)
    _upload(page, first, second)
    _wait_for_cards(page, 5)

    remove = _card(page, 'A').locator('button.remove-file')
    expect(remove).to_have_attribute('title', 'Remover PDF inteiro')
    expect(remove).to_have_attribute('aria-label', 'Remover PDF A inteiro')
    remove.click()

    expect(page.locator('.page-wrapper[data-source=A]')).to_have_count(0)
    expect(page.locator('.page-wrapper[data-source=B]')).to_have_count(2)
    expect(page.locator('#btn-merge')).to_be_disabled()


def test_expanded_and_compact_off_x_remove_only_one_page(merge_browser):
    page, tmp_path, base_url = merge_browser
    first = _make_pdf(tmp_path / 'first.pdf', [(101, 201), (102, 202), (103, 203)])
    second = _make_pdf(tmp_path / 'second.pdf', [(201, 301), (202, 302)])

    _open_merge(page, base_url)
    _upload(page, first, second)
    _wait_for_cards(page, 5)

    _card(page, 'A').locator('button.compact-toggle').click()
    second_page = _card(page, 'A', 2)
    expect(second_page).to_be_visible()
    remove_second = second_page.locator('button.remove-file')
    expect(remove_second).to_have_attribute('title', 'Remover página 2')
    expect(remove_second).to_have_attribute('aria-label', 'Remover página 2')
    remove_second.click()

    expect(_card(page, 'A', 2)).to_have_count(0)
    expect(page.locator('.page-wrapper[data-source=A]')).to_have_count(2)
    expect(page.locator('#btn-merge')).to_be_enabled()

    page.locator('#btn-toggle-compact').click()
    expect(page.locator('#preview-merge')).to_have_attribute('data-compact', 'off')
    remove_first = _card(page, 'A', 1).locator('button.remove-file')
    expect(remove_first).to_have_attribute('title', 'Remover página 1')
    remove_first.click()

    expect(_card(page, 'A', 1)).to_have_count(0)
    expect(_card(page, 'A', 3)).to_have_count(1)
    expect(page.locator('#btn-merge')).to_be_enabled()


def test_last_page_removal_drops_source_and_disables_merge(merge_browser):
    page, tmp_path, base_url = merge_browser
    first = _make_pdf(tmp_path / 'first.pdf', [(101, 201), (102, 202)])
    second = _make_pdf(tmp_path / 'second.pdf', [(201, 301)])

    _open_merge(page, base_url)
    _upload(page, first, second)
    _wait_for_cards(page, 3)
    page.locator('#btn-toggle-compact').click()

    _card(page, 'A', 1).locator('button.remove-file').click()
    _card(page, 'A', 2).locator('button.remove-file').click()

    expect(page.locator('.page-wrapper[data-source=A]')).to_have_count(0)
    expect(page.locator('.page-wrapper[data-source=B]')).to_have_count(1)
    expect(page.locator('#btn-merge')).to_be_disabled()


def test_remove_middle_add_next_identity_and_merge_exact_sources(merge_browser):
    page, tmp_path, base_url = merge_browser
    first = _make_pdf(tmp_path / 'a.pdf', [(101, 201), (102, 202), (103, 203)])
    middle = _make_pdf(tmp_path / 'b.pdf', [(201, 301)])
    third = _make_pdf(tmp_path / 'c.pdf', [(301, 401), (302, 402)])
    fourth = _make_pdf(tmp_path / 'd.pdf', [(401, 501)])

    _open_merge(page, base_url)
    _upload(page, first, middle, third)
    _wait_for_cards(page, 6)

    expect(_card(page, 'A')).to_have_attribute('data-src-index', '0')
    expect(_card(page, 'B')).to_have_attribute('data-src-index', '1')
    expect(_card(page, 'C')).to_have_attribute('data-src-index', '2')

    _card(page, 'B').locator('button.remove-file').click()
    expect(page.locator('.page-wrapper[data-source=B]')).to_have_count(0)
    expect(page.locator('#btn-merge')).to_be_enabled()

    with page.expect_download(timeout=60_000) as first_download_info:
        page.locator('#btn-merge').click()
    first_output = tmp_path / 'merged-a-c.pdf'
    first_download_info.value.save_as(first_output)
    assert _page_sizes(first_output) == [
        (101, 201),
        (102, 202),
        (103, 203),
        (301, 401),
        (302, 402),
    ]

    _upload(page, fourth)
    _wait_for_cards(page, 6)
    expect(_card(page, 'D')).to_have_attribute('data-src-index', '3')
    expect(page.locator('#btn-merge')).to_be_enabled()

    with page.expect_download(timeout=60_000) as download_info:
        page.locator('#btn-merge').click()
    output = tmp_path / 'merged-ui.pdf'
    download_info.value.save_as(output)

    assert _page_sizes(output) == [
        (101, 201),
        (102, 202),
        (103, 203),
        (301, 401),
        (302, 402),
        (401, 501),
    ]


def test_invalid_pdf_rolls_back_source_without_reusing_identity(merge_browser):
    page, tmp_path, base_url = merge_browser
    invalid = tmp_path / 'invalid.pdf'
    invalid.write_bytes(b'not a pdf')
    second = _make_pdf(tmp_path / 'valid-b.pdf', [(201, 301)])
    third = _make_pdf(tmp_path / 'valid-c.pdf', [(301, 401)])
    fourth = _make_pdf(tmp_path / 'valid-d.pdf', [(401, 501)])

    _open_merge(page, base_url)
    _upload(page, invalid, second, third)
    _wait_for_cards(page, 2)

    expect(page.locator('.page-wrapper[data-source=A]')).to_have_count(0)
    expect(_card(page, 'B')).to_have_attribute('data-src-index', '1')
    expect(_card(page, 'C')).to_have_attribute('data-src-index', '2')
    expect(page.locator('#btn-merge')).to_be_enabled()

    _upload(page, fourth)
    _wait_for_cards(page, 3)
    expect(_card(page, 'D')).to_have_attribute('data-src-index', '3')


_DELAY_ARRAY_BUFFER = '''
(() => {
  const original = File.prototype.arrayBuffer;
  window.__mergeTestReleases = Object.create(null);
  File.prototype.arrayBuffer = function () {
    if (!this.name.startsWith('slow-')) return original.call(this);
    const file = this;
    const key = file.name.replaceAll('-', '_').replaceAll('.', '_');
    return new Promise((resolve, reject) => {
      window.__mergeTestReleases[key] = () => {
        original.call(file).then(resolve, reject);
      };
    });
  };
})();
'''


def test_clear_invalidates_in_flight_upload_before_identity_reset(merge_browser):
    page, tmp_path, base_url = merge_browser
    slow = _make_pdf(tmp_path / 'slow-old.pdf', [(101, 201)])
    fresh = _make_pdf(tmp_path / 'fresh.pdf', [(201, 301)])

    page.add_init_script(_DELAY_ARRAY_BUFFER)
    _open_merge(page, base_url)
    _upload(page, slow)
    page.wait_for_function(
        '() => Boolean(window.__mergeTestReleases.slow_old_pdf)'
    )
    expect(page.locator('#btn-clear-all')).to_be_enabled()

    page.locator('#btn-clear-all').click()
    _upload(page, fresh)
    _wait_for_cards(page, 1)
    page.evaluate('window.__mergeTestReleases.slow_old_pdf()')
    page.wait_for_timeout(300)

    expect(page.locator('#preview-merge .page-wrapper')).to_have_count(1)
    expect(_card(page, 'A')).to_have_attribute('data-src-index', '0')
    expect(page.locator('#btn-merge')).to_be_disabled()


def test_overlapping_uploads_keep_distinct_reserved_identities(merge_browser):
    page, tmp_path, base_url = merge_browser
    first = _make_pdf(tmp_path / 'slow-a.pdf', [(101, 201)])
    second = _make_pdf(tmp_path / 'slow-b.pdf', [(201, 301)])

    page.add_init_script(_DELAY_ARRAY_BUFFER)
    _open_merge(page, base_url)
    _upload(page, first)
    page.wait_for_function(
        '() => Boolean(window.__mergeTestReleases.slow_a_pdf)'
    )
    _upload(page, second)
    page.wait_for_function(
        '() => Boolean(window.__mergeTestReleases.slow_b_pdf)'
    )

    page.evaluate('window.__mergeTestReleases.slow_b_pdf()')
    expect(_card(page, 'B')).to_have_count(1, timeout=20_000)
    page.evaluate('window.__mergeTestReleases.slow_a_pdf()')
    _wait_for_cards(page, 2)

    expect(_card(page, 'A')).to_have_attribute('data-src-index', '0')
    expect(_card(page, 'B')).to_have_attribute('data-src-index', '1')
    expect(page.locator('#btn-merge')).to_be_enabled()
