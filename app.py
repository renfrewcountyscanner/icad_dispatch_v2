import os
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask
from dotenv import load_dotenv
from flask_session import Session
from werkzeug.middleware.proxy_fix import ProxyFix

from lib.sqlite_module import SQLiteDatabase
from lib.utility import env_bool, choose_cookie_domain
from routes import base_site, auth, dashboard, bp_admin, api_systems, api_call_upload, bp_trig, bp_tone, register_middlewares

from lib.logging_module import CustomLogger

app_name = "icad_dispatch"
__version__ = "2.5.1"
DEFAULT_TIMEZONE = "America/New_York"

load_dotenv()

IS_DEBUG = os.getenv("DEBUG", "").lower() in ("true", "1", "yes", "on")

raw_tz = (os.getenv("TIMEZONE") or "").strip()

root_path = os.getcwd()
var_path = os.path.join(root_path, 'var')
log_path = os.path.join(root_path, 'log')
audio_path = os.path.join(root_path, 'static/audio')
log_file_name = f"{app_name}.log"

if not os.path.exists(log_path):
    os.makedirs(log_path)

if not os.path.exists(var_path):
    os.makedirs(var_path)

if not os.path.exists(audio_path):
    os.makedirs(audio_path)

# Start logger in debug mode
logger_instance = CustomLogger(os.getenv("LOG_LEVEL", 1), f'{app_name}', os.path.join(log_path, log_file_name))

main_logger = logger_instance.logger

# Init Database
try:
    db = SQLiteDatabase()
    main_logger.info("SQLite Database connected successfully.")

except ValueError as e:
    # Typically raised if db_path is invalid or empty
    main_logger.error(f"Invalid SQLite database path: {e}")
    time.sleep(5)
    sys.exit(1)

except IsADirectoryError as e:
    # Raised if db_path is actually a directory
    main_logger.error(f"Database path is a directory, not a file: {e}")
    time.sleep(5)
    sys.exit(1)

except sqlite3.Error as e:
    # Raised if there's a lower-level SQLite error (I/O issues, etc.)
    main_logger.error(f"SQLite error during database initialization: {e}")
    time.sleep(5)
    sys.exit(1)

except Exception as e:
    # Catch-all for anything else unexpected
    main_logger.error(f"Unexpected error while connecting to the database: {e}")
    time.sleep(5)
    sys.exit(1)

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
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

try:
    with open(os.path.join(var_path, "secret_key.txt"), 'rb') as f:
        app.config['SECRET_KEY'] = f.read()
except FileNotFoundError:
    secret_key = os.urandom(24)
    with open(os.path.join(var_path, "secret_key.txt"), 'wb') as f:
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
app.config['SESSION_PERMANENT']   = False
app.config['SESSION_USE_SIGNER']  = True
app.config['SESSION_KEY_PREFIX']  = 'icad_dispatch_session:'

# ─────────────── Cookies ───────────────
# Secure only on HTTPS by default (or force via env)
app.config['SESSION_COOKIE_SECURE']   = env_bool("SESSION_COOKIE_SECURE", default=False)
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


# Register Middleware
register_middlewares(app)

@app.context_processor
def inject_version():
    return dict(app_version=__version__)

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
