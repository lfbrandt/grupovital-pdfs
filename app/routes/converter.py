from flask import Blueprint, request, jsonify, send_file, after_this_request, current_app, abort
import os
from ..services.converter_service import (
    converter_doc_para_pdf,
    converter_planilha_para_pdf
)
import json
from werkzeug.utils import secure_filename
from .. import limiter

converter_bp = Blueprint('converter', __name__)

# Extensões permitidas para conversão
ALLOWED_EXTS = {
    'pdf','doc','docx','odt','rtf','txt','html',
    'xls','xlsx','ods',
    'ppt','pptx','odp',
    'jpg','jpeg','png','bmp','tiff'
}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTS

# Limita este endpoint a no máximo 5 requisições por minuto por IP
@converter_bp.route('/convert', methods=['POST'])
@limiter.limit("5 per minute")
def convert():
    # Verifica se o arquivo foi enviado
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400
    if not allowed_file(file.filename):
        return jsonify({'error': 'Formato n\u00e3o suportado.'}), 400

    # Determina a extensão para escolher o serviço correto
    filename = secure_filename(file.filename)
    if '.' not in filename:
        return jsonify({'error': 'Extensão de arquivo inválida.'}), 400
    ext = filename.rsplit('.', 1)[1].lower()

    mods = request.form.get('modificacoes')
    modificacoes = None
    if mods:
        try:
            modificacoes = json.loads(mods)
        except json.JSONDecodeError:
            return jsonify({'error': 'modificacoes deve ser JSON valido'}), 400

    try:
        # Se for CSV, XLS ou XLSX, usa o serviço de planilhas
        if ext in ['csv', 'xls', 'xlsx']:
            output_path = converter_planilha_para_pdf(file, modificacoes=modificacoes)
        else:
            output_path = converter_doc_para_pdf(file, modificacoes=modificacoes)

        @after_this_request
        def cleanup(response):
            try:
                os.remove(output_path)
            except OSError:
                pass
            return response

        return send_file(output_path, as_attachment=True)

    except Exception:
        current_app.logger.exception(f"Erro convertendo {filename}")
        abort(500)
