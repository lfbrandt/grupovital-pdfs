from flask import (
    Blueprint,
    request,
    jsonify,
    send_file,
    render_template,
    after_this_request,
    abort,
    current_app,
)
import os
import json
from ..services.compress_service import comprimir_pdf
from ..utils.preview_utils import preview_pdf
from .. import limiter

compress_bp = Blueprint('compress', __name__)

# Limita este endpoint a no máximo 5 requisições por minuto por IP
@compress_bp.route('/compress', methods=['POST'])
@limiter.limit("5 per minute")
def compress():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    # carrega modificações opcionais
    modificacoes = None
    mods = request.form.get('modificacoes')
    if mods:
        try:
            modificacoes = json.loads(mods)
        except json.JSONDecodeError:
            return jsonify({'error': 'Formato de modificacoes inválido. Deve ser JSON.'}), 400

    # carrega rotações opcionais
    rotations = None
    rot_json = request.form.get('rotations', '[]')
    try:
        rotations = json.loads(rot_json)
        rotations = [int(r) for r in rotations]
    except (json.JSONDecodeError, ValueError, TypeError):
        return jsonify({'error': 'Formato de rotations inválido. Deve ser um JSON de números.'}), 400

    try:
        output_path = comprimir_pdf(
            file,
            rotations=rotations,
            modificacoes=modificacoes
        )

        @after_this_request
        def cleanup(response):
            try:
                os.remove(output_path)
            except OSError:
                pass
            return response

        return send_file(output_path, as_attachment=True)
    except Exception:
        current_app.logger.exception("Erro comprimindo PDF")
        abort(500)

@compress_bp.route('/compress', methods=['GET'])
def compress_form():
    return render_template('compress.html')

@compress_bp.route('/compress/preview', methods=['POST'])
def preview_compress():
    """Return thumbnails for um PDF usado no preview de compressão."""
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400
    file = request.files['file']
    thumbs = preview_pdf(file)
    return jsonify({'thumbnails': thumbs})