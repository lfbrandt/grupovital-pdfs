from flask import Blueprint, render_template, current_app, send_from_directory, abort
import os

viewer_bp = Blueprint('viewer', __name__, url_prefix='/viewer')

@viewer_bp.route('/<path:filename>')
def show_pdf(filename):
    upload_folder = current_app.config['UPLOAD_FOLDER']
    file_path = os.path.join(upload_folder, filename)
    if not os.path.isfile(file_path):
        abort(404)
    return render_template('viewer.html', filename=filename)

@viewer_bp.route('/raw/<path:filename>')
def get_pdf(filename):
    upload_folder = current_app.config['UPLOAD_FOLDER']
    return send_from_directory(upload_folder, filename)

