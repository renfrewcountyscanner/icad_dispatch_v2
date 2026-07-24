import os
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask
from dotenv import load_dotenv
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix

from lib.postgres_module import PostgreSQLDatabase
from lib.utility import env_bool, choose_cookie_domain
from routes import base_site, auth, dashboard, bp_admin, api_systems, api_call_upload, bp_trig, bp_tone, bp_summary, bp_corrections, bp_operations, register_middlewares

from lib.logging_module import CustomLogger

app_name = "icad_dispatch"
__version__ = "2.5.1"
DEFAULT_TIMEZONE = "America/New_York"

load_dotenv()

IS_DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes", "on")
PUBLIC_DEPLOYMENT = env_bool("PUBLIC_DEPLOYMENT", default=False)
_proxy_hops_raw = os.getenv("TRUSTED_PROXY_HOPS", "1" if PUBLIC_DEPLOYMENT else "0")
try:
    TRUSTED_PROXY_HOPS = max(0, int(_proxy_hops_raw))
except ValueError:
    raise RuntimeError("TRUSTED_PROXY_HOPS must be a non-negative integer")

raw_tz = (os.getenv("TIMEZONE") or "").strip()

root_path = os.getcwd()
var_path = os.path.join(root_path, 'var')
log_path = os.path.join(root_path, 'log')
audio_path = os.path.join(root_path, 'static/audio')
log_file_name = f"{app_name}.log"

os.makedirs(log_path, exist_ok=True)
os.makedirs(var_path, exist_ok=True)
os.makedirs(audio_path, exist_ok=True)

# Start logger in debug mode
logger_instance = CustomLogger(os.getenv("LOG_LEVEL", 1), f'{app_name}', os.path.join(log_path, log_file_name))

main_logger = logger_instance.logger

# Init Database (PostgreSQL only)
db = None
try:
    db = PostgreSQLDatabase()
    main_logger.info("PostgreSQL Database connected successfully.")
except Exception as e:
    main_logger.error(f"PostgreSQL connection failed: {e}")
    time.sleep(5)
    sys.exit(1)

if db is None:
    main_logger.error("No database could be initialized.")
    sys.exit(1)

# ── Root user bootstrap (create admin on first run) ─────────────────────────
_root_username = os.getenv("ROOT_USERNAME", "root").strip()
_root_password = os.getenv("ROOT_PASSWORD", "").strip()

if _root_password:
    try:
        user_res = db.execute_query("SELECT COUNT(*) as cnt FROM users", fetch_mode="one")
        if user_res.get("success") and user_res["result"]["cnt"] == 0:
            import bcrypt
            from lib.user_module import set_session_keys
            hashed = bcrypt.hashpw(_root_password.encode("utf-8"), bcrypt.gensalt())
            db.execute_commit(
                "INSERT INTO users (user_username, user_password, is_admin, is_active) VALUES (?, ?, 1, 1)",
                (_root_username, hashed)
            )
            main_logger.warning(
                "Bootstrap: created root user '%s'. Change the password after first login.",
                _root_username
            )
    except Exception as e:
        main_logger.error("Bootstrap: failed to create root user: %s", e)

# ── Security: warn if default/weak secrets are in use ──────────────────────
_weak_secrets = [
    ("ROOT_PASSWORD", ["changeme", "password", "123", "admin"]),
    ("PUBLIC_MAP_API_KEY", ["icad-public-map", "change-me", "changeme"]),
    ("MAP_SECRET_KEY", ["change-me", "changeme", "default"]),
    ("PG_PASSWORD", ["icad_dispatch_password", "password", "123", "postgres"]),
]
for env_var, bad_patterns in _weak_secrets:
    val = os.getenv(env_var, "")
    if val and any(p.lower() in val.lower() for p in bad_patterns):
        main_logger.warning(
            "SECURITY: %s appears to be a weak/default value. "
            "Change it before exposing this service to the internet.",
            env_var
        )

if not raw_tz:
    tz_name = DEFAULT_TIMEZONE
else:
    # Require a valid IANA timezone name if provided
    try:
        ZoneInfo(raw_tz)  # validation only
        tz_name = raw_tz
    except ZoneInfoNotFoundError:
        main_logger.error(
            f"Invalid TIMEZONE={raw_tz!r}. Must be an IANA name like "
            f"'America/New_York', 'America/Chicago', 'UTC'."
        )
        time.sleep(2)
        sys.exit(1)

main_logger.info(f"Timezone set to: {tz_name}")

app = Flask(__name__)
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=TRUSTED_PROXY_HOPS,
    x_proto=TRUSTED_PROXY_HOPS,
    x_host=TRUSTED_PROXY_HOPS,
    x_prefix=TRUSTED_PROXY_HOPS,
)

