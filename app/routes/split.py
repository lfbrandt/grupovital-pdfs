from flask import Blueprint, request, jsonify, send_file, render_template, send_from_directory
from ..services.split_service import dividir_pdf
import os
import zipfile

split_bp = Blueprint('split', __name__)

@split_bp.route('/split', methods=['POST'])
def split():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    try:
        pdf_paths = dividir_pdf(file)

        # Compacta as páginas em um .zip para facilitar o download
        zip_path = os.path.join(os.getcwd(), 'uploads', 'pdf_dividido.zip')
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for pdf in pdf_paths:
                zipf.write(pdf, os.path.basename(pdf))

        return send_file(zip_path, as_attachment=True)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@split_bp.route('/split', methods=['GET'])
def split_form():
    return render_template('split.html')
