from flask import Blueprint, request, jsonify, send_file, render_template, after_this_request, abort, current_app
import os
from ..services.merge_service import juntar_pdfs, extrair_paginas_pdf, merge_pdfs, merge_selected_pdfs
import json
from .. import limiter

merge_bp = Blueprint('merge', __name__)

# Limita este endpoint a no máximo 3 requisições por minuto por IP
@merge_bp.route('/merge', methods=['POST'])
@limiter.limit("3 per minute")
def merge():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    pages_map = {}
    for idx in range(len(files)):
        key = f'pages_{idx}'
        if key in request.form:
            pages_map[idx] = json.loads(request.form[key])
        else:
            pages_map[idx] = None

    try:
        output_path = merge_selected_pdfs(files, pages_map)

        @after_this_request
        def cleanup(response):
            try:
                os.remove(output_path)
            except OSError:
                pass
            return response

        return send_file(output_path, as_attachment=True)
    except Exception as e:
        current_app.logger.exception("Erro juntando PDFs")
        abort(500)

@merge_bp.route('/merge', methods=['GET'])
def merge_form():
    return render_template('merge.html')
