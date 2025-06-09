import os
from dotenv import load_dotenv
from flask import Flask, render_template
from flask_talisman import Talisman
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

# Caminhos base
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

# Limiter instanciado no módulo para ser importável pelas rotas
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["10 per minute"]
)
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)

    # Ajuste para trabalhar atrás de proxy (Render, Cloudflare, etc.)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # Configurações via variáveis de ambiente
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')

    # Processar MAX_CONTENT_LENGTH removendo comentários e espaços
    raw_max = os.environ.get('MAX_CONTENT_LENGTH', '')
    if raw_max:
        cleaned = raw_max.split('#', 1)[0].strip()
        try:
            app.config['MAX_CONTENT_LENGTH'] = int(cleaned)
        except ValueError:
            app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
    else:
        app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    # Criar pasta de upload se não existir
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    # Configurar políticas de segurança HTTP com Flask-Talisman
    csp = {
        'default-src': ["'self'"],
        'script-src': ["'self'"],
        'style-src': ["'self'"],
        'img-src': ["'self'", 'data:'],
        'font-src': ["'self'"],
    }
    Talisman(
        app,
        content_security_policy=csp,
        force_https=True,
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        frame_options='DENY',
        referrer_policy='no-referrer'
    )

    # Inicializa extensões
    limiter.init_app(app)
    csrf.init_app(app)

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
