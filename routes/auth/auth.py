from flask import Blueprint, request, jsonify, flash, redirect, url_for, render_template, current_app, session

from lib.cookie_module import issue_cookie
from lib.user_module import authenticate_user, user_change_password, get_users
from routes.decorators import login_required, csrf_protect

auth = Blueprint('auth', __name__)

@auth.route('/login', methods=['POST'])
@csrf_protect
def auth_login():
    username = request.form.get("loginEmail", "").strip()
    password = request.form.get("loginPassword", "")
    remember = request.form.get("rememberMe") is not None     # ✅ checkbox

    if not username or not password:
        return jsonify(success=False, message="Malformed Request"), 400

    auth_result = authenticate_user(current_app.config['db'], username, password)
    if not auth_result.get('success'):
        return jsonify(success=False, message="Invalid credentials"), 401

    user_data = auth_result.get('result')

    resp = jsonify(success=True, message="Authenticated")

    # ── add the remember-me cookie, only if box ticked ───────────────────
    if remember:
        issue_cookie(resp, current_app.config["db"], user_data.get('user_id'))

    return resp


@auth.route("/logout", methods=['GET'])
def auth_logout():
    db = current_app.config["db"]
    if session.get("user_id"):
        db.execute_commit("DELETE FROM user_remember_tokens WHERE user_id=?", (session["user_id"],))
    session.clear()
    resp = jsonify(success=True)
    resp.delete_cookie("remember_me", path="/")
    return redirect(url_for("base_site.base_site_index"))


@auth.route("/change_password", methods=["POST"])
@login_required
@csrf_protect
def auth_change_password():
    log = current_app.config["logger"]

    # helper to decide output style
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    username = session.get("username")
    current_password = request.form.get("currentPassword", "")
    new_password     = request.form.get("newPassword", "")

    if not username:
        msg = "No username provided."
        log.error(msg)
        if not wants_json:
            return jsonify(success=False, message=msg), 400
        flash(msg, "error")
        return redirect(url_for("dashboard.dashboard_index"))

    if not new_password:
        msg = "No new password provided."
        log.error(msg)
        if wants_json:
            return jsonify(success=False, message=msg), 400
        flash(msg, "danger")
        return redirect(url_for("dashboard.dashboard_index"))

    # replace "admin" with session username if you support per-user change
    result = user_change_password(
        current_app.config["db"],
        username,
        current_password,
        new_password,
    )

    log.debug(result["message"])

    if wants_json:
        status = 200 if result["success"] else 400
        return jsonify(success=result["success"], message=result["message"]), status

    flash(result["message"], "success" if result["success"] else "danger")
    return redirect(url_for("dashboard.dashboard_index"))
