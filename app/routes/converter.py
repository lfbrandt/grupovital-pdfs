# app/routes/converter.py
from flask import (
    Blueprint, request, jsonify, send_file, after_this_request,
    current_app, abort, render_template, redirect, url_for, session
)
import os, tempfile, json, uuid, shutil
from werkzeug.utils import secure_filename
from werkzeug.exceptions import BadRequest
from .. import limiter

from ..services.converter_service import (
    converter_doc_para_pdf,
    converter_planilha_para_pdf,
    convert_upload_to_target,
    convert_many_uploads,
    convert_many_uploads_to_single_pdf,   # ⬅️ NOVO
)

# Blueprint já com prefixo /api/convert
converter_bp = Blueprint("converter", __name__, url_prefix="/api/convert")

# ----------------- Constantes/whitelists -----------------
ALLOWED_EXTS = {
    'pdf','doc','docx','odt','rtf','txt','html','htm',
    'xls','xlsx','ods','csv',
    'ppt','pptx','odp',
    'jpg','jpeg','png','bmp','tiff','tif'
}
SHEET_EXTS = {'csv','xls','xlsx','ods'}

ALLOWED_BY_TARGET = {
    'to-pdf' : ALLOWED_EXTS,
    'to-docx': {'pdf','doc','docx','odt','rtf','txt','html','htm'},
    'to-csv' : {'pdf','xls','xlsx','ods','csv'},
    'to-xlsm': {'xls','xlsx','ods','csv'},
    'to-xlsx': {'pdf','xls','xlsx','ods','csv'},
}

FRIENDLY_GOALS = {
    'to-pdf','pdf-to-docx','sheet-to-csv','sheet-to-xlsm','pdf-to-xlsx'
}

def _allowed_file(filename, allowed):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed

def _ensure_unique_path(dirpath: str, name: str) -> str:
    base = os.path.splitext(name)[0]
    ext  = os.path.splitext(name)[1]
    candidate = os.path.join(dirpath, name)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dirpath, f"{base} ({i}){ext}")
        i += 1
    return candidate

# =================== Seletor de objetivo ===================
@converter_bp.get('/select')
def converter_select_page():
    return render_template('convert_wizard.html')

# =================== Persistência do objetivo na sessão ===================
@converter_bp.get('/set-goal/<goal>')
@limiter.exempt
def set_convert_goal(goal: str):
    goal = (goal or '').strip().lower()
    if goal not in FRIENDLY_GOALS:
        raise BadRequest("Objetivo inválido.")
    session['convert_goal'] = goal
    try:
        return redirect(url_for('converter_page'))
    except Exception:
        return redirect('/converter', code=302)

@converter_bp.get('/goal')
@limiter.exempt
def get_convert_goal():
    return jsonify({'goal': session.get('convert_goal', 'to-pdf')}), 200

