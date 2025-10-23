# app/routes/split.py
# -*- coding: utf-8 -*-
import os
import uuid
import json
import zipfile
from flask import (
    Blueprint, request, jsonify, send_file,
    render_template, current_app, after_this_request
)
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from werkzeug.utils import secure_filename  # pode ser útil em evoluções

from ..services.split_service import dividir_pdf
from ..utils.preview_utils import preview_pdf
from .. import limiter
from ..utils.stats import record_job_event  # (7.1) métricas

split_bp = Blueprint("split", __name__, url_prefix="/api/split")


# ------------------------ helpers ------------------------

def _json_error(msg: str, status: int = 400):
    resp = jsonify({"error": msg})
    resp.status_code = status
    return resp


def _parse_pages(raw):
    """
    Aceita:
      - JSON list[int] 1-based, ex.: [1,3,5]
      - string com faixas/CSV, ex.: "1-3, 5, 7-8"
    Retorna list[int] 1-based (sem duplicatas) ou None.
    """
    if raw is None or raw == "":
        return None

    # Tenta JSON primeiro
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                out = []
                for p in parsed:
                    n = int(p)
                    if n >= 1:
                        out.append(n)
                # dedupe preservando ordem
                seen, dedup = set(), []
                for n in out:
                    if n not in seen:
                        seen.add(n)
                        dedup.append(n)
                return dedup or None
        except Exception:
            pass  # cai para o parser de texto

    # Texto "1,2,5-7"
    pages = []
    try:
        parts = str(raw).replace(" ", "").split(",")
        for part in parts:
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                a, b = int(a), int(b)
                if a <= 0 or b <= 0 or b < a:
                    raise BadRequest("Faixa de páginas inválida.")
                pages.extend(range(a, b + 1))
            else:
                n = int(part)
                if n <= 0:
                    raise BadRequest("Número de página inválido.")
                pages.append(n)
    except BadRequest:
        raise
    except Exception:
        raise BadRequest("Formato de páginas inválido.")

    seen, out = set(), []
    for p in pages:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out or None


def _parse_rotations(raw):
    """
    Aceita:
      - JSON dict {"1":90,"5":270}
      - JSON list  [0,90,0,270] (índice 0 => página 1)
      - CSV simplista "0,90,0,270"
    Retorna dict[int,int] com ângulos normalizados {1:90,...} (apenas 0/90/180/270).
    """
    if raw is None or raw == "":
        return {}

    # JSON
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                out = {}
                for k, v in parsed.items():
                    p = int(k)
                    deg = int(v) % 360
                    if deg < 0:
                        deg += 360
                    if deg not in (0, 90, 180, 270):
                        deg = (round(deg / 90) * 90) % 360
                    if p >= 1 and deg != 0:
                        out[p] = deg
                return out
            if isinstance(parsed, list):
                out = {}
                for i, v in enumerate(parsed):
                    p = i + 1
                    deg = int(v) % 360
                    if deg < 0:
                        deg += 360
                    if deg not in (0, 90, 180, 270):
                        deg = (round(deg / 90) * 90) % 360
                    if deg != 0:
                        out[p] = deg
                return out
        except Exception:
            pass  # cai para CSV

    # CSV
    out = {}
    try:
        parts = [s.strip() for s in str(raw).split(",")]
        for i, r in enumerate(parts):
            if not r:
                continue
            deg = int(r) % 360
            if deg < 0:
                deg += 360
            if deg not in (0, 90, 180, 270):
                deg = (round(deg / 90) * 90) % 360
            if deg != 0:
                out[i + 1] = deg
    except Exception:
        raise BadRequest("Formato de rotations inválido.")
    return out


