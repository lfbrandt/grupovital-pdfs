from flask import Blueprint, request, jsonify, send_file, render_template
from ..services.compress_service import comprimir_pdf

compress_bp = Blueprint('compress', __name__)

@compress_bp.route('/compress', methods=['POST'])
def compress():
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400

    try:
        output_path = comprimir_pdf(file)
        return send_file(output_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@compress_bp.route('/compress', methods=['GET'])
def compress_form():
    return render_template('compress.html')