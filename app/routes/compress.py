# app/routes/compress.py

from flask import Blueprint, request, jsonify, send_file, render_template, after_this_request, abort, current_app, url_for
import os
from ..services.compress_service import comprimir_pdf, gerar_previews
import json
from .. import limiter

compress_bp = Blueprint('compress', __name__)


@compress_bp.route('/preview', methods=['POST'])
def preview():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400
    try:
        names = gerar_previews(file)
        urls = [
            url_for('viewer.get_pdf', filename=n)
            for n in names
        ]
        return jsonify({'pages': urls})
    except Exception:
        current_app.logger.exception('Erro gerando preview')
        abort(500)

# Limita este endpoint a no máximo 5 requisições por minuto por IP
@compress_bp.route('/compress', methods=['POST'])
@limiter.limit("5 per minute")
def compress():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    mods_field = request.form.get('mods')
    mods = None
    if mods_field:
        try:
            mods = json.loads(mods_field)
        except json.JSONDecodeError:
            return jsonify({'error': 'mods deve ser JSON valido'}), 400

    mods_old = request.form.get('modificacoes')
    modificacoes = None
    if mods_old:
        try:
            modificacoes = json.loads(mods_old)
        except json.JSONDecodeError:
            return jsonify({'error': 'modificacoes deve ser JSON valido'}), 400

    rot_json = request.form.get('rotations')
    rotations = None
    if rot_json:
        try:
            rotations = json.loads(rot_json)
        except json.JSONDecodeError:
            return jsonify({'error': 'rotations deve ser JSON valido'}), 400

    try:
        output_path = comprimir_pdf(
            file,
            rotations=rotations,
            modificacoes=modificacoes,
            mods=mods
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
