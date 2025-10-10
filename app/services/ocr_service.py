# app/services/ocr_service.py
# -*- coding: utf-8 -*-
"""
OCR de PDFs usando ocrmypdf.

• Windows: subprocess direto (evita quoting/rc=2) + injeta Tesseract no PATH/TESSDATA.
• Linux/macOS: executa em sandbox (sem rede, limites CPU/RAM/tempo).
• Sanitiza ENTRADA/SAÍDA com pikepdf (remove JS/anotações/OpenAction).
• Flags padrão seguras: --skip-text, --deskew, --rotate-pages, --optimize 2.
• Usa --clean apenas se 'unpaper' estiver presente (ou se OCR_CLEAN=1).

NOVO:
• Detecção de assinatura digital e estratégia configurável (block | ask | invalidate).
• Se faltar pngquant, usa --optimize 1 (com retry automático).
• Fallback de idiomas: usa apenas os instalados no Tesseract (mantendo a ordem).

ENV:
  OCR_BIN                 -> comando ocrmypdf (aceita "python -m ocrmypdf")
  OCR_LANGS               -> línguas (ex.: "por+eng"; default "por+eng")
  OCR_TIMEOUT             -> timeout em segundos (default 300)
  OCR_MEM_MB              -> limite de RAM em MB (default 1024)
  OCR_JOBS                -> paralelismo (default "1")
  OCR_CLEAN               -> "1"/"0" para forçar habilitar/desabilitar --clean
  OCR_ON_SIGNED           -> "block" (padrão) | "ask" | "invalidate"
  TESSERACT_PREFIX / TESSERACT_PATH -> diretório do Tesseract (Windows)
  TESSDATA_PREFIX         -> diretório das tessdata (Windows)
"""
from __future__ import annotations

import os
import sys
import shlex
import shutil
import tempfile
import logging
import subprocess
from typing import Optional, Set, List

import pikepdf  # <- usado p/ checar assinatura
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import BadRequest

from ..utils.limits import enforce_pdf_page_limit
from .sanitize_service import sanitize_pdf

logger = logging.getLogger(__name__)

__all__ = ["ocr_pdf_path", "ocr_upload_file"]


# ---------------- helpers ----------------
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default

def _env_bool(name: str, default: bool) -> bool:
    val = (os.environ.get(name) or "").strip().lower()
    if not val:
        return default
    return val in {"1", "true", "t", "yes", "y", "on"}

def _env_str(name: str, default: str) -> str:
    val = (os.environ.get(name) or "").strip()
    return val if val else default

def _unique_tmp(suffix: str = ".pdf") -> str:
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path

def _ensure_parent_dir(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)

def _resolve_ocr_cmd() -> List[str]:
    env_bin = (os.environ.get("OCR_BIN") or "").strip()
    if env_bin:
        return shlex.split(env_bin)
    if os.name == "nt":
        # garante que use o Python do venv no Windows
        return [sys.executable, "-m", "ocrmypdf"]
    exe = shutil.which("ocrmypdf")
    return [exe or "ocrmypdf"]

# ---- Tesseract (Windows) ----
def _find_tesseract_dir() -> Optional[str]:
    hint = os.environ.get("TESSERACT_PREFIX") or os.environ.get("TESSERACT_PATH")
    if hint and os.path.isfile(os.path.join(hint, "tesseract.exe")):
        return os.path.abspath(hint)
    for p in (r"C:\Program Files\Tesseract-OCR", r"C:\Program Files (x86)\Tesseract-OCR"):
        if os.path.isfile(os.path.join(p, "tesseract.exe")):
            return p
    for p in (os.environ.get("PATH") or "").split(os.pathsep):
        if os.path.isfile(os.path.join(p.strip('"'), "tesseract.exe")):
            return p
    for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if root:
            guess = os.path.join(root, "Tesseract-OCR")
            if os.path.isfile(os.path.join(guess, "tesseract.exe")):
                return guess
    return None

def _ensure_win_paths(env: dict) -> None:
    if os.name != "nt":
        return
    # Tesseract
    tdir = _find_tesseract_dir()
    if tdir and tdir not in (env.get("PATH") or ""):
        env["PATH"] = tdir + os.pathsep + env.get("PATH", "")
        env.setdefault("TESSDATA_PREFIX", os.path.join(tdir, "tessdata"))
        logger.info("Tesseract adicionado ao PATH em runtime: %s", tdir)
    # Chocolatey (possível unpaper/pngquant)
    choco_bin = r"C:\ProgramData\chocolatey\bin"
    if os.path.isdir(choco_bin) and choco_bin not in (env.get("PATH") or ""):
        env["PATH"] = choco_bin + os.pathsep + env.get("PATH", "")

