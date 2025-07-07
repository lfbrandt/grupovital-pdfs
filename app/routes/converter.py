from flask import Blueprint, request, jsonify, send_file, render_template, after_this_request, current_app
import os
from ..services.converter_service import (
    converter_doc_para_pdf,
    converter_planilha_para_pdf
)
from werkzeug.utils import secure_filename
from .. import limiter

converter_bp = Blueprint('converter', __name__)

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

    # Determina a extensão para escolher o serviço correto
    filename = secure_filename(file.filename)
    if '.' not in filename:
        return jsonify({'error': 'Extensão de arquivo inválida.'}), 400
    ext = filename.rsplit('.', 1)[1].lower()

    try:
        # Se for CSV, XLS ou XLSX, usa o serviço de planilhas
        if ext in ['csv', 'xls', 'xlsx']:
            output_path = converter_planilha_para_pdf(file)
        else:
            output_path = converter_doc_para_pdf(file)

        @after_this_request
        def cleanup(response):
            try:
                os.remove(output_path)
            except OSError:
                pass
            return response

        return send_file(output_path, as_attachment=True)

    except Exception as e:
        # registra stacktrace completo no log do Gunicorn
        current_app.logger.exception(f"Erro convertendo {filename}")
        return jsonify({'error': str(e)}), 500

@converter_bp.route('/', methods=['GET'])
def home():
    return render_template('index.html')
