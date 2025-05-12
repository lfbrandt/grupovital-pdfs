from flask import Blueprint, request, jsonify, send_file, render_template
from ..services.merge_service import juntar_pdfs
import os

merge_bp = Blueprint('merge', __name__)

@merge_bp.route('/merge', methods=['POST'])
def merge():
    if 'files' not in request.files:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400

    files = request.files.getlist('files')

    if len(files) < 2:
        return jsonify({'error': 'Envie pelo menos dois arquivos PDF.'}), 400

    try:
        output_path = juntar_pdfs(files)
        return send_file(output_path, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@merge_bp.route('/merge', methods=['GET'])
def merge_form():
    return render_template('merge.html')