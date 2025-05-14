from flask import Blueprint, request, jsonify, send_file, render_template
from ..services.converter_service import (
    converter_doc_para_pdf,
    converter_planilha_para_pdf
)
from werkzeug.utils import secure_filename

converter_bp = Blueprint('converter', __name__)

@converter_bp.route('/convert', methods=['POST'])
def convert():
    # Verifica se o arquivo foi enviado
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    # Determina a extensão para escolher o serviço correto
    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[1].lower()

    try:
        # Se for CSV, XLS ou XLSX, usa o serviço de planilhas
        if ext in ['csv', 'xls', 'xlsx']:
            output_path = converter_planilha_para_pdf(file)
        else:
            output_path = converter_doc_para_pdf(file)

        return send_file(output_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@converter_bp.route('/', methods=['GET'])
def home():
    return render_template('index.html')