# =================== API: multi-upload/múltiplos outputs ===================
@converter_bp.post('/<target>')
@limiter.limit("10 per minute")
def api_convert_multi(target: str):
    target = (target or '').strip().lower()
    if target not in ALLOWED_BY_TARGET:
        raise BadRequest("Destino inválido. Use: to-pdf, to-docx, to-csv, to-xlsm, to-xlsx.")

    files = request.files.getlist('files[]') or request.files.getlist('files')
    if not files:
        return jsonify({'error': "Envie pelo menos um arquivo em 'files[]'."}), 400

    allowed_exts = ALLOWED_BY_TARGET[target]
    for f in files:
        if not f or not f.filename or not _allowed_file(f.filename, allowed_exts):
            return jsonify({'error': f"Formato não suportado para {target}: {getattr(f,'filename','(sem nome)')}"}), 400

    job_id = str(uuid.uuid4())
    job_out_dir = os.path.join(tempfile.gettempdir(), 'gvpdf_jobs', job_id, 'out')
    os.makedirs(job_out_dir, exist_ok=True)

    try:
        raw_outputs = convert_many_uploads(files, target.replace('to-', ''), job_out_dir)
    except Exception:
        current_app.logger.exception(f"[convert-multi] erro no job {job_id}")
        abort(500)

    final_paths = []
    if not raw_outputs:
        current_app.logger.error(f"[convert-multi] job {job_id} sem outputs")
        abort(500)

    for src in raw_outputs:
        if not src or not os.path.isfile(src):
            current_app.logger.error(f"[convert-multi] output inexistente: {src}")
            continue
        dst_name = os.path.basename(src) or "output"
        dst_path = _ensure_unique_path(job_out_dir, dst_name)
        try:
            if os.path.abspath(os.path.dirname(src)) == os.path.abspath(job_out_dir):
                if os.path.abspath(src) != os.path.abspath(dst_path):
                    os.replace(src, dst_path)
                final_paths.append(dst_path)
            else:
                try:
                    os.replace(src, dst_path)
                except Exception:
                    shutil.copyfile(src, dst_path)
                final_paths.append(dst_path)
        except Exception:
            current_app.logger.exception(f"[convert-multi] falha movendo/cop. '{src}' → '{dst_path}'")

    if not final_paths:
        current_app.logger.error(f"[convert-multi] job {job_id} sem outputs finais")
        abort(500)

    base_download = f"/api/convert/download/{job_id}"
    payload = [{
        'name': os.path.basename(p),
        'size': os.path.getsize(p),
        'download_url': f"{base_download}/{os.path.basename(p)}"
    } for p in final_paths]

    return jsonify({'jobId': job_id, 'count': len(payload), 'files': payload}), 200

# =================== NOVA API: unir tudo em um único PDF ===================
@converter_bp.post('/to-pdf-merge')
@limiter.limit("10 per minute")
def api_convert_to_pdf_merge():
    files = request.files.getlist('files[]') or request.files.getlist('files')
    if not files:
        return jsonify({'error': "Envie pelo menos um arquivo em 'files[]'."}), 400
    try:
        final_pdf = convert_many_uploads_to_single_pdf(files)
        return send_file(
            final_pdf,
            mimetype='application/pdf',
            as_attachment=True,
            download_name='arquivos_unidos.pdf'
        )
    except BadRequest as e:
        return jsonify({'error': str(e)}), 400
    except Exception:
        current_app.logger.exception("[to-pdf-merge] erro ao unir PDFs")
        abort(500)

# =================== Download legados ===================
@converter_bp.get('/download/<job_id>/<path:filename>')
@limiter.limit("30 per minute")
def api_convert_download(job_id, filename):
    job_out_dir = os.path.join(tempfile.gettempdir(), 'gvpdf_jobs', job_id, 'out')
    out_path = os.path.join(job_out_dir, os.path.basename(filename))
    if not (os.path.isdir(job_out_dir) and os.path.isfile(out_path)):
        raise BadRequest("Arquivo não encontrado ou expirado.")
    return send_file(out_path, as_attachment=True, download_name=os.path.basename(out_path))

# =================== LEGADO: POST único -> PDF ===================
@converter_bp.route('', methods=['POST'])
@converter_bp.route('/', methods=['POST'])
@limiter.limit("5 per minute")
def convert_legacy_single_pdf():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Nenhum arquivo enviado.'}), 400
    if not f.filename:
        return jsonify({'error': 'Nenhum arquivo selecionado.'}), 400
    if not _allowed_file(f.filename, ALLOWED_EXTS):
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
        if ext == 'pdf':
            import tempfile, os
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                f.save(tmp.name)
                output_path = tmp.name

            @after_this_request
            def cleanup_pdf(resp):
                try: os.remove(output_path)
                except OSError: pass
                return resp

            return send_file(output_path, mimetype='application/pdf', as_attachment=False)

        if ext in SHEET_EXTS:
            output_path = converter_planilha_para_pdf(f, modificacoes=modificacoes)
        else:
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