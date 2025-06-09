from flask import Blueprint, request, jsonify, send_file, render_template, after_this_request
import os
from ..services.merge_service import juntar_pdfs
from .. import limiter

merge_bp = Blueprint('merge', __name__)

# Limita este endpoint a no máximo 3 requisições por minuto por IP
@merge_bp.route('/merge', methods=['POST'])
@limiter.limit("3 per minute")
def merge():
    if 'files' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    files = request.files.getlist('files')

    if len(files) < 2:
        return jsonify({'error': 'Envie pelo menos dois arquivos PDF.'}), 400

    try:
        output_path = juntar_pdfs(files)

        @after_this_request
        def cleanup(response):
            try:
                os.remove(output_path)
            except OSError:
                pass
            return response

        return send_file(output_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@merge_bp.route('/merge', methods=['GET'])
def merge_form():
    return render_template('merge.html')
