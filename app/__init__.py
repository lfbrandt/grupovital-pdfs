import os
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify
from flask_talisman import Talisman
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import secrets
from .utils.config_utils import clean_old_uploads


# Limiter instanciado no módulo para ser importável pelas rotas
limiter = Limiter(key_func=get_remote_address, default_limits=["10 per minute"])
csrf = CSRFProtect()


def create_app():
    # Carrega automaticamente o arquivo .env adequado em envs/
    env = os.environ.get("FLASK_ENV", "development")
    dotenv_path = os.path.join(os.getcwd(), "envs", f".env.{env}")
    load_dotenv(dotenv_path, override=True)

    app = Flask(__name__)

    # Ajuste para trabalhar atrás de proxy (Render, Cloudflare, etc.)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    # Configurações via variáveis de ambiente
    app.config["UPLOAD_FOLDER"] = os.path.join(os.getcwd(), "uploads")

    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        secret_key = secrets.token_hex(16)
    app.config["SECRET_KEY"] = secret_key

    # Processar MAX_CONTENT_LENGTH removendo comentários e espaços
    raw_max = os.environ.get("MAX_CONTENT_LENGTH", "")
    if raw_max:
        cleaned = raw_max.split("#", 1)[0].strip()
        try:
            app.config["MAX_CONTENT_LENGTH"] = int(cleaned)
        except ValueError:
            app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
    else:
        app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

    # Criar pasta de upload se não existir
    if not os.path.exists(app.config["UPLOAD_FOLDER"]):
        os.makedirs(app.config["UPLOAD_FOLDER"])

    # Limpar arquivos antigos do diretório de upload
    ttl = int(os.environ.get("UPLOAD_TTL_HOURS", "24"))
    clean_old_uploads(app.config["UPLOAD_FOLDER"], ttl)

    # Configurar se Talisman deve forçar HTTPS
    raw_force = os.environ.get("FORCE_HTTPS")
    force_https = (
        raw_force.lower() not in ("false", "0", "no")
        if raw_force is not None
        else env == "production"
    )

    # Configurar políticas de segurança HTTP com Flask-Talisman
    csp = {
        "default-src": ["'self'"],
        "script-src": ["'self'", "https://cdn.jsdelivr.net"],
        "style-src": ["'self'", "https://fonts.googleapis.com"],
        "img-src": ["'self'", "data:"],
        "font-src": ["'self'", "https://fonts.gstatic.com"],
        "connect-src": ["'self'", "blob:"],
        "worker-src": ["'self'", "blob:"],
        "frame-src": ["'self'", "blob:"],
    }
    Talisman(
        app,
        content_security_policy=csp,
        force_https=force_https,
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
    )

    # Inicializa extensões
    limiter.init_app(app)
    csrf.init_app(app)

    # Importar e registrar Blueprints
    from .routes.converter import converter_bp
    from .routes.merge import merge_bp
    from .routes.split import split_bp
    from .routes.compress import compress_bp
    from .routes.viewer import viewer_bp

    api_prefix = "/api/pdf"
    app.register_blueprint(converter_bp, url_prefix=api_prefix)
    app.register_blueprint(merge_bp, url_prefix=api_prefix)
    app.register_blueprint(split_bp, url_prefix=api_prefix)
    app.register_blueprint(compress_bp, url_prefix=api_prefix)
    app.register_blueprint(viewer_bp)

    # Rotas das páginas do frontend
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/converter")
    def converter_page():
        return render_template("converter.html")

    @app.route("/merge")
    def merge_page():
        return render_template("merge.html")

    @app.route("/split")
    def split_page():
        return render_template("split.html")

    @app.route("/compress")
    def compress_page():
        return render_template("compress.html")

    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        """Return JSON or HTML when CSRF validation fails."""
        if (
            request.accept_mimetypes.accept_json
            and not request.accept_mimetypes.accept_html
        ):
            return jsonify({"error": "CSRF token missing or invalid."}), 400
        return render_template("csrf_error.html", reason=e.description), 400

    @app.errorhandler(RequestEntityTooLarge)
    def handle_file_too_large(e):
        """Return JSON when uploaded file exceeds MAX_CONTENT_LENGTH."""
        return jsonify({"error": "Arquivo muito grande."}), 413

    @app.errorhandler(500)
    def handle_internal_error(e):
        if (
            request.accept_mimetypes.accept_json
            and not request.accept_mimetypes.accept_html
        ):
            return jsonify({"error": "Erro interno no servidor."}), 500
        return render_template("internal_error.html"), 500

    return app
