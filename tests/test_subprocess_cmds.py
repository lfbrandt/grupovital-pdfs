import os
from io import BytesIO
from werkzeug.datastructures import FileStorage
from app import create_app
from app.services import compress_service, converter_service
from PyPDF2 import PdfWriter


def _simple_pdf():
    w = PdfWriter()
    w.add_blank_page(width=10, height=10)
    buf = BytesIO()
    w.write(buf)
    buf.seek(0)
    return buf


def test_compress_runs_ghostscript(monkeypatch, tmp_path):
    """
    Verifies that comprimir_pdf calls Ghostscript with the resolved binary and
    the correct pdfwrite device flag.

    The binary is whatever _get_gs_cmd() resolves to (may be a full path on
    Windows, e.g. 'C:\\...\\gswin64c.exe').  We assert only on the device flag
    which is stable regardless of platform.

    Note: -dPDFSETTINGS is intentionally NOT used; the service passes image
    parameters via setdistillerparams (PostScript inline) instead.
    """
    import subprocess as _subprocess
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path

    # Reset GS binary cache so monkeypatching shutil.which takes effect
    monkeypatch.setattr(compress_service, "_GS_CMD_CACHE", None)

    called = {}

    def fake_run(cmd, **kwargs):
        called["cmd"] = cmd
        for part in cmd:
            if str(part).startswith("-sOutputFile="):
                open(part.split("=", 1)[1], "wb").close()
        return _subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        file = FileStorage(stream=_simple_pdf(), filename="a.pdf")
        compress_service.comprimir_pdf(file)

    assert called, "subprocess.run was never called — GS command not invoked"
    assert "-sDEVICE=pdfwrite" in called["cmd"], (
        "Expected -sDEVICE=pdfwrite in GS args; got: " + str(called["cmd"])
    )
    # Confirm the old -dPDFSETTINGS flag is NOT present (service uses setdistillerparams)
    assert not any(str(a).startswith("-dPDFSETTINGS") for a in called["cmd"]), (
        "-dPDFSETTINGS should not be used; setdistillerparams is used instead"
    )


def test_planilha_uses_libreoffice(monkeypatch, tmp_path):
    import subprocess as _subprocess
    app = create_app()
    app.config["UPLOAD_FOLDER"] = tmp_path
    called = {}

    def fake_run(cmd, **kwargs):
        called["cmd"] = cmd
        # converter_service expects an output file in --outdir (cmd[-2]) named
        # after the input (cmd[-1]) with the extension replaced by .pdf
        out_dir  = cmd[cmd.index('--outdir') + 1]
        in_path  = cmd[-1]
        out_name = os.path.splitext(os.path.basename(in_path))[0] + ".pdf"
        open(os.path.join(out_dir, out_name), "wb").close()
        return _subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)

    with app.app_context():
        csv = BytesIO(b"a,b\n1,2")
        file = FileStorage(stream=csv, filename="t.csv")
        converter_service.converter_planilha_para_pdf(file)

    assert "--headless" in called["cmd"]
    # The binary is whatever _soffice_bin() resolved to — assert only the flag
    # that is stable across platforms, not the binary name/path itself.
