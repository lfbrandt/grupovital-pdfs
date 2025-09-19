# app/routes/dev.py
import os
import shutil
import subprocess
from flask import Blueprint, render_template, jsonify

dev_bp = Blueprint("dev", __name__)

# ---------------------------------------------------------------------
# Página de desenvolvimento que você já tinha
# ---------------------------------------------------------------------
@dev_bp.route("/dev/editor")
def dev_editor():
    return render_template("dev-editor.html")

# ---------------------------------------------------------------------
# Health check simples (sempre disponível)
# ---------------------------------------------------------------------
@dev_bp.get("/healthz")
def healthz():
    # leve, não toca em sessão/FS; usado por Render/uptime
    return jsonify(status="ok", version=os.getenv("APP_VERSION", "unknown"))

# ---------------------------------------------------------------------
# Diagnósticos (somente se ENABLE_DIAG=1 estiver setado no ambiente)
# Use temporariamente no Render para verificar binários.
# ---------------------------------------------------------------------
def _diag_enabled() -> bool:
    return os.getenv("ENABLE_DIAG", "0") == "1"

def _run(cmd, timeout=3):
    """Executa um binário e retorna rc/out/err sem explodir a app."""
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
        return {"rc": p.returncode, "out": (p.stdout or "").strip(), "err": (p.stderr or "").strip()}
    except Exception as e:
        return {"rc": -1, "out": "", "err": str(e)[:200]}

@dev_bp.get("/diag/gs")
def diag_gs():
    if not _diag_enabled():
        return jsonify(error="disabled"), 404

    gs_bin = os.getenv("GHOSTSCRIPT_BIN") or shutil.which("gs") or "gs"
    res = _run([gs_bin, "-v"])
    info = {
        "bin": gs_bin,
        "ok": res["rc"] == 0,
        "version": (res["out"] or res["err"]).splitlines()[0] if (res["out"] or res["err"]) else None,
    }
    if res["rc"] != 0:
        info["err_tail"] = (res["err"] or res["out"]).splitlines()[-5:]
    return jsonify(info)

@dev_bp.get("/diag/soffice")
def diag_soffice():
    if not _diag_enabled():
        return jsonify(error="disabled"), 404

    soffice_bin = os.getenv("SOFFICE_BIN") or shutil.which("soffice") or "soffice"
    res = _run([soffice_bin, "--version"])
    info = {
        "bin": soffice_bin,
        "ok": res["rc"] == 0,
        "version": (res["out"] or res["err"]).splitlines()[0] if (res["out"] or res["err"]) else None,
    }
    if res["rc"] != 0:
        info["err_tail"] = (res["err"] or res["out"]).splitlines()[-5:]
    return jsonify(info)