from flask import Blueprint, request, jsonify, send_file, render_template
from ..services.converter_service import converter_doc_para_pdf
import os

converter_bp = Blueprint('converter', __name__)

@converter_bp.route('/convert', methods=['POST'])
def convert():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    try:
        output_path = converter_doc_para_pdf(file)
        return send_file(output_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@converter_bp.route('/', methods=['GET'])
def home():
    return render_template('index.html')