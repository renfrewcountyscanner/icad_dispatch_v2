# routes/__init__.py

from flask import Flask
from routes.middleware import log_ip, inject_csrf_token, log_request_path, load_remembered_user, attach_rotated_cookie
from routes.base_site.base_site import base_site
from routes.auth.auth import auth
from routes.dashboard.dashboard import dashboard
from routes.admin import bp_admin
from routes.api.systems import api_systems
from routes.api.call_upload import api_call_upload
from routes.api.tone_finder import bp_tone
from routes.api.trigger_calls import bp_trig


def register_middlewares(app: Flask):
    """Registers global middlewares for the Flask app."""
    app.before_request(log_ip)  # Log IP address before every request
    app.before_request(log_request_path) # Log Request path
    app.before_request(load_remembered_user)
    app.after_request(attach_rotated_cookie)
    app.context_processor(inject_csrf_token)  # Inject CSRF token into templates
