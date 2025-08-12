import os, json
from flask import Blueprint, request, jsonify, send_file, after_this_request, current_app, abort
from werkzeug.utils import secure_filename
from ..services.compress_service import comprimir_pdf, USER_PROFILES
from .. import limiter

compress_bp = Blueprint('compress', __name__)

@compress_bp.route('/compress', methods=['POST'])
@limiter.limit("5 per minute")
def compress():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400
    if not f.filename:
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    # parâmetros opcionais
    mods = request.form.get('modificacoes')
    rotations_raw = request.form.get('rotations')
    profile = request.form.get('profile', 'equilibrio')  # nomes PT-BR: equilibrio, mais-leve, alta-qualidade, sem-perdas

    modificacoes = None
    if mods:
        try:
            modificacoes = json.loads(mods)
        except json.JSONDecodeError:
            return jsonify({'error': 'modificacoes deve ser JSON válido'}), 400

    rotations = None
    if rotations_raw:
        try:
            rotations = json.loads(rotations_raw)  # aceita lista [0,90,...] ou dict {"0":90,"3":270}
            # normaliza chaves numéricas caso venha como dict com strings
            if isinstance(rotations, dict):
                rotations = {int(k): int(v) for k, v in rotations.items()}
        except json.JSONDecodeError:
            return jsonify({'error': 'rotations deve ser JSON válido'}), 400

    try:
        out_path = comprimir_pdf(f, rotations=rotations, modificacoes=modificacoes, profile=profile)

        @after_this_request
        def cleanup(resp):
            try: 
                if os.path.exists(out_path):
                    os.remove(out_path)
            except OSError:
                pass
            return resp

        # Cabeçalhos e retorno do arquivo para preview/download
        return send_file(out_path, mimetype='application/pdf', as_attachment=False)

    except Exception:
        current_app.logger.exception("Erro comprimindo PDF")
        abort(500)

@compress_bp.get('/compress/profiles')
def list_profiles():
    """Endpoint opcional para o front exibir nomes e descrições das opções."""
    items = {k: {'label': v['label'], 'hint': v['hint']} for k, v in USER_PROFILES.items()}
    return jsonify(items)