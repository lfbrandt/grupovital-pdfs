import os
import uuid
import secrets
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, g
from flask_talisman import Talisman
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Formatter template with request_id placeholder
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-5s | %(name)s:%(funcName)s:%(lineno)d | "
    "req=%(request_id)s | %(message)s"
)

class RequestFilter(logging.Filter):
    """Attach a request_id from flask.g to each log record."""
    def filter(self, record):
        record.request_id = getattr(g, 'request_id', '-')
        return True

# Limiter instanciado no módulo para ser importável pelas rotas
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["10 per minute"]
)
csrf = CSRFProtect()

def create_app():
    # Carrega .env correto
    env = os.environ.get('FLASK_ENV', 'development')
    dotenv_path = os.path.join(os.getcwd(), 'envs', f'.env.{env}')
    load_dotenv(dotenv_path, override=True)

    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # Configurações básicas
    app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
    secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(16)
    app.config['SECRET_KEY'] = secret_key
    raw_max = os.environ.get('MAX_CONTENT_LENGTH', '')
    if raw_max:
        cleaned = raw_max.split('#', 1)[0].strip()
        try:
            app.config['MAX_CONTENT_LENGTH'] = int(cleaned)
        except ValueError:
            app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
    else:
        app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    from .utils.config_utils import clean_old_uploads
    ttl = int(os.environ.get('UPLOAD_TTL_HOURS', '24'))
    clean_old_uploads(app.config['UPLOAD_FOLDER'], ttl)

    # Talisman CSP
    raw_force = os.environ.get("FORCE_HTTPS")
    force_https = (
        raw_force.lower() not in ("false", "0", "no")
        if raw_force is not None
        else env == "production"
    )
    csp = {
        'default-src': ["'self'"],
        'script-src': ["'self'", 'https://cdn.jsdelivr.net'],
        'style-src': ["'self'", 'https://fonts.googleapis.com'],
        'img-src': ["'self'", 'data:'],
        'font-src': ["'self'", 'https://fonts.gstatic.com'],
        'connect-src': ["'self'", 'blob:'],
        'worker-src': ["'self'", 'blob:'],
        'frame-src': ["'self'", 'blob:']
    }
    # Adiciona nonces para scripts e estilos
    Talisman(
        app,
        content_security_policy=csp,
        content_security_policy_nonce_in=["script-src", "style-src"],
        force_https=force_https,
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        frame_options='DENY',
        referrer_policy='strict-origin-when-cross-origin'
    )

    # Structured file handler
    log_path = os.path.join(app.root_path, 'app.log')
    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=5, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.addFilter(RequestFilter())
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.DEBUG)

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.addFilter(RequestFilter())
    console.setFormatter(logging.Formatter("%(levelname)-5s | req=%(request_id)s | %(message)s"))
    app.logger.addHandler(console)

    # Assign a unique request_id for each request
    @app.before_request
    def assign_request_id():
        g.request_id = uuid.uuid4().hex[:8]

    # Log incoming request
    @app.before_request
    def log_request_info():
        files = {k: v.filename for k, v in request.files.items()}
        app.logger.debug(
            f"Request {request.method} {request.path} | "
            f"args={request.args.to_dict()} | "
            f"form={request.form.to_dict()} | "
            f"files={files}"
        )

    # Log outgoing response
    @app.after_request
    def log_response_info(response):
        app.logger.debug(f"Response {response.status} | {request.method} {request.path}")
        return response

    # Inicializa extensões
    limiter.init_app(app)
    csrf.init_app(app)

    # Registrar Blueprints
    from .routes.converter import converter_bp
    from .routes.merge import merge_bp
    from .routes.split import split_bp
    from .routes.compress import compress_bp
    from .routes.viewer import viewer_bp

    app.register_blueprint(converter_bp, url_prefix='/api')
    app.register_blueprint(merge_bp, url_prefix='/api')
    app.register_blueprint(split_bp, url_prefix='/api')
    app.register_blueprint(compress_bp, url_prefix='/api')
    app.register_blueprint(viewer_bp)

    # Rotas do frontend
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

    # Tratamento de erros
    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'CSRF token missing or invalid.'}), 400
        return render_template('csrf_error.html', reason=e.description), 400

    @app.errorhandler(RequestEntityTooLarge)
    def handle_file_too_large(e):
        return jsonify({'error': 'Arquivo muito grande.'}), 413

    @app.errorhandler(500)
    def handle_internal_error(e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'Erro interno no servidor.'}), 500
        return render_template('internal_error.html'), 500

    return app