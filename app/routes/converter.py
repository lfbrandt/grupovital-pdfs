from flask import Blueprint, request, jsonify, send_file, after_this_request, current_app, abort
import os, tempfile, json
from werkzeug.utils import secure_filename
from ..services.converter_service import converter_doc_para_pdf, converter_planilha_para_pdf
from .. import limiter

converter_bp = Blueprint('converter', __name__)

ALLOWED_EXTS = {
    'pdf','doc','docx','odt','rtf','txt','html',
    'xls','xlsx','ods','csv',
    'ppt','pptx','odp',
    'jpg','jpeg','png','bmp','tiff'
}
SHEET_EXTS = {'csv','xls','xlsx','ods'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTS

@converter_bp.route('/convert', methods=['POST'])
@limiter.limit("5 per minute")
def convert():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400
    if not f.filename:
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400
    if not allowed_file(f.filename):
        return jsonify({'error': 'Formato não suportado.'}), 400

    filename = secure_filename(f.filename)
    if '.' not in filename:
        return jsonify({'error': 'Extensão de arquivo inválida.'}), 400
    ext = filename.rsplit('.', 1)[1].lower()

    mods = request.form.get('modificacoes')
    modificacoes = None
    if mods:
        try:
            modificacoes = json.loads(mods)
        except json.JSONDecodeError:
            return jsonify({'error': 'modificacoes deve ser JSON válido'}), 400

    try:
        # ✅ Se já for PDF: só devolve para preview/download
        if ext == 'pdf':
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                f.save(tmp.name)
                output_path = tmp.name

            @after_this_request
            def cleanup_pdf(resp):
                try: os.remove(output_path)
                except OSError: pass
                return resp

            return send_file(output_path, mimetype='application/pdf', as_attachment=False)

        # Planilhas (inclui ODS/CSV)
        if ext in SHEET_EXTS:
            output_path = converter_planilha_para_pdf(f, modificacoes=modificacoes)
        else:
            # Demais docs/imagens/apresentações
            output_path = converter_doc_para_pdf(f, modificacoes=modificacoes)

        @after_this_request
        def cleanup(resp):
            try: os.remove(output_path)
            except OSError: pass
            return resp

        return send_file(output_path, mimetype='application/pdf', as_attachment=False)

    except Exception:
        current_app.logger.exception(f"Erro convertendo {filename} (ext={ext})")
        abort(500)