# app/routes/viewer.py
# -*- coding: utf-8 -*-
"""
Rotas de visualização e download de arquivos gerados pelo conversor.
Segurança:
- Proteção contra path traversal: resolve o caminho real e valida que está
  dentro de UPLOAD_FOLDER antes de servir.
- PDFs são servidos inline (comportamento normal do browser).
- Outros formatos (xlsx, docx, csv, etc.) são servidos como attachment,
  forçando o download com o nome correto do arquivo.
- Nenhum caminho absoluto é exposto na resposta.
- Logs não registram conteúdo do arquivo nem dados sensíveis.
"""
import logging
import os

from flask import Blueprint, abort, current_app, render_template, send_from_directory

from ..utils.security import session_owned_generated_rel_path

logger = logging.getLogger(__name__)

viewer_bp = Blueprint('viewer', __name__, url_prefix='/viewer')


def _safe_upload_path(upload_folder: str, filename: str) -> str:
    """
    Resolve o caminho real do arquivo e valida que está dentro de upload_folder.
    Lança 404 se o arquivo não existir ou se houver tentativa de path traversal.
    Retorna o caminho absoluto seguro.
    """
    safe_filename = session_owned_generated_rel_path(filename)
    if not safe_filename:
        abort(404)

    # Normaliza upload_folder
    base = os.path.realpath(os.path.abspath(upload_folder))

    # Monta o candidato sem permitir componentes absolutos no filename
    # (os.path.join ignora partes anteriores se encontra um componente absoluto)
    candidate = os.path.realpath(os.path.join(base, safe_filename))

    # Verifica que o arquivo resolvido está dentro da pasta de uploads
    if not candidate.startswith(base + os.sep) and candidate != base:
        logger.warning(
            "[viewer] Tentativa de path traversal bloqueada. "
            "upload_folder=%s filename_len=%d",
            base, len(filename)
        )
        abort(404)

    if not os.path.isfile(candidate):
        abort(404)

    return candidate


@viewer_bp.route('/<path:filename>')
def show_pdf(filename):
    upload_folder = current_app.config['UPLOAD_FOLDER']
    safe_filename = session_owned_generated_rel_path(filename)
    _safe_upload_path(upload_folder, filename)   # valida existência + traversal
    return render_template('viewer.html', filename=safe_filename)


@viewer_bp.route('/raw/<path:filename>')
def get_pdf(filename):
    """
    Serve o arquivo diretamente:
    - .pdf sem ?download=1  → inline (abre no browser / visualizador embutido)
    - .pdf com ?download=1  → attachment (força download)
    - demais                → attachment (força download com nome correto)
    """
    from flask import request as _req
    upload_folder = current_app.config['UPLOAD_FOLDER']
    safe_filename = session_owned_generated_rel_path(filename)
    safe_path = _safe_upload_path(upload_folder, filename)   # 404 se inválido / traversal

    basename = os.path.basename(safe_path)
    _, ext = os.path.splitext(basename)
    is_pdf = ext.lower() == '.pdf'

    force_download = _req.args.get('download', '').lower() in {'1', 'true', 'yes'}
    as_attachment = force_download or not is_pdf

    logger.debug(
        "[viewer] servindo arquivo ext=%s as_attachment=%s force_download=%s",
        ext.lower(), as_attachment, force_download
    )

    if as_attachment:
        return send_from_directory(
            upload_folder,
            safe_filename,
            as_attachment=True,
            download_name=basename,
        )
    # PDF inline — permite visualização no browser / pdf.js
    return send_from_directory(upload_folder, safe_filename)

