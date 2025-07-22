# app/routes/compress.py

from flask import Blueprint, request, jsonify, send_file, render_template, after_this_request, abort, current_app, url_for
import os
import json
from ..services.compress_service import comprimir_pdf, gerar_previews, preview_pdf
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

    level = request.form.get('level', 'ebook')
    mods_field = request.form.get('modifications') or request.form.get('mods')
    if mods_field:
        try:
            mods = json.loads(mods_field)
        except json.JSONDecodeError:
            return jsonify({'error': 'mods deve ser JSON valido'}), 400
    else:
        mods = {}

    try:
        output_path = comprimir_pdf(file, level=level, mods=mods)

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

# Nova rota para gerar preview das páginas como data-URIs
@compress_bp.route('/compress/preview', methods=['POST'])
@limiter.limit("5 per minute")
def compress_preview():
    file = request.files.get('file')
    if not file or not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Envie um PDF válido.'}), 400
    pages = preview_pdf(file)
    return jsonify({'pages': pages})
