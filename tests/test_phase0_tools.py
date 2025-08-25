import os

def test_gs_uses_safer_and_output(monkeypatch):
    from app.services import compress_service as cs
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R: pass
        return R()

    monkeypatch.setattr(cs, "run_in_sandbox", lambda cmd, **kw: fake_run(cmd, **kw))
    cs.compress_pdf("in.pdf", "out.pdf", profile="screen")

    cmd = captured["cmd"]
    assert "-sDEVICE=pdfwrite" in cmd
    assert "-dSAFER" in cmd
    assert any(x.startswith("-sOutputFile=") for x in cmd)
    assert cmd[-1] == "in.pdf"

def test_soffice_args(monkeypatch, tmp_path):
    from app.services import converter_service as conv
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R: pass
        return R()

    monkeypatch.setattr(conv, "run_in_sandbox", lambda cmd, **kw: fake_run(cmd, **kw))

    inp = tmp_path / "docx input.docx"
    inp.write_bytes(b"fake")
    outdir = tmp_path / "out"
    outdir.mkdir()

    # Não vamos checar o arquivo final (fake), só a linha de comando
    try:
        conv.convert_to_pdf(str(inp), str(outdir))
    except RuntimeError:
        # esperado, pois fake_run não gera arquivo; o objetivo aqui é validar flags.
        pass

    cmd = captured["cmd"]
    assert cmd[0] == (os.environ.get("SOFFICE_BIN") or os.environ.get("LIBREOFFICE_BIN") or "soffice")
    assert "--headless" in cmd
    assert "--safe-mode" in cmd
    assert "--convert-to" in cmd and "pdf" in cmd
    assert "--outdir" in cmd
    assert str(inp) in cmd