def _inject_tesseract_into_env(env: dict) -> dict:
    if os.name != "nt":
        return env
    # Se já tem tesseract visível, só garanta TESSDATA_PREFIX
    if shutil.which("tesseract", path=env.get("PATH", "")):
        if "TESSDATA_PREFIX" not in env:
            which = shutil.which("tesseract", path=env.get("PATH", ""))
            if which:
                base = os.path.dirname(which)
                td = os.path.join(base, "tessdata")
                if os.path.isdir(td):
                    env["TESSDATA_PREFIX"] = td
        return env
    _ensure_win_paths(env)
    if not shutil.which("tesseract", path=env.get("PATH", "")):
        logger.warning("Tesseract não encontrado nos caminhos padrão. "
                       "Instale-o (ex.: UB-Mannheim) e/ou ajuste PATH/TESSDATA_PREFIX.")
    return env

# ---- unpaper detection ----
def _has_unpaper() -> bool:
    if shutil.which("unpaper"):
        return True
    for p in (
        r"C:\ProgramData\chocolatey\bin\unpaper.exe",
        r"C:\Program Files\unpaper\unpaper.exe",
    ):
        if os.path.exists(p):
            return True
    return False

# ---- pngquant detection (para --optimize 2/3) ----
def _has_pngquant(env_path: str) -> bool:
    return shutil.which("pngquant", path=env_path or os.environ.get("PATH", "")) is not None

# ---- assinatura digital ----
def _pdf_has_digital_signature(path: str) -> bool:
    """
    Heurística: procura campos /Sig no AcroForm e marcadores típicos (/ByteRange, /Sig).
    Não altera o arquivo.
    """
    try:
        with pikepdf.open(path) as pdf:
            af = pdf.root.get("/AcroForm", None)
            if isinstance(af, pikepdf.Dictionary):
                fields = af.get("/Fields", [])
                for ref in fields:
                    try:
                        fld = ref.get_object()
                        ft = fld.get("/FT", None)
                        if ft and str(ft) == "/Sig":
                            return True
                    except Exception:
                        pass
        # fallback rápido: varre cabeçalho do arquivo por palavras-chave
        with open(path, "rb") as fh:
            head = fh.read(256 * 1024)  # 256 KB
            return (b"/ByteRange" in head) or (b"/Sig" in head)
    except Exception:
        return False

# ---- idiomas do Tesseract ----
def _available_tesseract_langs(env: dict) -> Set[str]:
    try:
        res = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=15, check=False, env=env
        )
        data = (res.stdout or res.stderr or "")
        langs = set()
        for line in data.splitlines():
            line = line.strip()
            if not line or line.startswith("List of available languages"):
                continue
            if len(line) <= 16:
                langs.add(line)
        return langs
    except Exception:
        return set()

def _select_installed_langs(requested: str, env: dict) -> str:
    req = [p.strip() for p in requested.split("+") if p.strip()]
    if not req:
        return requested
    av = _available_tesseract_langs(env)
    if not av:
        logger.warning("Não foi possível listar idiomas do Tesseract; seguindo com '%s'.", requested)
        return requested
    chosen = [l for l in req if l in av]
    missing = [l for l in req if l not in av]
    if missing:
        logger.warning("Idiomas ausentes no Tesseract: %s. Usando disponíveis: %s",
                       ", ".join(missing) or "-", "+".join(chosen) or "-")
    if not chosen:
        raise BadRequest(
            "Pacotes de idioma do Tesseract ausentes. "
            f"Solicitado: {requested}; instalados: {', '.join(sorted(av)) or '-'}."
        )
    return "+".join(chosen)


def _run_ocr(args: List[str], *, timeout: int, mem_mb: int):
    if os.name == "nt":
        env = _inject_tesseract_into_env(os.environ.copy())
        _ensure_win_paths(env)
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    else:
        from .sandbox import run_in_sandbox
        return run_in_sandbox(args, timeout=timeout, cpu_seconds=timeout, mem_mb=mem_mb)