def _parse_mods(raw):
    """
    Espera JSON:
      {
        "3": {"crop": {"x":0.1,"y":0.2,"w":0.5,"h":0.4}},
        "7": {"crop": {...}}
      }
    x,y,w,h normalizados (0..1) com origem no topo-esquerda.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise BadRequest("modificacoes deve ser JSON válido.")
    if not isinstance(raw, dict):
        return None

    mods = {}
    for k, v in raw.items():
        try:
            p = int(k)
            if p < 1 or not isinstance(v, dict):
                continue
            crop = v.get("crop")
            if crop and all(t in crop for t in ("x", "y", "w", "h")):
                x = float(crop["x"]); y = float(crop["y"])
                w = float(crop["w"]); h = float(crop["h"])
                if 0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0:
                    mods[p] = {"crop": {"x": x, "y": y, "w": w, "h": h}}
        except Exception:
            continue
    return mods or None


# ------------------------ endpoints ------------------------

@split_bp.route("", methods=["POST"])
@split_bp.route("/", methods=["POST"])
@limiter.limit("5 per minute")
def split():
    try:
        if "file" not in request.files:
            return _json_error("Nenhum arquivo enviado.", 400)
        file = request.files["file"]
        if not file.filename:
            return _json_error("Nenhum arquivo selecionado.", 400)

        # Params opcionais
        pages = _parse_pages(request.form.get("pages"))
        rotations = _parse_rotations(request.form.get("rotations"))
        modificacoes = _parse_mods(
            request.form.get("modificacoes") or request.form.get("modifications")
        )

        pdf_paths = dividir_pdf(
            file,
            pages=pages,
            rotations=rotations,
            modificacoes=modificacoes,
        )

        # Com pages -> único PDF; Sem pages -> ZIP contendo 1 PDF por página
        if pages:
            output_path = pdf_paths[0]

            # ===== (7.1) MÉTRICAS =====
            try:
                bytes_out = os.path.getsize(output_path) if os.path.exists(output_path) else None
            except Exception:
                bytes_out = None
            try:
                bytes_in = int(request.content_length) if request.content_length else None
            except Exception:
                bytes_in = None
            try:
                record_job_event(
                    route="/api/split",
                    action="split",
                    bytes_in=bytes_in,
                    bytes_out=bytes_out,
                    files_out=1,
                )
            except Exception:
                pass
            # ===========================

            @after_this_request
            def cleanup_single(response):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                return response

            return send_file(
                output_path,
                as_attachment=True,
                download_name="paginas_selecionadas.pdf",
                mimetype="application/pdf",
            )

        # ZIP
        zip_filename = f"{uuid.uuid4().hex}.zip"
        zip_path = os.path.join(current_app.config["UPLOAD_FOLDER"], zip_filename)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
            for path in pdf_paths:
                zipf.write(path, os.path.basename(path))

        # ===== (7.1) MÉTRICAS =====
        try:
            bytes_out = os.path.getsize(zip_path) if os.path.exists(zip_path) else None
        except Exception:
            bytes_out = None
        try:
            bytes_in = int(request.content_length) if request.content_length else None
        except Exception:
            bytes_in = None
        try:
            record_job_event(
                route="/api/split",
                action="split",
                bytes_in=bytes_in,
                bytes_out=bytes_out,
                files_out=len(pdf_paths),
            )
        except Exception:
            pass
        # ===========================

        @after_this_request
        def cleanup_zip(response):
            try:
                os.remove(zip_path)
                for path in pdf_paths:
                    os.remove(path)
            except OSError:
                pass
            return response

        return send_file(
            zip_path,
            as_attachment=True,
            download_name="paginas_divididas.zip",
            mimetype="application/zip",
        )

    except RequestEntityTooLarge:
        return _json_error("Arquivo muito grande (MAX_CONTENT_LENGTH).", 413)
    except BadRequest as e:
        return _json_error(e.description or "Requisição inválida.", 422)
    except Exception:
        current_app.logger.exception("Erro dividindo PDF")
        return _json_error("Falha ao dividir o PDF.", 500)


@split_bp.route("", methods=["GET"])
@split_bp.route("/", methods=["GET"])
def split_form():
    return render_template("split.html")


@split_bp.post("/preview")
def preview_split():
    try:
        if "file" not in request.files:
            return _json_error("Nenhum arquivo enviado.", 400)
        file = request.files["file"]
        thumbs = preview_pdf(file)
        return jsonify({"thumbnails": thumbs})
    except BadRequest as e:
        return _json_error(e.description or "Requisição inválida.", 422)
    except RequestEntityTooLarge:
        return _json_error("Arquivo muito grande (MAX_CONTENT_LENGTH).", 413)
    except Exception:
        current_app.logger.exception("Erro no preview de split")
        return _json_error("Falha ao gerar preview.", 500)