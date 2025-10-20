# -*- coding: utf-8 -*-
import os
import uuid
import secrets
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from flask import (
    Flask, render_template, render_template_string,
    request, jsonify, g, has_request_context, redirect, url_for
)
from flask_talisman import Talisman
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import RequestEntityTooLarge, BadRequest
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# =========================
# Logging format estruturado
# =========================
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-5s | %(name)s:%(funcName)s:%(lineno)d | "
    "req=%(request_id)s | %(message)s"
)

class RequestFilter(logging.Filter):
    """Anexa um request_id aos logs; fora de request, usa marcador estático."""
    def filter(self, record):
        if has_request_context():
            record.request_id = getattr(g, 'request_id', '-') or '-'
        else:
            record.request_id = 'startup'
        return True

# Limiter instanciado no módulo para ser importável pelas rotas
limiter = Limiter(key_func=get_remote_address, default_limits=["10 per minute"])
csrf = CSRFProtect()

def _bool_env(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}

def _load_dotenv_by_env():
    """Carrega envs/.env.<FLASK_ENV> de forma estável (sem depender do CWD)."""
    env = (os.getenv("FLASK_ENV") or "production").strip()

    # 1) Se ENV_DIR estiver definido, ele manda:
    env_dir = os.getenv("ENV_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir) / f".env.{env}")

    # 2) Procurar 'envs/.env.<env>' perto deste arquivo e no repo
    here = Path(__file__).resolve()
    likely_roots = [here.parent, here.parent.parent, Path.cwd()]
    for root in likely_roots:
        candidates.append(root / "envs" / f".env.{env}")

    # 3) Fallback: um .env “solto” no diretório atual
    candidates.append(Path.cwd() / ".env")

    used = None
    for c in candidates:
        if c.exists():
            load_dotenv(dotenv_path=c, override=True)
            used = str(c)
            break

    return env, (used or "(none)")

def create_app():
    # =====================
    # Carrega .env adequado
    # =====================
    env, loaded_env_file = _load_dotenv_by_env()

    app = Flask(__name__)

    # Confiar nos headers do proxy (nginx)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    # =================
    # Configurações base
    # =================
    app.config['UPLOAD_FOLDER'] = os.path.join(os.getcwd(), 'uploads')
    secret_key = os.environ.get('SECRET_KEY') or secrets.token_hex(16)
    app.config['SECRET_KEY'] = secret_key

    app.config['APP_CHANNEL']  = os.getenv('APP_CHANNEL', '').strip()
    app.config['APP_VERSION']  = os.getenv('APP_VERSION', 'alpha 0.8').strip()
    app.config['BUILD_TAG'] = (
        f"{app.config['APP_CHANNEL']} {app.config['APP_VERSION']}".strip()
        if app.config['APP_CHANNEL'] else app.config['APP_VERSION']
    )

    # Limite de upload (bytes)
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

    # =========================== 🔒 Cookies de sessão ===========================
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SECURE'] = _bool_env('SESSION_COOKIE_SECURE', default=(env == 'production'))
    app.config['SESSION_COOKIE_SAMESITE'] = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
    app.config['REMEMBER_COOKIE_SECURE'] = app.config['SESSION_COOKIE_SECURE']

    # =========== 🔒 CSRF/WTF ===========
    app.config.setdefault('WTF_CSRF_TIME_LIMIT', None)

    # ================ 🔒 Talisman + CSP ================
    raw_force = os.environ.get("FORCE_HTTPS")
    force_https = (
        raw_force.lower() not in ("false", "0", "no")
        if raw_force is not None
        else env == "production"
    )

    csp_prod = {
        'default-src': ["'self'"],
        'script-src':  ["'self'", 'https://cdn.jsdelivr.net'],
        'style-src':   ["'self'", 'https://fonts.googleapis.com'],
        'img-src':     ["'self'", 'data:'],
        'font-src':    ["'self'", 'https://fonts.gstatic.com'],
        'connect-src': ["'self'", 'blob:'],
        'worker-src':  ["'self'", 'blob:'],
        'frame-src':   ["'self'", 'blob:'],
        'object-src':  ["'none'"],
        'base-uri':    ["'self'"],
    }
    csp_dev = {
        'default-src': ["'self'"],
        'script-src':  ["'self'"],
        'style-src':   ["'self'", "'unsafe-inline'"],
        'img-src':     ["'self'", 'data:'],
        'font-src':    ["'self'"],
        'connect-src': ["'self'", 'blob:'],
        'worker-src':  ["'self'", 'blob:'],
        'frame-src':   ["'self'", 'blob:'],
        'object-src':  ["'none'"],
        'base-uri':    ["'self'"],
    }

    perms = {
        "geolocation": "()",
        "microphone": "()",
        "camera": "()",
        "usb": "()",
        "fullscreen": "()",
        "browsing-topics": "()",
    }

    if env == "production":
        Talisman(
            app,
            content_security_policy=csp_prod,
            content_security_policy_nonce_in=["script-src", "style-src"],
            force_https=force_https,
            strict_transport_security=force_https,
            strict_transport_security_max_age=31536000,
            frame_options='DENY',
            referrer_policy='strict-origin-when-cross-origin',
            permissions_policy=perms,
            session_cookie_secure=app.config['SESSION_COOKIE_SECURE'],
            session_cookie_samesite=app.config['SESSION_COOKIE_SAMESITE'],
        )
    else:
        Talisman(
            app,
            content_security_policy=csp_dev,
            content_security_policy_nonce_in=["script-src", "style-src"],
            force_https=False,
            strict_transport_security=False,
            frame_options='DENY',
            referrer_policy='strict-origin-when-cross-origin',
            permissions_policy=perms,
            session_cookie_secure=app.config['SESSION_COOKIE_SECURE'],
            session_cookie_samesite=app.config['SESSION_COOKIE_SAMESITE'],
        )

    # ======================
    # Logging (arquivo + std)
    # ======================
    log_path = os.path.join(app.root_path, 'app.log')
    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=5, encoding='utf-8')
    file_level = os.environ.get('FILE_LOG_LEVEL', 'DEBUG').upper()
    console_level = os.environ.get('CONSOLE_LOG_LEVEL', 'INFO').upper()
    file_handler.setLevel(file_level)
    file_handler.addFilter(RequestFilter())
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    app.logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.addFilter(RequestFilter())
    console.setFormatter(logging.Formatter("%(levelname)-5s | req=%(request_id)s | %(message)s"))
    app.logger.addHandler(console)

    app.logger.setLevel(min(file_handler.level, console.level))

    # ===========================
    # Atribui request_id por req
    # ===========================
    @app.before_request
    def assign_request_id():
        g.request_id = uuid.uuid4().hex[:8]

    # ==========================================
    # Logs de entrada (sem vazar dados sensíveis)
    # ==========================================
    @app.before_request
    def log_request_info():
        arg_keys = list(request.args.keys())
        form_keys = list(request.form.keys())
        files_meta = {k: v.filename for k, v in request.files.items()}
        app.logger.debug(
            "REQ %s %s | args_keys=%s | form_keys=%s | files=%s",
            request.method, request.path, arg_keys, form_keys, files_meta
        )

    # =========================================================
    # Headers extras + log de saída (num único after_request)
    # =========================================================
    @app.after_request
    def security_headers_and_log(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', "geolocation=(), microphone=(), camera=(), usb=(), fullscreen=(), browsing-topics=()")

        if force_https:
            response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
            response.headers.setdefault('Cross-Origin-Resource-Policy', 'same-origin')
            if _bool_env('ENABLE_COEP', False):
                response.headers['Cross-Origin-Embedder-Policy'] = 'require-corp'
        else:
            for h in ('Cross-Origin-Opener-Policy',
                      'Cross-Origin-Embedder-Policy',
                      'Cross-Origin-Resource-Policy'):
                response.headers.pop(h, None)

        app.logger.debug("RESP %s | %s %s", response.status, request.method, request.path)
        return response

    # =====================
    # Rate limiting (config)
    # =====================
    app.config['RATELIMIT_STORAGE_URI'] = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    app.config['RATELIMIT_HEADERS_ENABLED'] = _bool_env('RATELIMIT_HEADERS_ENABLED', True)

    # =====================
    # Inicializa extensões
    # =====================
    limiter.init_app(app)
    csrf.init_app(app)

    # ==============================
    # Variáveis globais nos templates
    # ==============================
    @app.context_processor
    def inject_globals():
        return {
            "APP_VERSION": app.config.get("APP_VERSION", "alpha 0.8"),
            "APP_CHANNEL": app.config.get("APP_CHANNEL", ""),
            "BUILD_TAG": app.config.get("BUILD_TAG", app.config.get("APP_VERSION", "")),
            "ENV_NAME": os.environ.get("FLASK_ENV", "development"),
        }

    # =================
    # Rotas do frontend
    # =================
    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/merge')
    def merge_page():
        return render_template('merge.html')

    @app.route('/split')
    def split_page():
        return render_template('split.html')

    @app.route('/compress')
    def compress_page():
        return render_template('compress.html')

    # ---- Health check dedicado (exempt do rate limit) ----
    @app.get('/healthz')
    @limiter.exempt
    def healthz():
        return jsonify(status='ok'), 200

    # ============ Compat legado ============
    @app.route('/edit/options')
    def legacy_edit_options():
        return redirect(url_for('edit_bp.edit'), code=301)

    # ====================
    # Registrar Blueprints
    # ====================
    from .routes.converter import converter_bp, convert_api_bp
    app.register_blueprint(converter_bp)
    app.register_blueprint(convert_api_bp)

    from .routes.merge import merge_bp
    from .routes.split import split_bp
    from .routes.compress import compress_bp
    from .routes.viewer import viewer_bp
    from .routes.preview import preview_bp
    from .routes.organize import organize_bp
    from .routes.edit import edit_bp

    app.register_blueprint(merge_bp)
    app.register_blueprint(split_bp)
    app.register_blueprint(compress_bp)
    app.register_blueprint(viewer_bp)
    app.register_blueprint(preview_bp)
    app.register_blueprint(organize_bp)
    app.register_blueprint(edit_bp)

    # ==================
    # Tratamento de erros
    # ==================
    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'CSRF', 'message': e.description}), 400
        return render_template('csrf_error.html', reason=e.description), 400

    @app.errorhandler(RequestEntityTooLarge)
    def handle_file_too_large(e):
        return jsonify({'error': 'Arquivo muito grande.'}), 413

    @app.errorhandler(BadRequest)
    def handle_bad_request(e: BadRequest):
        msg = getattr(e, "description", str(e)) or "Solicitação inválida."
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': msg}), 400
        return render_template_string(
            "<h1>400 — Solicitação inválida</h1><p>{{msg}}</p>", msg=msg
        ), 400

    @app.errorhandler(500)
    def handle_internal_error(e):
        if request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html:
            return jsonify({'error': 'Erro interno no servidor.'}), 500
        return render_template('internal_error.html'), 500

    # Logs informativos fora de request (ambiente/versão/.env)
    app.logger.info("RateLimit storage: %s", app.config.get('RATELIMIT_STORAGE_URI'))
    app.logger.info("FLASK_ENV=%s | dotenv=%s", env, loaded_env_file)
    app.logger.info("APP_VERSION=%s | BUILD_TAG=%s", app.config['APP_VERSION'], app.config['BUILD_TAG'])

    return app