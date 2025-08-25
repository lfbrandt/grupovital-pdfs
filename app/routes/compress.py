# app/routes/compress.py
import os, json
from flask import Blueprint, request, jsonify, send_file, after_this_request, current_app, abort
from werkzeug.utils import secure_filename  # mantido se for usado em outros pontos
from ..services.compress_service import comprimir_pdf, USER_PROFILES
from .. import limiter

# Padrão A: url_prefix completo
compress_bp = Blueprint("compress", __name__, url_prefix="/api/compress")

# POST /api/compress  (com e sem barra)
@compress_bp.route('', methods=['POST'])
@compress_bp.route('/', methods=['POST'])
@limiter.limit("5 per minute")
def compress():
    """
    Recebe:
      - file: PDF
      - pages: JSON list[int] (1-based) com a ORDEM das páginas (DnD) — opcional
      - rotations: JSON list[int] OU dict[str|int,int] — opcional
      - profile: str (mais-leve|equilibrio|alta-qualidade|sem-perdas) — opcional
      - modificacoes: JSON (opcional) — repassado ao serviço

    Retorna:
      - PDF inline (para preview/download pelo front)
    """
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400
    if not f.filename:
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    # parâmetros opcionais
    mods = request.form.get('modificacoes')
    rotations_raw = request.form.get('rotations')
    pages_raw = request.form.get('pages')
    profile = request.form.get('profile', 'equilibrio')  # equilibrio, mais-leve, alta-qualidade, sem-perdas

    # -------- modificacoes --------
    modificacoes = None
    if mods:
        try:
            modificacoes = json.loads(mods)
        except json.JSONDecodeError:
            return jsonify({'error': 'modificacoes deve ser JSON válido'}), 400

    # -------- rotations --------
    rotations = None
    if rotations_raw:
        try:
            rotations = json.loads(rotations_raw)  # aceita lista [0,90,...] ou dict {"0":90,"3":270}
            if isinstance(rotations, dict):
                rotations = {int(k): int(v) for k, v in rotations.items()}
            elif isinstance(rotations, list):
                rotations = [int(v) for v in rotations]
            else:
                return jsonify({'error': 'rotations deve ser lista ou objeto JSON'}), 400
        except (json.JSONDecodeError, ValueError, TypeError):
            return jsonify({'error': 'rotations deve ser JSON válido (lista ou objeto)'}), 400

    # -------- pages (ordem DnD) --------
    pages = None
    if pages_raw:
        try:
            pages_val = json.loads(pages_raw)
        except json.JSONDecodeError:
            return jsonify({'error': 'pages deve ser JSON válido'}), 400

        if pages_val is not None:
            if not isinstance(pages_val, list):
                return jsonify({'error': 'pages deve ser uma lista de inteiros (1-based)'}), 400
            try:
                pages = [int(p) for p in pages_val]
            except (ValueError, TypeError):
                return jsonify({'error': 'pages deve conter apenas inteiros'}), 400

    try:
        out_path = comprimir_pdf(
            f,
            pages=pages,
            rotations=rotations,
            modificacoes=modificacoes,
            profile=profile
        )

        @after_this_request
        def cleanup(resp):
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            return resp

        return send_file(
            out_path,
            mimetype='application/pdf',
            as_attachment=False,
            download_name=os.path.basename(out_path)
        )

    except Exception:
        current_app.logger.exception("Erro comprimindo PDF")
        abort(500)

# GET /api/compress/profiles
@compress_bp.get('/profiles')
def list_profiles():
    """Endpoint opcional para o front exibir nomes e descrições das opções."""
    items = {k: {'label': v['label'], 'hint': v['hint']} for k, v in USER_PROFILES.items()}
    return jsonify(items)