secret_key_file = os.path.join(var_path, "secret_key.txt")
try:
    with open(secret_key_file, 'rb') as f:
        app.config['SECRET_KEY'] = f.read()
    # Ensure existing file has restrictive permissions
    os.chmod(secret_key_file, 0o600)
except FileNotFoundError:
    secret_key = os.urandom(24)
    # Create with restrictive permissions (owner read/write only)
    fd = os.open(secret_key_file, os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(secret_key)
    app.config['SECRET_KEY'] = secret_key


app.config['logger'] = main_logger
app.config['db'] = db
app.config['EXTERNAL_BASE_URL'] = os.getenv("BASE_URL")
app.config["TIMEZONE"] = tz_name
app.config["AUDIO_ARCHIVE_PATH"] = audio_path

# ─────────────── Sessions ───────────────
app.config['SESSION_TYPE']        = 'filesystem'
app.config['SESSION_FILE_DIR']    = os.path.join(var_path, 'sessions')
app.config['SESSION_PERMANENT']   = True
app.config['PERMANENT_SESSION_LIFETIME'] = int(os.getenv(
    "SESSION_LIFETIME_SECONDS", "43200" if PUBLIC_DEPLOYMENT else "604800"
))
app.config['SESSION_USE_SIGNER']  = True
app.config['SESSION_KEY_PREFIX']  = 'icad_dispatch_session:'

# ─────────────── Cookies ───────────────
# Secure only on HTTPS by default (or force via env)
app.config['SESSION_COOKIE_SECURE']   = env_bool(
    "SESSION_COOKIE_SECURE", default=PUBLIC_DEPLOYMENT
)
app.config['PUBLIC_DEPLOYMENT']       = PUBLIC_DEPLOYMENT
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_DOMAIN']   = choose_cookie_domain()  # None => host-only (works for IP/localhost)
app.config['SESSION_COOKIE_NAME']     = os.getenv("SESSION_COOKIE_NAME", "icad_dispatch")
app.config['SESSION_COOKIE_PATH']     = os.getenv("SESSION_COOKIE_PATH", "/")
app.config['SESSION_COOKIE_SAMESITE'] = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")

# Ensure session dir exists before Session() uses it
os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)

# Initializing the session
sess = Session()
sess.init_app(app)

app.static_folder = 'static'
app.template_folder = 'templates'

# Register base site /
app.register_blueprint(base_site, url_prefix='/')

app.register_blueprint(auth, url_prefix='/auth')

app.register_blueprint(dashboard, url_prefix='/dashboard')

app.register_blueprint(bp_admin, url_prefix='/admin')

app.register_blueprint(api_systems, url_prefix='/api/systems')

app.register_blueprint(api_call_upload, url_prefix='/api/call-upload')

app.register_blueprint(bp_tone, url_prefix='/api/tone-finder')

app.register_blueprint(bp_trig, url_prefix='/api/trigger-calls')

app.register_blueprint(bp_summary, url_prefix='/api/summary')

app.register_blueprint(bp_corrections, url_prefix='/api')
app.register_blueprint(bp_operations, url_prefix='/api/operations')

# Register Middleware
register_middlewares(app)

@app.context_processor
def inject_version():
    return dict(app_version=__version__)

# ── JSON error handlers for API routes ────────────────────────────
from flask import jsonify, request as flask_request

@app.errorhandler(Exception)
def handle_exception(e):
    """Return JSON for API routes, HTML for everything else."""
    path = flask_request.path if flask_request else ""
    if path.startswith("/api/"):
        main_logger.error("Unhandled API exception: %s", e, exc_info=True)
        return jsonify(success=False, message="Internal server error"), 500
    # Re-raise to let Flask's default HTML handler deal with non-API routes
    raise e

@app.errorhandler(404)
def handle_404(e):
    if flask_request.path.startswith("/api/"):
        return jsonify(success=False, message="Not found"), 404
    return e

@app.errorhandler(405)
def handle_405(e):
    if flask_request.path.startswith("/api/"):
        return jsonify(success=False, message="Method not allowed"), 405
    return e


main_logger.info("+++++++++====================++++++++++++")
main_logger.info(f"iCAD Dispatch")
main_logger.info(f"Version {__version__}")
main_logger.info(f"------")
main_logger.info(f"Log Path {log_path}")
main_logger.info(f"Var Path {var_path}")
main_logger.info(f"Audio Path {audio_path}")
main_logger.info(f"------")
main_logger.info("+++++++++====================++++++++++++")
main_logger.info("  ")
main_logger.info("\n=== URL MAP ===")
for r in app.url_map.iter_rules():
    main_logger.info(f"{r!s:30}  ->  {r.endpoint}")
main_logger.info("================\n")

if IS_DEBUG:
    if __name__ == '__main__':
        app.run(host='0.0.0.0', port=5600, debug=True, threaded=True, use_reloader=False)