# ---------------- núcleo ----------------
def ocr_pdf_path(
    in_path: str,
    out_path: str,
    *,
    lang: Optional[str] = None,
    force: bool = False,
    skip_text: bool = True,
    optimize: int = 2,
    deskew: bool = True,
    rotate_pages: bool = True,
    clean: bool = True,
    jobs: Optional[int] = None,
    timeout: Optional[int] = None,
    mem_mb: Optional[int] = None,
    allow_invalidate_sig: Optional[bool] = None,  # <- novo
) -> str:
    """
    Executa OCR no PDF informado. Sanitiza entrada/saída.

    • Se o PDF tiver assinatura digital:
        - OCR_ON_SIGNED=block (padrão): recusa e explica.
        - OCR_ON_SIGNED=ask: recusa com mensagem própria p/ confirmação no front.
        - OCR_ON_SIGNED=invalidate ou allow_invalidate_sig=True: prossegue com
          --invalidate-digital-signatures (a assinatura ficará inválida).
    """
    if not os.path.isfile(in_path):
        raise BadRequest("Arquivo de entrada do OCR não encontrado.")
    if os.path.abspath(in_path) == os.path.abspath(out_path):
        raise BadRequest("out_path deve ser diferente do in_path.")

    _ensure_parent_dir(out_path)
    enforce_pdf_page_limit(in_path, label=os.path.basename(in_path))

    # Estratégia para PDFs assinados
    on_signed = _env_str("OCR_ON_SIGNED", "block").lower()
    if allow_invalidate_sig is None:
        allow_invalidate_sig = on_signed in {"invalidate", "force", "true", "1", "yes"}

    # Sanitiza entrada (remove JS etc) — não remove assinatura
    safe_in = _unique_tmp(".pdf")
    try:
        sanitize_pdf(in_path, safe_in)
        logger.info("Sanitização pikepdf OK (entrada).")
    except Exception:
        shutil.copyfile(in_path, safe_in)

    # Se o arquivo (original) tem assinatura e a política não permite invalidar,
    # já interrompe com mensagem clara.
    if _pdf_has_digital_signature(in_path) and not allow_invalidate_sig:
        tip = (
            "Esse PDF possui ASSINATURA DIGITAL. Qualquer alteração (incluindo OCR) "
            "invalida a assinatura. Para continuar mesmo assim, ative a opção "
            "'invalidar assinatura' no aplicativo, defina OCR_ON_SIGNED=invalidate no .env "
            "ou chame o serviço com allow_invalidate_sig=True."
        )
        if on_signed == "ask":
            raise BadRequest("PDF assinado detectado. Ação requerida: confirmar invalidação da assinatura para prosseguir com o OCR. " + tip)
        raise BadRequest("OCR não executado para preservar a assinatura digital. " + tip)

    cmd = _resolve_ocr_cmd()
    # monta env para pré-checagens
    env_for_check = os.environ.copy()
    if os.name == "nt":
        _ensure_win_paths(env_for_check)

    langs_requested = (lang or os.environ.get("OCR_LANGS") or "por+eng").strip()
    langs = _select_installed_langs(langs_requested, env_for_check)

    to = timeout if isinstance(timeout, int) else _env_int("OCR_TIMEOUT", 300)
    mem = mem_mb if isinstance(mem_mb, int) else _env_int("OCR_MEM_MB", 1024)
    j = str(jobs if jobs else os.environ.get("OCR_JOBS", "1") or "1").strip()
    try:
        opt_req = max(0, min(3, int(optimize)))
    except Exception:
        opt_req = 2

    # Permitir override via .env (OCR_CLEAN=0/1)
    if os.environ.get("OCR_CLEAN") is not None:
        clean = _env_bool("OCR_CLEAN", clean)

    # Se pediu limpeza mas não tem unpaper, desabilita automaticamente
    if clean and not _has_unpaper():
        logger.warning("unpaper não encontrado. Prosseguindo sem --clean.")
        clean = False

    # Pré-checagem pngquant
    opt = opt_req
    if opt_req >= 2 and not _has_pngquant(env_for_check.get("PATH", "")):
        logger.warning("pngquant não encontrado no PATH. Usando --optimize 1.")
        opt = 1

    def build_args(opt_level: int, invalidate_sig: bool) -> List[str]:
        a = cmd + [
            "--output-type", "pdf",
            "--optimize", str(opt_level),
            "--jobs", j,
            "--language", langs,
        ]
        if invalidate_sig:
            a.append("--invalidate-digital-signatures")
        a.append("--rotate-pages" if rotate_pages else "--no-rotate-pages")
        if deskew:
            a.append("--deskew")
        if clean:
            a.append("--clean")  # só entra se unpaper estiver disponível
        if force:
            a.append("--force-ocr")
        elif skip_text:
            a.append("--skip-text")
        a.extend([safe_in, out_path])
        return a

    args = build_args(opt, allow_invalidate_sig)
    logger.info("OCR cmd: %r", args)

    try:
        proc = _run_ocr(args, timeout=to, mem_mb=mem)
    except subprocess.TimeoutExpired:
        try: os.remove(safe_in)
        except Exception: pass
        raise BadRequest("OCR excedeu o tempo limite (timeout).")
    except FileNotFoundError as e:
        try: os.remove(safe_in)
        except Exception: pass
        raise BadRequest(f"Falha ao iniciar OCR: {e}")
    except Exception as e:
        try: os.remove(safe_in)
        except Exception: pass
        raise BadRequest(f"OCR falhou ao iniciar: {e}")

    try:
        rc = getattr(proc, "returncode", 1)
        out = (getattr(proc, "stdout", "") or "")
        err = (getattr(proc, "stderr", "") or "")
        if rc != 0 or not os.path.exists(out_path):
            msg = (err.strip() or out.strip())[:1500]
            low = msg.lower()

            # Caso o usuário não tenha permitido, o ocrmypdf devolve DigitalSignatureError
            if "digital signature" in low and not allow_invalidate_sig:
                raise BadRequest(
                    "PDF assinado detectado. O OCR foi interrompido para não invalidar a assinatura. "
                    "Se desejar prosseguir, habilite a opção de invalidar assinatura (frontend) "
                    "ou chame com allow_invalidate_sig=True / OCR_ON_SIGNED=invalidate."
                )

            # Fallback automático se optimize>=2 e falha indica pngquant
            need_pngquant = ("pngquant" in low) or rc == 3
            if opt >= 2 and need_pngquant:
                logger.warning("Falha possivelmente por pngquant. Tentando novamente com --optimize 1.")
                args2 = build_args(1, allow_invalidate_sig)
                proc2 = _run_ocr(args2, timeout=to, mem_mb=mem)
                rc2 = getattr(proc2, "returncode", 1)
                out2 = (getattr(proc2, "stdout", "") or "")
                err2 = (getattr(proc2, "stderr", "") or "")
                if rc2 == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                    logger.info("OCR concluído (fallback optimize=1).")
                else:
                    msg2 = (err2.strip() or out2.strip())[:1500]
                    raise BadRequest(f"OCR falhou (fallback optimize=1; rc={rc2}). {msg2}")

            elif "error opening data file" in low or "failed loading language" in low or "does not have language data" in low:
                raise BadRequest(
                    "Pacotes de idioma do Tesseract ausentes. "
                    f"Solicitado: {langs_requested}. Em uso: {langs}. "
                    "Instale os idiomas necessários (ex.: 'por.traineddata' em tessdata) "
                    "ou ajuste OCR_LANGS."
                )
            elif ("tesseract" in low and
                  ("not found" in low or "is not recognized" in low or "não é reconhecido" in low)):
                raise BadRequest(
                    "Tesseract não encontrado. Instale (UB-Mannheim) e ponha no PATH "
                    "ou defina TESSDATA_PREFIX para a pasta tessdata."
                )
            elif rc == 2 and "usage" in low:
                raise BadRequest("Falha ao chamar o OCR (uso inválido). Verifique ocrmypdf/Tesseract.")
            else:
                raise BadRequest(f"OCR falhou (rc={rc}). {msg}")

    finally:
        try:
            os.remove(safe_in)
        except Exception:
            pass

    # Sanitiza saída
    enforce_pdf_page_limit(out_path, label="PDF pós-OCR")
    try:
        safe_out = _unique_tmp(".pdf")
        sanitize_pdf(out_path, safe_out)
        os.replace(safe_out, out_path)
    except Exception:
        pass

    return out_path


