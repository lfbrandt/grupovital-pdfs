# app/routes/dev.py
from flask import Blueprint, render_template
dev_bp = Blueprint('dev', __name__)
@dev_bp.route('/dev/editor')
def dev_editor():
    return render_template('dev-editor.html')