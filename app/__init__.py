from flask import Flask, render_template
import os

def create_app():
    app = Flask(__name__)
    app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
    app.config['SECRET_KEY'] = 'super-secret-key'

    # Criar pasta de upload se não existir
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    # Importar e registrar Blueprints
    from .routes.converter import converter_bp
    from .routes.merge import merge_bp
    from .routes.split import split_bp
    from .routes.compress import compress_bp

    app.register_blueprint(converter_bp, url_prefix='/api')
    app.register_blueprint(merge_bp, url_prefix='/api')
    app.register_blueprint(split_bp, url_prefix='/api')
    app.register_blueprint(compress_bp, url_prefix='/api')

    # Rotas das páginas do frontend
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/converter')
    def converter_page():
        return render_template('converter.html')

    @app.route('/merge')
    def merge_page():
        return render_template('merge.html')

    @app.route('/split')
    def split_page():
        return render_template('split.html')

    @app.route('/compress')
    def compress_page():
        return render_template('compress.html')

    return app