def ocr_upload_file(
    upload: FileStorage,
    *,
    lang: Optional[str] = None,
    force: bool = False,
    skip_text: bool = True,
    optimize: int = 2,
    deskew: bool = True,
    rotate_pages: bool = True,
    clean: bool = True,
    jobs: Optional[int] = None,
    timeout: Optional[int] = None,
    mem_mb: Optional[int] = None,
    allow_invalidate_sig: Optional[bool] = None,  # <- novo
) -> str:
    """Recebe um upload, executa OCR e retorna o caminho absoluto do PDF output."""
    from ..utils.config_utils import validate_upload, ensure_upload_folder_exists, secure_filename

    try:
        validate_upload(upload, allowed_exts={"pdf"}, allowed_mimetypes={"application/pdf"})
    except TypeError:
        validate_upload(upload, {"pdf"}, {"application/pdf"})

    upload_dir = ensure_upload_folder_exists()
    in_name = secure_filename(upload.filename or "arquivo.pdf")
    in_tmp = os.path.join(upload_dir, f"up_{in_name}")
    upload.save(in_tmp)

    base_out = os.path.join(upload_dir, f"ocr_{os.path.splitext(in_name)[0]}")
    out_tmp = base_out + ".pdf"
    i = 1
    while os.path.exists(out_tmp):
        out_tmp = f"{base_out}_{i}.pdf"
        i += 1

    try:
        ocr_pdf_path(
            in_tmp, out_tmp,
            lang=lang, force=force, skip_text=skip_text, optimize=optimize,
            deskew=deskew, rotate_pages=rotate_pages, clean=clean,
            jobs=jobs, timeout=timeout, mem_mb=mem_mb,
            allow_invalidate_sig=allow_invalidate_sig,
        )
    finally:
        try:
            os.remove(in_tmp)
        except Exception:
            pass

    return out_tmp