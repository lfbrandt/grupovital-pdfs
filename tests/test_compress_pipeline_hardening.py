from __future__ import annotations

import io
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pikepdf
import pytest
from PyPDF2 import PdfReader, PdfWriter

from app import create_app
from app.services import compress_service
from tests.pdf_fixture_factory import (
    PLAIN_PAGE1_TEXT,
    PLAIN_PAGE2_TEXT,
    make_plain_pdf,
)


@pytest.fixture
def app(tmp_path):
    application = create_app()
    application.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        RATELIMIT_ENABLED=False,
        UPLOAD_FOLDER=tmp_path,
    )
    return application


def _pdf_with_pages(path: Path, page_count: int) -> Path:
    writer = PdfWriter()
    for index in range(page_count):
        writer.add_blank_page(width=595 + index, height=842)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _patch_thumbnails(monkeypatch):
    from app.routes import compress as compress_routes

    calls = []

    def fake_thumbnail(_path, page_index):
        calls.append(page_index)
        return f"data:image/svg+xml;base64,page-{page_index + 1}"

    monkeypatch.setattr(compress_routes, "_generate_page_thumbnail", fake_thumbnail)
    return compress_routes, calls


def _analyze(client, path: Path, monkeypatch) -> tuple[str, object]:
    _patch_thumbnails(monkeypatch)
    response = client.post(
        "/api/compress/analyze",
        data={"file": (io.BytesIO(path.read_bytes()), "fixture.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()["analyse_id"], response


def _fake_group_original(
    input_path,
    output_path,
    pages,
    quality,
    dpi,
    resize_to_a4=False,
    rotations=None,
):
    compress_service._apply_rotations_pikepdf(
        input_path,
        pages,
        rotations,
        output_path,
    )
    return compress_service.CompressionGroupWarnings(
        fallback_reason="gs_larger"
    )


def _process(client, analyse_id: str, page_settings: list, rotations=None):
    return client.post(
        "/api/compress/process-with-settings",
        json={
            "analyse_id": analyse_id,
            "page_settings": page_settings,
            "rotations": rotations,
        },
        headers={"Accept": "application/pdf"},
    )


def _lock_process_worker(
    upload_folder: str,
    analyse_id: str,
    start_event,
    release_event,
    result_queue,
) -> None:
    from app.routes import compress as compress_routes

    try:
        start_event.wait(10)
        token = compress_routes._acquire_process_lock(analyse_id, upload_folder)
        result_queue.put(("acquired", bool(token)))
        if token:
            release_event.wait(10)
            compress_routes._release_process_lock(
                analyse_id,
                upload_folder,
                token,
            )
    except Exception as exc:
        result_queue.put(("error", type(exc).__name__))


def _guard_holder_worker(
    upload_folder: str,
    analyse_id: str,
    acquired_event,
    release_event,
) -> None:
    from app.routes import compress as compress_routes

    with compress_routes._process_lock_guard(
        analyse_id,
        upload_folder,
        blocking=True,
    ) as guarded:
        if not guarded:
            return
        acquired_event.set()
        release_event.wait(10)


def _set_event_after(delay: float, event) -> None:
    time.sleep(delay)
    event.set()


def _run_compress_js(expression: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js não disponível para validar compress.js")

    harness = f"""
const fs = require('fs');
const vm = require('vm');
const listeners = {{}};
const elements = new Map();
function makeElement(tagName = 'div') {{
  const classNames = new Set();
  const element = {{
    tagName: String(tagName).toUpperCase(),
    textContent: '',
    className: '',
    disabled: false,
    hidden: false,
    value: '',
    checked: false,
    dataset: {{}},
    attributes: {{}},
    children: [],
    classList: {{
      add(...names) {{
        names.forEach(name => classNames.add(name));
        element.className = [...classNames].join(' ');
      }},
      remove(...names) {{
        names.forEach(name => classNames.delete(name));
        element.className = [...classNames].join(' ');
      }},
      toggle(name, force) {{
        const enabled = force === undefined ? !classNames.has(name) : force;
        if (enabled) classNames.add(name);
        else classNames.delete(name);
        element.className = [...classNames].join(' ');
        return enabled;
      }},
      contains(name) {{ return classNames.has(name); }},
    }},
    setAttribute(name, value) {{ element.attributes[name] = String(value); }},
    removeAttribute(name) {{ delete element.attributes[name]; }},
    appendChild(child) {{ element.children.push(child); return child; }},
    addEventListener() {{}},
    querySelector() {{ return null; }},
    querySelectorAll() {{ return []; }},
  }};
  return element;
}}
const document = {{
  __listeners: listeners,
  addEventListener(type, callback) {{
    (listeners[type] ||= []).push(callback);
  }},
  getElementById(id) {{
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  }},
  createElement(tagName) {{ return makeElement(tagName); }},
  querySelector() {{ return null; }},
  querySelectorAll() {{ return []; }},
}};
class FormData {{
  append() {{}}
}}
const context = {{
  document,
  FormData,
  window: {{
    addEventListener() {{}},
    getCSRFToken() {{ return 'csrf'; }},
  }},
  URL: {{
    createObjectURL() {{ return 'blob:test'; }},
    revokeObjectURL() {{}},
  }},
  console: {{ debug() {{}}, error() {{}} }},
  setTimeout() {{ return 1; }},
  clearTimeout() {{}},
  setInterval() {{ return 1; }},
  clearInterval() {{}},
  fetch: async () => {{ throw new Error('fetch não esperado'); }},
}};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync('app/static/js/compress.js', 'utf8'),
  context,
  {{ filename: 'compress.js' }},
);
(async () => {{
  const result = await Promise.resolve(
    vm.runInContext({json.dumps(expression)}, context)
  );
  process.stdout.write(JSON.stringify(result));
}})().catch((error) => {{
  process.stderr.write(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
}});
"""
    completed = subprocess.run(
        [node, "-"],
        input=harness,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _run_result_card_js(expression: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js não disponível para validar result-card.js")

    harness = f"""
const fs = require('fs');
const vm = require('vm');
class Element {{
  constructor(tagName) {{
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.attributes = {{}};
    this.classList = {{
      add: (...names) => {{
        const current = new Set(this.className.split(/\\s+/).filter(Boolean));
        names.forEach(name => current.add(name));
        this.className = [...current].join(' ');
      }},
      remove: (...names) => {{
        const removed = new Set(names);
        this.className = this.className
          .split(/\\s+/)
          .filter(name => name && !removed.has(name))
          .join(' ');
      }},
    }};
  }}
  appendChild(child) {{
    this.children.push(child);
    return child;
  }}
  setAttribute(name, value) {{
    this.attributes[name] = String(value);
  }}
  addEventListener() {{}}
  focus() {{}}
}}
const document = {{
  createElement(tagName) {{ return new Element(tagName); }},
  querySelector() {{ return null; }},
}};
const context = {{
  document,
  window: {{
    location: {{ origin: 'https://vitaldoc.test' }},
    requestAnimationFrame(callback) {{ callback(); }},
  }},
  URL,
}};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync('app/static/js/result-card.js', 'utf8'),
  context,
  {{ filename: 'result-card.js' }},
);
const result = vm.runInContext({json.dumps(expression)}, context);
process.stdout.write(JSON.stringify(result));
"""
    completed = subprocess.run(
        [node, "-"],
        input=harness,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_process_button_enables_after_successful_analyze():
    result = _run_compress_js(
        """
(async () => {
  document.getElementById('target-size-mb').value = '5';
  fetch = async () => ({
    ok: true,
    json: async () => ({
      analyse_id: 'analysis-ready',
      filename: 'fixture.pdf',
      total_pages: 1,
      uploaded_size_bytes: 102400,
      has_large_pages: false,
      pages: [{
        page_number: 1,
        include: true,
        keep_original: false,
        quality: 80,
        dpi: 100,
        estimated_size_kb: 100,
        size_factor: 1,
        is_large: false,
        thumbnail: 'data:image/png;base64,fixture',
        width: 595,
        height: 842,
      }],
    }),
  });

  await _runAnalyze({ name: 'fixture.pdf', size: 102400 });
  return {
    disabled: document.getElementById('btn-process-with-settings').disabled,
    analyseId: _AState.analyseId,
    inflight: _AState.inflight,
  };
})()
"""
    )

    assert result == {
        "disabled": False,
        "analyseId": "analysis-ready",
        "inflight": False,
    }


def test_process_button_stays_disabled_while_analyzing():
    disabled = _run_compress_js(
        """
(() => {
  _AState.analyseId = 'analysis-ready';
  _AState.pages = [{ include: true }];
  _AState.mode = 'manual';
  _AState.inflight = true;
  _updateProcessButtonState();
  return document.getElementById('btn-process-with-settings').disabled;
})()
"""
    )

    assert disabled is True


def test_process_button_disables_when_no_pages_selected():
    disabled = _run_compress_js(
        """
(() => {
  _AState.analyseId = 'analysis-ready';
  _AState.pages = [{ include: false }];
  _AState.mode = 'manual';
  _AState.inflight = false;
  _updateProcessButtonState();
  return document.getElementById('btn-process-with-settings').disabled;
})()
"""
    )

    assert disabled is True


def test_process_button_reenables_after_processing_finishes():
    result = _run_compress_js(
        """
(() => {
  _AState.analyseId = 'analysis-ready';
  _AState.pages = [{ include: true }];
  _AState.mode = 'manual';
  _AState.inflight = true;
  _updateProcessButtonState();
  const during = document.getElementById('btn-process-with-settings').disabled;

  _AState.inflight = false;
  _updateProcessButtonState();
  const after = document.getElementById('btn-process-with-settings').disabled;
  return { during, after };
})()
"""
    )

    assert result == {"during": True, "after": False}


@pytest.mark.parametrize(
    "path",
    (
        Path("app/static/js/compress.js"),
        Path("app/static/js/result-card.js"),
        Path("app/templates/compress.html"),
    ),
)
def test_compress_frontend_has_no_inline_style_mutations(path):
    source = path.read_text(encoding="utf-8")
    forbidden_patterns = {
        "CSSStyleDeclaration": re.compile(r"\.style\b"),
        "cssText": re.compile(r"\bcssText\b", re.IGNORECASE),
        "setAttribute(style)": re.compile(
            r"""setAttribute\s*\(\s*['"]style['"]""",
            re.IGNORECASE,
        ),
        "style attribute": re.compile(
            r"""\bstyle\s*=\s*['"]""",
            re.IGNORECASE,
        ),
    }

    for label, pattern in forbidden_patterns.items():
        assert pattern.search(source) is None, f"{path}: found {label}"


def test_compress_progress_and_rotation_are_csp_safe():
    source = Path("app/static/js/compress.js").read_text(encoding="utf-8")
    template = Path("app/templates/compress.html").read_text(encoding="utf-8")
    styles = Path("app/static/scss/pages/_compress.scss").read_text(
        encoding="utf-8"
    )

    assert template.count("<progress") == 2
    assert 'id="cz-top-bar"' in template
    assert 'id="progress-bar"' in template
    assert "_setProgressElementValue" in source
    assert "bdg.hidden = !visible" in source
    assert "img.style" not in source
    for angle in (0, 90, 180, 270):
        assert f"pac-rotation--{angle}" in source
        assert f".pac-rotation--{angle}" in styles
    assert "atualizarProgresso(" not in source
    assert "resetarProgresso(" not in source
    assert "fitRotateMedia(" not in source


def test_target_result_presentation_highlights_unmet_target_in_decimal_mb():
    result = _run_compress_js(
        """
(() => {
  const targetAchieved = _resolveTargetAchieved(
    'false',
    true,
    4950000,
    5883889
  );
  return _buildTargetResultPresentation({
    targetAchieved,
    requestedTargetBytes: 5000000,
    finalBytes: 5883889,
    warnings: 'recompressao_jpeg_agressiva',
  });
})()
"""
    )

    assert result["title"] == "Meta não atingida"
    assert result["subtitle"] == (
        "Não foi possível atingir 5,00 MB sem comprometer ainda mais o documento."
    )
    assert result["variant"] == "warning"
    assert result["sizeText"] == "5,88 MB"
    assert result["detailRows"] == [
        {"label": "Melhor resultado seguro", "value": "5,88 MB"},
        {"label": "Acima da meta em", "value": "0,88 MB"},
    ]
    assert result["downloadLabel"] == "Baixar melhor resultado"
    assert result["messages"] == [
        {
            "text": (
                "Recompressão agressiva aplicada. Confira textos pequenos "
                "e imagens antes de enviar."
            ),
            "type": "warning",
        }
    ]


def test_target_result_presentation_keeps_achieved_target_green():
    result = _run_compress_js(
        """
_buildTargetResultPresentation({
  targetAchieved: _resolveTargetAchieved('true', true, 4950000, 4860000),
  requestedTargetBytes: 5000000,
  finalBytes: 4860000,
  warnings: 'tons_de_cinza_aplicados',
})
"""
    )

    assert result["title"] == "Meta atingida"
    assert result["variant"] == "success"
    assert result["detailRows"] == [
        {"label": "Meta solicitada", "value": "5,00 MB"},
        {"label": "Tamanho final", "value": "4,86 MB"},
    ]
    assert result["messages"] == [
        {
            "text": "Tons de cinza aplicados. As cores do documento foram removidas.",
            "type": "warning",
        }
    ]


def test_result_card_options_are_additive_and_default_pages_stay_compatible():
    result = _run_result_card_js(
        """
(() => {
  const flattenText = (node) => [
    node.textContent,
    ...node.children.flatMap(flattenText),
  ].filter(Boolean);
  const standard = window.VitalResultCard.create({
    name: 'convertido.pdf',
    size: 1048576,
    downloadUrl: '/convertido.pdf',
  }, { showView: false });
  const target = window.VitalResultCard.create({
    name: 'comprimido.pdf',
    size: '5,88 MB',
    downloadUrl: '/comprimido.pdf',
    title: 'Meta não atingida',
    subtitle: 'Não foi possível atingir 5,00 MB sem comprometer ainda mais o documento.',
    variant: 'warning',
    detailRows: [
      { label: 'Melhor resultado seguro', value: '5,88 MB' },
      { label: 'Acima da meta em', value: '0,88 MB' },
    ],
    messages: [{
      text: 'Recompressão agressiva aplicada. Confira textos pequenos e imagens antes de enviar.',
      type: 'warning',
    }],
    downloadLabel: 'Baixar melhor resultado',
  }, { showView: false });
  return {
    standardClass: standard.className,
    standardText: flattenText(standard),
    targetClass: target.className,
    targetText: flattenText(target),
  };
})()
"""
    )

    assert result["standardClass"] == "result-card"
    assert "Arquivo pronto" in result["standardText"]
    assert "Tamanho" in result["standardText"]
    assert "1.00 MiB" in result["standardText"]
    assert "Baixar arquivo" in result["standardText"]
    assert result["targetClass"] == "result-card result-card--warning"
    assert "Meta não atingida" in result["targetText"]
    assert "Melhor resultado seguro" in result["targetText"]
    assert "5,88 MB" in result["targetText"]
    assert "Acima da meta em" in result["targetText"]
    assert "0,88 MB" in result["targetText"]
    assert "Baixar melhor resultado" in result["targetText"]
    assert (
        "Recompressão agressiva aplicada. Confira textos pequenos e imagens antes de enviar."
        in result["targetText"]
    )


def test_preserved_interactive_does_not_show_success_copy():
    presentation = _run_compress_js(
        '''
_buildCompressResultPresentation({
  fallback: 'preserved_interactive',
  targetMode: true,
  targetAchieved: false,
  requestedTargetBytes: 5000000,
  finalBytes: 24900000,
  warnings: 'target_not_achieved:interactive_content_preserved; target_not_achieved',
  reductionPct: 0,
})
'''
    )
    rendered = _run_result_card_js(
        f'''
(() => {{
  const presentation = {json.dumps(presentation, ensure_ascii=False)};
  const flattenText = (node) => [
    node.textContent,
    ...node.children.flatMap(flattenText),
  ].filter(Boolean);
  const card = window.VitalResultCard.create({{
    name: 'comprimido.pdf',
    size: presentation.sizeText,
    downloadUrl: '/comprimido.pdf',
    ...presentation,
  }}, {{ showView: false }});
  return {{ className: card.className, text: flattenText(card) }};
}})()
'''
    )

    assert rendered['className'] == 'result-card result-card--warning'
    assert 'Conteúdo interativo preservado' in rendered['text']
    assert 'Meta de tamanho não atingida.' in rendered['text']
    assert 'Baixar arquivo preservado' in rendered['text']
    assert 'Arquivo pronto' not in rendered['text']
    assert 'Seu arquivo foi gerado com sucesso.' not in rendered['text']
    assert 'PDF comprimido com sucesso' not in rendered['text']


def test_preserved_interactive_hides_internal_warning_codes():
    result = _run_compress_js(
        '''
_buildCompressResultPresentation({
  fallback: 'preserved_interactive',
  targetMode: true,
  targetAchieved: false,
  requestedTargetBytes: 5000000,
  finalBytes: 24900000,
  warnings: 'Compressao pesada ignorada para preservar formularios, anotacoes ou assinaturas visuais.; target_not_achieved:interactive_content_preserved; target_not_achieved; group_original; selected_baseline',
  reductionPct: 0,
})
'''
    )
    visible_text = json.dumps(result, ensure_ascii=False)

    for internal_code in (
        'target_not_achieved',
        'interactive_content_preserved',
        'group_original',
        'selected_baseline',
    ):
        assert internal_code not in visible_text


def test_target_not_achieved_interactive_has_human_message():
    result = _run_compress_js(
        '''
_buildCompressResultPresentation({
  fallback: 'preserved_interactive',
  targetMode: true,
  targetAchieved: false,
  requestedTargetBytes: 5000000,
  finalBytes: 24900000,
  warnings: 'target_not_achieved:interactive_content_preserved; target_not_achieved',
  reductionPct: 0,
})
'''
    )

    assert result['title'] == 'Conteúdo interativo preservado'
    assert result['variant'] == 'warning'
    assert result['progressLabel'] == 'Arquivo preservado pronto.'
    assert result['messages'].count(
        {'text': 'Meta de tamanho não atingida.', 'type': 'warning'}
    ) == 1
    assert 'Meta de tamanho não atingida.' in result['feedbackMsg']


def test_partial_interactive_preservation_has_explicit_human_copy():
    result = _run_compress_js(
        '''
_buildCompressResultPresentation({
  fallback: 'partial_interactive_preservation',
  targetMode: true,
  targetAchieved: true,
  requestedTargetBytes: 5000000,
  finalBytes: 2592688,
  warnings: 'Paginas com conteudo interativo foram preservadas; as demais foram comprimidas quando seguro.',
  reductionPct: 90.1,
})
'''
    )
    visible_text = json.dumps(result, ensure_ascii=False)

    assert result['title'] == 'Compressao seletiva concluida'
    assert result['variant'] == 'warning'
    assert result['progressLabel'] == (
        'Resultado seletivo pronto para download.'
    )
    assert 'paginas seguras foram recomprimidas' in result['subtitle']
    assert 'partial_interactive_preservation' not in visible_text


def test_normal_compression_still_shows_success():
    result = _run_compress_js(
        '''
_buildCompressResultPresentation({
  fallback: 'final_compressed',
  targetMode: false,
  targetAchieved: false,
  requestedTargetBytes: 0,
  finalBytes: 4200000,
  warnings: '',
  reductionPct: 42.5,
})
'''
    )

    assert result['title'] == 'Arquivo comprimido'
    assert result['subtitle'] == 'Seu PDF foi comprimido com sucesso.'
    assert result['variant'] == 'success'
    assert result['downloadLabel'] == 'Baixar arquivo comprimido'


def test_selected_baseline_is_not_presented_as_normal_compression():
    result = _run_compress_js(
        '''
_buildCompressResultPresentation({
  fallback: 'selected_baseline',
  targetMode: false,
  targetAchieved: false,
  requestedTargetBytes: 0,
  finalBytes: 9000000,
  warnings: 'compression_fallback:gs_larger; selected_baseline',
  reductionPct: 0,
})
'''
    )
    visible_text = json.dumps(result, ensure_ascii=False)

    assert result['title'] == 'Melhor versão preservada'
    assert result['variant'] == 'warning'
    assert 'Seu PDF foi comprimido com sucesso.' not in visible_text
    assert 'compression_fallback:gs_larger' not in visible_text
    assert 'selected_baseline' not in visible_text


def test_unknown_warning_code_is_not_rendered_raw():
    result = _run_compress_js(
        '''
_buildCompressResultPresentation({
  fallback: 'final_compressed',
  targetMode: false,
  targetAchieved: false,
  requestedTargetBytes: 0,
  finalBytes: 4200000,
  warnings: 'future_internal_warning:private_diagnostic_value',
  reductionPct: 42.5,
})
'''
    )
    visible_text = json.dumps(result, ensure_ascii=False)

    assert result['title'] == 'Arquivo comprimido'
    assert result['messages'] == []
    assert 'future_internal_warning' not in visible_text
    assert 'private_diagnostic_value' not in visible_text


def test_analysis_is_owned_by_flask_session(app, tmp_path, monkeypatch):
    from app.routes import compress as compress_routes

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        _fake_group_original,
    )
    source = make_plain_pdf(tmp_path / "owned.pdf")
    owner_client = app.test_client()
    other_client = app.test_client()
    analyse_id, _ = _analyze(owner_client, source, monkeypatch)
    settings = [{"page_number": 1, "include": True}]

    denied = _process(other_client, analyse_id, settings)
    assert denied.status_code == 404

    allowed = _process(owner_client, analyse_id, settings)
    assert allowed.status_code == 200
    allowed.close()
    assert not (tmp_path / f".compress_lock_{analyse_id}").exists()


def test_legacy_session_without_owner_is_invalid_and_cleaned(app, tmp_path):
    client = app.test_client()
    with client.session_transaction() as client_session:
        client_session["compress_owner_id"] = "l" * 43
    source = _pdf_with_pages(tmp_path / "legacy.pdf", 1)
    analyse_id = "a" * 32
    session_file = tmp_path / f".session_{analyse_id}"
    session_file.write_text(
        json.dumps({"path": str(source), "ts": time.time()}),
        encoding="utf-8",
    )

    response = _process(
        client,
        analyse_id,
        [{"page_number": 1, "include": True}],
    )

    assert response.status_code == 404
    assert not session_file.exists()
    assert not source.exists()


def test_malformed_analyse_id_is_generic_404(app):
    response = _process(
        app.test_client(),
        "../not-an-id",
        [{"page_number": 1, "include": True}],
    )
    assert response.status_code == 404
    assert "não encontrada" in response.get_json()["error"]


@pytest.mark.parametrize("analyse_id", [123, ["not", "a", "string"], {}])
def test_non_string_analyse_id_is_controlled_400(app, analyse_id):
    response = _process(
        app.test_client(),
        analyse_id,
        [{"page_number": 1, "include": True}],
    )

    assert response.status_code == 400
    assert set(response.get_json()) == {"error"}


def test_expired_owned_analysis_removes_metadata_and_pdf(app, tmp_path):
    client = app.test_client()
    with client.session_transaction() as client_session:
        owner_id = "o" * 43
        client_session["compress_owner_id"] = owner_id
    source = _pdf_with_pages(tmp_path / "expired.pdf", 1)
    analyse_id = "e" * 32
    session_file = tmp_path / f".session_{analyse_id}"
    session_file.write_text(
        json.dumps(
            {
                "owner_id": owner_id,
                "analyse_id": analyse_id,
                "path": str(source),
                "created_at": time.time() - 7200,
                "expires_at": time.time() - 3600,
            }
        ),
        encoding="utf-8",
    )

    response = _process(
        client,
        analyse_id,
        [{"page_number": 1, "include": True}],
    )

    assert response.status_code == 404
    assert not session_file.exists()
    assert not source.exists()


@pytest.mark.parametrize("page_count", [1, 2])
def test_page_limit_accepts_one_and_exact_limit(
    app, tmp_path, monkeypatch, page_count
):
    monkeypatch.setenv("MAX_PDF_PAGES", "2")
    source = _pdf_with_pages(tmp_path / f"accepted-{page_count}.pdf", page_count)
    _routes, thumbnail_calls = _patch_thumbnails(monkeypatch)
    response = app.test_client().post(
        "/api/compress/analyze",
        data={"file": (io.BytesIO(source.read_bytes()), "accepted.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(thumbnail_calls) == page_count


def test_page_limit_rejects_before_thumbnails_and_cleans(
    app, tmp_path, monkeypatch
):
    monkeypatch.setenv("MAX_PDF_PAGES", "2")
    source = _pdf_with_pages(tmp_path / "too-many.pdf", 3)
    _routes, thumbnail_calls = _patch_thumbnails(monkeypatch)

    response = app.test_client().post(
        "/api/compress/analyze",
        data={"file": (io.BytesIO(source.read_bytes()), "too-many.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    assert thumbnail_calls == []
    assert list(tmp_path.glob(".session_*")) == []
    assert list(tmp_path.glob("analyze_*")) == []
    assert list(tmp_path.glob("sanitized_*")) == []


def test_empty_pdf_is_rejected_before_thumbnails(app, tmp_path, monkeypatch):
    source = _pdf_with_pages(tmp_path / "empty.pdf", 0)
    _routes, thumbnail_calls = _patch_thumbnails(monkeypatch)

    response = app.test_client().post(
        "/api/compress/analyze",
        data={"file": (io.BytesIO(source.read_bytes()), "empty.pdf")},
        content_type="multipart/form-data",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 422
    assert thumbnail_calls == []
    assert list(tmp_path.glob(".session_*")) == []
    assert list(tmp_path.glob("analyze_*")) == []
    assert list(tmp_path.glob("sanitized_*")) == []


def test_partial_fallback_never_restores_excluded_pages(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        _fake_group_original,
    )
    source = make_plain_pdf(tmp_path / "partial.pdf")
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)

    response = _process(
        client,
        analyse_id,
        [
            {"page_number": 1, "include": True},
            {"page_number": 2, "include": False},
        ],
    )

    assert response.status_code == 200
    reader = PdfReader(io.BytesIO(response.data))
    assert len(reader.pages) == 1
    assert PLAIN_PAGE1_TEXT in (reader.pages[0].extract_text() or "")
    assert PLAIN_PAGE2_TEXT not in (reader.pages[0].extract_text() or "")
    assert response.headers["X-Fallback"] in {
        "group_original",
        "selected_baseline",
    }
    response.close()


def test_selected_order_and_rotation_survive_baseline_fallback(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        _fake_group_original,
    )
    source = make_plain_pdf(tmp_path / "ordered.pdf")
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)

    response = _process(
        client,
        analyse_id,
        [
            {"page_number": 2, "include": True},
            {"page_number": 1, "include": True},
        ],
        rotations={"2": 90},
    )

    reader = PdfReader(io.BytesIO(response.data))
    assert len(reader.pages) == 2
    assert PLAIN_PAGE2_TEXT in (reader.pages[0].extract_text() or "")
    assert PLAIN_PAGE1_TEXT in (reader.pages[1].extract_text() or "")
    assert int(reader.pages[0].get("/Rotate", 0)) == 90
    response.close()


def test_mixed_groups_keep_original_preserve_requested_page_identity(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        _fake_group_original,
    )
    source = _pdf_with_pages(tmp_path / "mixed-groups.pdf", 5)
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)
    real_getsize = compress_routes.os.path.getsize

    def biased_getsize(path):
        size = real_getsize(path)
        if Path(path).name.startswith("selected_baseline_"):
            return size + 1_000_000
        return size

    monkeypatch.setattr(compress_routes.os.path, "getsize", biased_getsize)
    response = _process(
        client,
        analyse_id,
        [
            {
                "page_number": 5,
                "include": True,
                "quality": 70,
                "dpi": 100,
            },
            {
                "page_number": 2,
                "include": True,
                "keep_original": True,
            },
            {
                "page_number": 4,
                "include": True,
                "quality": 80,
                "dpi": 120,
            },
            {"page_number": 1, "include": False},
            {"page_number": 3, "include": False},
        ],
        rotations={"5": 90, "2": 180, "4": 270},
    )

    assert response.status_code == 200
    assert response.headers["X-Fallback"] == "group_original"
    reader = PdfReader(io.BytesIO(response.data))
    assert len(reader.pages) == 3
    assert [float(page.mediabox.width) for page in reader.pages] == [
        599.0,
        596.0,
        598.0,
    ]
    assert [int(page.get("/Rotate", 0)) for page in reader.pages] == [
        90,
        180,
        270,
    ]
    response.close()


def test_no_selected_page_returns_400_without_consuming_analysis(
    app, tmp_path, monkeypatch
):
    source = make_plain_pdf(tmp_path / "none.pdf")
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)

    response = _process(
        client,
        analyse_id,
        [{"page_number": 1, "include": False}],
    )

    assert response.status_code == 400
    assert (tmp_path / f".session_{analyse_id}").exists()


def test_legacy_route_exposes_fallback_headers(app, tmp_path, monkeypatch):
    from app.routes import compress as compress_routes

    output = _pdf_with_pages(tmp_path / 'legacy-fallback.pdf', 1)
    warnings = compress_service.CompressionGroupWarnings(
        ['compression_fallback:gs_larger'],
        fallback_reason='gs_larger',
    )
    monkeypatch.setattr(
        compress_routes,
        'comprimir_pdf',
        lambda *_args, **_kwargs: (str(output), warnings),
    )

    response = app.test_client().post(
        '/api/compress',
        data={'file': (io.BytesIO(output.read_bytes()), 'fixture.pdf')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 200
    assert response.headers['X-Fallback'] == 'gs_larger'
    assert response.headers['X-Compress-Warnings'] == 'compression_fallback:gs_larger'
    assert response.headers['Access-Control-Expose-Headers'] == (
        'X-Fallback, X-Compress-Warnings'
    )
    response.close()


def test_resize_true_is_forwarded_and_splits_groups(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    calls = []

    def fake_group(**kwargs):
        calls.append(kwargs)
        _fake_group_original(**kwargs)
        return compress_service.CompressionGroupWarnings(
            fallback_reason="gs_larger"
        )

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        fake_group,
    )
    source = make_plain_pdf(tmp_path / "resize.pdf")
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)

    response = _process(
        client,
        analyse_id,
        [
            {
                "page_number": 1,
                "include": True,
                "quality": 70,
                "dpi": 100,
                "resize_to_a4": True,
            },
            {
                "page_number": 2,
                "include": True,
                "quality": 70,
                "dpi": 100,
                "resize_to_a4": False,
            },
        ],
    )

    assert response.status_code == 200
    assert len(calls) == 2
    assert {
        (tuple(call['pages']), call['resize_to_a4'])
        for call in calls
    } == {((1,), True), ((2,), False)}
    assert 'Redimensionamento A4 ainda nao disponivel' not in response.headers.get(
        'X-Compress-Warnings',
        '',
    )
    response.close()


def test_resize_control_and_local_estimate_are_honest():
    source = Path("app/static/js/compress.js").read_text(encoding="utf-8")
    estimate_block = source.split(
        "function _estimateSize(page) {", 1
    )[1].split("function _updateSummary()", 1)[0]
    template = Path("app/templates/compress.html").read_text(encoding="utf-8")

    assert "page.resize_to_a4" not in estimate_block
    assert 'data-field="resize_to_a4"' in source
    assert 'A4 — indisponível' not in source
    assert 'Redimensionar para A4' in source
    assert 'id="global-dpi"' in template
    assert 'min="72"' in template
    assert "Estimativa inicial" in template
    assert "delete grid.__analysisEventsBound" not in source


def test_target_mode_does_not_mask_special_fallback_messages():
    source = Path('app/static/js/compress.js').read_text(encoding='utf-8')

    assert 'const specialFallback = (' in source
    assert 'function _buildCompressResultPresentation({' in source
    assert 'warningText' not in source
    assert 'steps[4].text = resultPresentation.progressLabel' in source
    assert 'Conteúdo interativo preservado' in source
    assert 'Melhor versão preservada' in source
    assert 'Compressão não aplicada' in source


def test_resize_pdf_pages_to_a4_changes_only_selected_pages(tmp_path):
    source = tmp_path / 'non-a4-source.pdf'
    output = tmp_path / 'a4-output.pdf'
    writer = PdfWriter()
    writer.add_blank_page(width=400, height=400)
    writer.add_blank_page(width=700, height=300)
    with source.open('wb') as handle:
        writer.write(handle)

    compress_service.resize_pdf_pages_to_a4(
        str(source),
        str(output),
        [1],
    )

    with pikepdf.open(output) as pdf:
        first = compress_service._visual_page_size(pdf.pages[0])
        second = compress_service._visual_page_size(pdf.pages[1])

    assert first == pytest.approx(
        (compress_service.A4_WIDTH_POINTS, compress_service.A4_HEIGHT_POINTS),
        abs=0.05,
    )
    assert second == pytest.approx((700, 300), abs=0.01)


def test_modern_settings_clamp_dpi_and_respect_keep_original():
    from app.routes import compress as compress_routes

    settings, order, resize_requested = (
        compress_routes._normalize_modern_page_settings(
            [
                {
                    'page_number': 1,
                    'dpi': 50,
                    'resize_to_a4': True,
                },
                {
                    'page_number': 2,
                    'dpi': 500,
                    'resize_to_a4': True,
                    'keep_original': True,
                },
            ]
        )
    )

    assert order == [1, 2]
    assert settings[1]['dpi'] == 72
    assert settings[1]['resize_to_a4'] is True
    assert settings[2]['dpi'] == 300
    assert settings[2]['resize_to_a4'] is False
    assert resize_requested is True


def test_local_estimate_is_monotonic_for_quality_dpi_and_reference_profiles():
    result = _run_compress_js(
        """
(() => {
  const base = {
    include: true,
    keep_original: false,
    estimated_size_kb: 1000,
    size_factor: 1,
  };
  const estimate = (quality, dpi) =>
    _estimateSize({ ...base, quality, dpi });
  return {
    quality: [25, 70, 95].map(quality => estimate(quality, 120)),
    dpi: [72, 120, 200].map(dpi => estimate(70, dpi)),
    profiles: [[25, 72], [70, 120], [95, 200]]
      .map(([quality, dpi]) => estimate(quality, dpi)),
  };
})()
"""
    )

    assert result["quality"][0] < result["quality"][1] < result["quality"][2]
    assert result["dpi"][0] < result["dpi"][1] < result["dpi"][2]
    assert result["profiles"][0] < result["profiles"][1] < result["profiles"][2]
    assert len({round(value, 3) for value in result["profiles"]}) == 3


def test_global_controls_update_hidden_included_pages_only_and_bind_once():
    result = _run_compress_js(
        """
(() => {
  _AState.pages = [
    { page_number: 1, include: true, keep_original: false,
      quality: 80, dpi: 100, estimated_size_kb: 100, size_factor: 1 },
    { page_number: 2, include: true, keep_original: false,
      quality: 70, dpi: 120, estimated_size_kb: 100, size_factor: 1 },
    { page_number: 3, include: false, keep_original: false,
      quality: 55, dpi: 150, estimated_size_kb: 100, size_factor: 1 },
    { page_number: 4, include: true, keep_original: true,
      quality: 60, dpi: 160, estimated_size_kb: 100, size_factor: 1 },
  ];
  bindGlobalControls();
  bindGlobalControls();
  const inputListeners = document.__listeners.input;
  inputListeners[0]({ target: { id: 'global-quality', value: '25' } });
  inputListeners[0]({ target: { id: 'global-dpi', value: '72' } });

  const gridCounts = {};
  const grid = {
    addEventListener(type) {
      gridCounts[type] = (gridCounts[type] || 0) + 1;
    },
  };
  _bindCardEvents(grid);
  _bindCardEvents(grid);

  return {
    pages: _AState.pages.map(
      ({ page_number, quality, dpi }) => ({ page_number, quality, dpi })
    ),
    globalInputListeners: inputListeners.length,
    gridCounts,
  };
})()
"""
    )

    assert result["pages"] == [
        {"page_number": 1, "quality": 25, "dpi": 72},
        {"page_number": 2, "quality": 25, "dpi": 72},
        {"page_number": 3, "quality": 55, "dpi": 150},
        {"page_number": 4, "quality": 60, "dpi": 160},
    ]
    assert result["globalInputListeners"] == 1
    assert result["gridCounts"]
    assert set(result["gridCounts"].values()) == {1}


def test_individual_control_updates_only_its_page_and_recalculates_estimate():
    result = _run_compress_js(
        """
(() => {
  _AState.pages = [
    { page_number: 1, include: true, keep_original: false,
      quality: 80, dpi: 120, estimated_size_kb: 1000, size_factor: 1 },
    { page_number: 2, include: true, keep_original: false,
      quality: 70, dpi: 120, estimated_size_kb: 1000, size_factor: 1 },
  ];
  const handlers = {};
  const grid = {
    addEventListener(type, callback) { handlers[type] = callback; },
  };
  _bindCardEvents(grid);

  const qualityLabel = { textContent: '' };
  const card = {
    dataset: { pageNumber: '1' },
    querySelector(selector) {
      return selector === '.pac-quality-val' ? qualityLabel : null;
    },
  };
  const input = {
    dataset: { field: 'quality' },
    value: '25',
    matches(selector) { return selector === 'input[type="range"]'; },
    closest() { return card; },
  };
  const before = _estimateSize(_AState.pages[0]);
  handlers.input({ target: input });
  const after = _estimateSize(_AState.pages[0]);

  return {
    qualities: _AState.pages.map(page => page.quality),
    before,
    after,
    label: qualityLabel.textContent,
  };
})()
"""
    )

    assert result["qualities"] == [25, 70]
    assert result["after"] < result["before"]
    assert result["label"] == "25%"


def test_process_payload_uses_current_page_state():
    payload = _run_compress_js(
        """
(() => {
  _AState.analyseId = 'analysis-current';
  _AState.pages = [
    { page_number: 1, include: true, keep_original: false,
      quality: 25, dpi: 72, resize_to_a4: false },
    { page_number: 2, include: true, keep_original: false,
      quality: 95, dpi: 200, resize_to_a4: false },
    { page_number: 3, include: false, keep_original: false,
      quality: 70, dpi: 120, resize_to_a4: false },
  ];
  return _buildProcessPayload({ '2': 90 });
})()
"""
    )

    assert payload["analyse_id"] == "analysis-current"
    assert [
        (page["quality"], page["dpi"])
        for page in payload["page_settings"]
    ] == [(25, 72), (95, 200), (70, 120)]
    assert all(
        page["resize_to_a4"] is False
        for page in payload["page_settings"]
    )
    assert payload["rotations"] == {"2": 90}


def test_backend_preserves_distinct_settings_groups_and_skips_keep_original(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    calls = []

    def fake_group(**kwargs):
        calls.append(kwargs)
        return _fake_group_original(**kwargs)

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        fake_group,
    )
    source = _pdf_with_pages(tmp_path / "quality-groups.pdf", 3)
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)

    response = _process(
        client,
        analyse_id,
        [
            {
                "page_number": 1,
                "include": True,
                "quality": 25,
                "dpi": 72,
                "resize_to_a4": False,
            },
            {
                "page_number": 2,
                "include": True,
                "quality": 95,
                "dpi": 200,
                "resize_to_a4": False,
            },
            {
                "page_number": 3,
                "include": True,
                "quality": 70,
                "dpi": 120,
                "resize_to_a4": False,
                "keep_original": True,
            },
        ],
    )

    assert response.status_code == 200
    assert len(calls) == 2
    assert {
        (
            tuple(call["pages"]),
            call["quality"],
            call["dpi"],
            call["resize_to_a4"],
        )
        for call in calls
    } == {
        ((1,), 25, 72, False),
        ((2,), 95, 200, False),
    }
    assert all(3 not in call["pages"] for call in calls)
    assert response.headers["X-Fallback"] in {
        "group_original",
        "selected_baseline",
    }
    response.close()


def test_ghostscript_params_change_between_aggressive_and_conservative():
    aggressive = compress_service._build_gs_image_params(25, 72)
    conservative = compress_service._build_gs_image_params(95, 200)
    aggressive_args = compress_service._build_gs_args(
        "input.pdf",
        "aggressive.pdf",
        aggressive,
    )
    conservative_args = compress_service._build_gs_args(
        "input.pdf",
        "conservative.pdf",
        conservative,
    )

    assert aggressive["qfactor"] > conservative["qfactor"]
    assert compress_service._build_gs_image_params(75, 120)['qfactor'] == 0.5
    assert 0.0 <= conservative['qfactor'] <= 1.0
    assert 0.0 <= aggressive['qfactor'] <= 1.0
    assert len({
        compress_service._build_gs_image_params(quality, 100)['qfactor']
        for quality in (20, 40, 60, 75, 90)
    }) == 5
    assert aggressive["color_res"] < conservative["color_res"]
    assert aggressive["downsample"] == "Subsample"
    assert conservative["downsample"] == "Bicubic"
    assert aggressive["hsamples"] != conservative["hsamples"]
    assert aggressive_args[aggressive_args.index("-c") + 1] != (
        conservative_args[conservative_args.index("-c") + 1]
    )


def test_process_lock_is_atomic_and_stale_lock_is_recoverable(app, tmp_path):
    from app.routes import compress as compress_routes

    analyse_id = "c" * 32
    (tmp_path / f".session_{analyse_id}").write_text("{}", encoding="utf-8")
    with app.app_context():
        first = compress_routes._acquire_process_lock(analyse_id, str(tmp_path))
        assert first
        assert compress_routes._acquire_process_lock(analyse_id, str(tmp_path)) is None
        lock_path = Path(
            compress_routes._process_lock_path(analyse_id, str(tmp_path))
        )
        compress_routes._release_process_lock(
            analyse_id,
            str(tmp_path),
            "wrong-token",
        )
        assert lock_path.exists()
        lock_path.write_text(
            json.dumps({"token": first, "created_at": time.time() - 7200}),
            encoding="utf-8",
        )
        assert compress_routes._process_lock_is_stale(str(lock_path))
        assert compress_routes._refresh_process_lock(
            analyse_id,
            str(tmp_path),
            first,
        )
        assert not compress_routes._process_lock_is_stale(str(lock_path))
        assert compress_routes._acquire_process_lock(
            analyse_id,
            str(tmp_path),
        ) is None
        compress_routes._release_process_lock(analyse_id, str(tmp_path), first)

        lock_path.write_text(
            json.dumps({"token": "abandoned", "created_at": time.time() - 7200}),
            encoding="utf-8",
        )
        recovered = compress_routes._acquire_process_lock(
            analyse_id,
            str(tmp_path),
        )
        assert recovered
        compress_routes._release_process_lock(
            analyse_id,
            str(tmp_path),
            recovered,
        )
        assert not lock_path.exists()


def test_stale_lock_recovery_is_serialized_between_processes(tmp_path):
    analyse_id = "d" * 32
    (tmp_path / f".session_{analyse_id}").write_text("{}", encoding="utf-8")
    lock_path = tmp_path / f".compress_lock_{analyse_id}"
    lock_path.write_text(
        json.dumps({"token": "abandoned", "created_at": time.time() - 7200}),
        encoding="utf-8",
    )

    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    release_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_lock_process_worker,
            args=(
                str(tmp_path),
                analyse_id,
                start_event,
                release_event,
                result_queue,
            ),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start_event.set()

    results = [result_queue.get(timeout=20) for _ in processes]
    assert results.count(("acquired", True)) == 1
    assert results.count(("acquired", False)) == 1

    release_event.set()
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert not lock_path.exists()


def test_different_analysis_ids_can_hold_process_locks_together(tmp_path):
    from app.routes import compress as compress_routes

    first_id = "1" * 32
    second_id = "2" * 32
    for analyse_id in (first_id, second_id):
        (tmp_path / f".session_{analyse_id}").write_text("{}", encoding="utf-8")

    first_token = compress_routes._acquire_process_lock(first_id, str(tmp_path))
    second_token = compress_routes._acquire_process_lock(second_id, str(tmp_path))
    try:
        assert first_token
        assert second_token
    finally:
        compress_routes._release_process_lock(
            first_id,
            str(tmp_path),
            first_token,
        )
        compress_routes._release_process_lock(
            second_id,
            str(tmp_path),
            second_token,
        )


def test_lock_refresh_waits_for_short_guard_contention(tmp_path):
    from app.routes import compress as compress_routes

    analyse_id = "4" * 32
    (tmp_path / f".session_{analyse_id}").write_text("{}", encoding="utf-8")
    token = compress_routes._acquire_process_lock(analyse_id, str(tmp_path))
    assert token

    context = multiprocessing.get_context("spawn")
    acquired_event = context.Event()
    release_event = context.Event()
    holder = context.Process(
        target=_guard_holder_worker,
        args=(str(tmp_path), analyse_id, acquired_event, release_event),
    )
    holder.start()
    assert acquired_event.wait(10)
    releaser = context.Process(
        target=_set_event_after,
        args=(0.25, release_event),
    )
    releaser.start()

    started = time.monotonic()
    assert compress_routes._refresh_process_lock(
        analyse_id,
        str(tmp_path),
        token,
    )
    assert time.monotonic() - started >= 0.1

    holder.join(timeout=20)
    releaser.join(timeout=20)
    assert holder.exitcode == 0
    assert releaser.exitcode == 0
    compress_routes._release_process_lock(
        analyse_id,
        str(tmp_path),
        token,
    )


def test_purge_does_not_remove_expired_session_with_active_process_lock(
    app, tmp_path
):
    from app.routes import compress as compress_routes

    analyse_id = "3" * 32
    source = _pdf_with_pages(tmp_path / "active-purge.pdf", 1)
    session_file = tmp_path / f".session_{analyse_id}"
    session_file.write_text(
        json.dumps(
            {
                "owner_id": "p" * 43,
                "analyse_id": analyse_id,
                "path": str(source),
                "created_at": time.time() - 7200,
                "expires_at": time.time() - 1,
            }
        ),
        encoding="utf-8",
    )

    with app.app_context():
        token = compress_routes._acquire_process_lock(
            analyse_id,
            str(tmp_path),
        )
        assert token
        compress_routes._purge_expired_sessions()
        assert session_file.exists()
        assert source.exists()
        compress_routes._release_process_lock(
            analyse_id,
            str(tmp_path),
            token,
        )
        compress_routes._purge_expired_sessions()

    assert not session_file.exists()
    assert not source.exists()


def test_active_process_lock_returns_409(app, tmp_path, monkeypatch):
    from app.routes import compress as compress_routes

    source = make_plain_pdf(tmp_path / "locked.pdf")
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)
    with app.app_context():
        token = compress_routes._acquire_process_lock(
            analyse_id,
            str(tmp_path),
        )
    try:
        response = _process(
            client,
            analyse_id,
            [{"page_number": 1, "include": True}],
        )
        assert response.status_code == 409
    finally:
        with app.app_context():
            compress_routes._release_process_lock(
                analyse_id,
                str(tmp_path),
                token,
            )


def test_lock_is_released_and_analysis_preserved_after_error(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    source = make_plain_pdf(tmp_path / "retry.pdf")
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)
    original_inspector = compress_routes.pdf_requires_content_preservation
    monkeypatch.setattr(
        compress_routes,
        "pdf_requires_content_preservation",
        lambda _path: (_ for _ in ()).throw(RuntimeError("controlled")),
    )

    response = _process(
        client,
        analyse_id,
        [{"page_number": 1, "include": True}],
    )

    assert response.status_code == 500
    assert not (tmp_path / f".compress_lock_{analyse_id}").exists()
    assert (tmp_path / f".session_{analyse_id}").exists()
    metadata = json.loads(
        (tmp_path / f".session_{analyse_id}").read_text(encoding="utf-8")
    )
    assert Path(metadata["path"]).exists()

    monkeypatch.setattr(
        compress_routes,
        "pdf_requires_content_preservation",
        original_inspector,
    )
    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        _fake_group_original,
    )
    retry = _process(
        client,
        analyse_id,
        [{"page_number": 1, "include": True}],
    )
    assert retry.status_code == 200
    retry.close()
    assert not (tmp_path / f".session_{analyse_id}").exists()
    assert not Path(metadata["path"]).exists()
    assert not (tmp_path / f".compress_lock_{analyse_id}").exists()


def test_merge_failure_cleans_temporaries_and_allows_retry(
    app, tmp_path, monkeypatch
):
    from app.routes import compress as compress_routes

    source = make_plain_pdf(tmp_path / "merge-retry.pdf")
    client = app.test_client()
    analyse_id, _ = _analyze(client, source, monkeypatch)
    session_file = tmp_path / f".session_{analyse_id}"
    metadata = json.loads(session_file.read_text(encoding="utf-8"))
    owned_source = Path(metadata["path"])
    original_merge = compress_routes._merge_selected_page_sources

    monkeypatch.setattr(
        compress_routes,
        "comprimir_pdf_com_params",
        _fake_group_original,
    )
    monkeypatch.setattr(
        compress_routes,
        "_merge_selected_page_sources",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("controlled merge failure")
        ),
    )
    settings = [
        {"page_number": 1, "include": True, "quality": 70, "dpi": 100},
        {"page_number": 2, "include": True, "quality": 80, "dpi": 120},
    ]

    failed = _process(client, analyse_id, settings)
    assert failed.status_code == 500
    assert session_file.exists()
    assert owned_source.exists()
    assert not (tmp_path / f".compress_lock_{analyse_id}").exists()
    for pattern in ("selected_baseline_*.pdf", "group_*.pdf", "merged_*.pdf"):
        assert list(tmp_path.glob(pattern)) == []

    monkeypatch.setattr(
        compress_routes,
        "_merge_selected_page_sources",
        original_merge,
    )
    retry = _process(client, analyse_id, settings)
    assert retry.status_code == 200
    assert len(PdfReader(io.BytesIO(retry.data)).pages) == 2
    retry.close()
    assert not session_file.exists()
    assert not owned_source.exists()
    assert not (tmp_path / f".compress_lock_{analyse_id}").exists()


def _output_path_from_gs_command(command: list[str]) -> Path:
    argument = next(
        item for item in command if str(item).startswith("-sOutputFile=")
    )
    return Path(str(argument).split("=", 1)[1])


def test_shared_ghostscript_executor_preserves_args_and_uses_sandbox(
    app, tmp_path, monkeypatch
):
    source = _pdf_with_pages(tmp_path / "gs-source.pdf", 2)
    base_bytes = source.read_bytes()
    source.write_bytes(base_bytes + b" " * 40000)
    output = tmp_path / "gs-output.pdf"
    captured = {}

    def fake_sandbox(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        _output_path_from_gs_command(command).write_bytes(
            base_bytes + b" " * 12000
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(compress_service, "run_in_sandbox", fake_sandbox)
    relative_source = os.path.relpath(source, Path.cwd())
    relative_output = os.path.relpath(output, Path.cwd())
    with app.app_context():
        result = compress_service.execute_ghostscript_validated(
            relative_source,
            relative_output,
            quality=80,
            dpi=100,
            expected_pages=2,
        )

    assert result.usable is True
    assert "-dSAFER" in captured["command"]
    assert "-dNOPAUSE" in captured["command"]
    assert "-dBATCH" in captured["command"]
    assert "-sDEVICE=pdfwrite" in captured["command"]
    output_argument = next(
        item.split("=", 1)[1]
        for item in captured["command"]
        if item.startswith("-sOutputFile=")
    )
    input_argument = captured["command"][captured["command"].index("-f") + 1]
    assert Path(output_argument).is_absolute()
    assert Path(input_argument).is_absolute()
    assert captured["kwargs"]["timeout"] == compress_service.GHOSTSCRIPT_TIMEOUT
    assert captured["kwargs"]["output_limit_chars"] > 0
    assert Path(captured["kwargs"]["cwd"]).parent == tmp_path
    formatted = compress_service._fmt_gs_cmd(
        [
            "gs",
            "-sOutputFile=C:\\private-data\\group_sensitive.pdf",
            "C:\\private-data\\rotated_sensitive.pdf",
        ]
    )
    assert "private-data" not in formatted
    assert "sensitive" not in formatted


def test_ghostscript_version_probe_uses_shared_executor(tmp_path, monkeypatch):
    captured = {}

    def fake_sandbox(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="10.05.1\n",
            stderr="",
        )

    monkeypatch.setattr(compress_service, "_GS_CMD_CACHE", None)
    monkeypatch.setattr(compress_service.shutil, "which", lambda _name: "gs")
    monkeypatch.setattr(compress_service, "run_in_sandbox", fake_sandbox)
    monkeypatch.setenv("SECRET_KEY", "must-not-reach-child")
    monkeypatch.setenv("ADMIN_TOKEN", "must-not-reach-child")
    monkeypatch.setenv("TMP", str(tmp_path))

    assert compress_service._get_gs_cmd() == "gs"
    assert captured["command"] == ["gs", "--version"]
    assert captured["kwargs"]["timeout"] == 5
    assert captured["kwargs"]["output_limit_chars"] > 0
    assert "SECRET_KEY" not in captured["kwargs"]["env"]
    assert "ADMIN_TOKEN" not in captured["kwargs"]["env"]


@pytest.mark.parametrize(
    ("mode", "expected_reason"),
    [
        ("timeout", "timeout"),
        ("nonzero", "gs_error"),
        ("missing", "output_missing"),
        ("unreadable", "output_unreadable"),
        ("page_loss", "page_count_mismatch"),
        ("larger", "gs_larger"),
        ("insufficient_gain", "insufficient_gain"),
    ],
)
def test_shared_ghostscript_executor_rejects_and_cleans_invalid_output(
    app, tmp_path, monkeypatch, mode, expected_reason
):
    source = _pdf_with_pages(tmp_path / f"source-{mode}.pdf", 2)
    source.write_bytes(source.read_bytes() + b" " * 40000)
    if mode == "ratio_too_low":
        source.write_bytes(source.read_bytes() + b" " * 400000)
    output = tmp_path / f"output-{mode}.pdf"

    def fake_sandbox(command, **_kwargs):
        if mode == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if mode == "nonzero":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        if mode == "unreadable":
            _output_path_from_gs_command(command).write_bytes(b"x" * 12000)
        elif mode == "page_loss":
            one_page = _pdf_with_pages(tmp_path / "one-page.pdf", 1)
            _output_path_from_gs_command(command).write_bytes(
                one_page.read_bytes() + b" " * 12000
            )
        elif mode == "larger":
            _output_path_from_gs_command(command).write_bytes(
                source.read_bytes() + b" " * 1000
            )
        elif mode == "too_small":
            _output_path_from_gs_command(command).write_bytes(
                _pdf_with_pages(tmp_path / "small.pdf", 2).read_bytes()
            )
        elif mode == "ratio_too_low":
            _output_path_from_gs_command(command).write_bytes(
                _pdf_with_pages(tmp_path / "low-ratio.pdf", 2).read_bytes()
                + b" " * 12000
            )
        elif mode == "insufficient_gain":
            _output_path_from_gs_command(command).write_bytes(
                source.read_bytes()[:-100]
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(compress_service, "run_in_sandbox", fake_sandbox)
    with app.app_context():
        result = compress_service.execute_ghostscript_validated(
            str(source),
            str(output),
            quality=80,
            dpi=100,
            expected_pages=2,
        )

    assert result.usable is False
    assert result.fallback_reason == expected_reason
    assert not output.exists()


def test_shared_ghostscript_executor_accepts_structurally_valid_tiny_output(
    app, tmp_path, monkeypatch
):
    source = _pdf_with_pages(tmp_path / 'source-valid-tiny.pdf', 2)
    source.write_bytes(source.read_bytes() + b' ' * 400000)
    candidate = _pdf_with_pages(tmp_path / 'candidate-valid-tiny.pdf', 2)
    output = tmp_path / 'output-valid-tiny.pdf'

    def fake_sandbox(command, **_kwargs):
        _output_path_from_gs_command(command).write_bytes(candidate.read_bytes())
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(compress_service, 'run_in_sandbox', fake_sandbox)
    with app.app_context():
        result = compress_service.execute_ghostscript_validated(
            str(source),
            str(output),
            quality=80,
            dpi=100,
            expected_pages=2,
        )

    assert result.usable is True
    assert result.fallback_reason is None
    assert output.exists()


def test_shared_ghostscript_executor_rejects_suspicious_content_loss(
    app, tmp_path, monkeypatch
):
    source = make_plain_pdf(tmp_path / 'source-with-content.pdf')
    source.write_bytes(source.read_bytes() + b' ' * 400000)
    output = tmp_path / 'output-without-content.pdf'

    def fake_sandbox(command, **_kwargs):
        reader = PdfReader(str(source))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_blank_page(
                width=float(page.mediabox.width),
                height=float(page.mediabox.height),
            )
        with _output_path_from_gs_command(command).open('wb') as handle:
            writer.write(handle)
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(compress_service, 'run_in_sandbox', fake_sandbox)
    with app.app_context():
        result = compress_service.execute_ghostscript_validated(
            str(source),
            str(output),
            quality=80,
            dpi=100,
            expected_pages=2,
        )

    assert result.usable is False
    assert result.fallback_reason == 'suspicious_content_mismatch'
    assert not output.exists()


def test_group_compression_uses_shared_executor(app, tmp_path, monkeypatch):
    source = _pdf_with_pages(tmp_path / "group-source.pdf", 2)
    output = tmp_path / "group-output.pdf"
    called = {}

    def fake_executor(input_pdf, output_pdf, quality, dpi, expected_pages=None):
        called["expected_pages"] = expected_pages
        shutil.copyfile(input_pdf, output_pdf)
        size = os.path.getsize(output_pdf)
        return compress_service.GhostscriptExecution(
            usable=True,
            fallback_reason=None,
            input_size=size + 1000,
            output_size=size,
            expected_pages=expected_pages,
            actual_pages=expected_pages,
            returncode=0,
        )

    monkeypatch.setattr(
        compress_service,
        "execute_ghostscript_validated",
        fake_executor,
    )
    with app.app_context():
        warnings = compress_service.comprimir_pdf_com_params(
            str(source),
            str(output),
            pages=[1, 2],
            quality=80,
            dpi=100,
        )

    assert called["expected_pages"] == 2
    assert output.exists()
    assert warnings.used_original is False


def test_group_rotation_is_applied_after_ghostscript(app, tmp_path, monkeypatch):
    source = _pdf_with_pages(tmp_path / 'rotation-source.pdf', 2)
    output = tmp_path / 'rotation-output.pdf'
    seen_input_rotations = []

    def fake_executor(input_pdf, output_pdf, quality, dpi, expected_pages=None):
        with pikepdf.open(input_pdf) as pdf:
            seen_input_rotations.append(
                [int(page.get('/Rotate', 0) or 0) for page in pdf.pages]
            )
        shutil.copyfile(input_pdf, output_pdf)
        size = os.path.getsize(output_pdf)
        return compress_service.GhostscriptExecution(
            usable=True,
            fallback_reason=None,
            input_size=size + 1000,
            output_size=size,
            expected_pages=expected_pages,
            actual_pages=expected_pages,
            returncode=0,
        )

    monkeypatch.setattr(
        compress_service,
        'execute_ghostscript_validated',
        fake_executor,
    )
    with app.app_context():
        warnings = compress_service.comprimir_pdf_com_params(
            str(source),
            str(output),
            pages=[1, 2],
            quality=80,
            dpi=100,
            rotations={1: 90},
        )

    assert seen_input_rotations == [[0, 0]]
    with pikepdf.open(output) as pdf:
        assert int(pdf.pages[0].get('/Rotate', 0) or 0) == 90
        assert int(pdf.pages[1].get('/Rotate', 0) or 0) == 0
    assert warnings.used_original is False
