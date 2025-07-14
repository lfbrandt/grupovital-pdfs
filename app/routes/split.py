from flask import Blueprint, request, jsonify, send_file, render_template, current_app, after_this_request, abort
from ..services.split_service import dividir_pdf
from ..services.merge_service import extrair_paginas_pdf
import json
import os
import zipfile
import uuid
from .. import limiter

split_bp = Blueprint('split', __name__)

# Limita este endpoint a no máximo 5 requisições por minuto por IP
@split_bp.route('/split', methods=['POST'])
@limiter.limit("5 per minute")
def split():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    if 'pages' in request.form:
        try:
            pages = json.loads(request.form['pages'])
        except json.JSONDecodeError:
            return jsonify({'error': 'pages deve ser JSON valido'}), 400

        try:
            output_path = extrair_paginas_pdf(file, [int(p) for p in pages])

            @after_this_request
            def cleanup(response):
                try:
                    os.remove(output_path)
                except OSError:
                    pass
                return response

            return send_file(output_path, as_attachment=True)
        except Exception:
            current_app.logger.exception("Erro extraindo paginas")
            abort(500)

    mods = request.form.get('modificacoes')
    modificacoes = None
    if mods:
        try:
            modificacoes = json.loads(mods)
        except json.JSONDecodeError:
            return jsonify({'error': 'modificacoes deve ser JSON valido'}), 400

    try:
        pdf_paths = dividir_pdf(file, modificacoes=modificacoes)

        zip_filename = f"{uuid.uuid4().hex}.zip"
        zip_path = os.path.join(current_app.config['UPLOAD_FOLDER'], zip_filename)
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for pdf in pdf_paths:
                zipf.write(pdf, os.path.basename(pdf))

        @after_this_request
        def cleanup(response):
            try:
                os.remove(zip_path)
                for p in pdf_paths:
                    os.remove(p)
            except OSError:
                pass
            return response

        return send_file(zip_path, as_attachment=True)

    except Exception:
        current_app.logger.exception("Erro dividindo PDF")
        abort(500)

@split_bp.route('/split', methods=['GET'])
def split_form():
    return render_template('split.html')
