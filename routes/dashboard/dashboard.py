from flask import Blueprint, request, jsonify, flash, redirect, url_for, render_template, current_app, session, abort

from routes.decorators import login_required

dashboard = Blueprint('dashboard', __name__)

@dashboard.route('', strict_slashes=False)
@login_required
def dashboard_index():
    return render_template('dashboard/dashboard.html')

@dashboard.route('/change_password', methods=['GET'])
@login_required
def dashboard_change_password():
    return render_template('dashboard/change_password.html')

@dashboard.route('/systems', methods=['GET'])
@login_required
def dashboard_edit_systems():
    return render_template('dashboard/edit_systems.html')

@dashboard.route('/triggers', methods=['GET'])
@login_required
def dashboard_edit_triggers():
    return render_template('dashboard/edit_triggers.html')

@dashboard.route("/debug", methods=["GET"])
@login_required
def debug_page():
    return render_template("dashboard/debug.html")

@dashboard.route("/upload", methods=["GET"])
@login_required
def dashboard_upload():
    from lib.sqlite_module import SQLiteDatabase
    from lib.system_module import get_systems

    db = SQLiteDatabase()
    systems = get_systems(db)
    return render_template("dashboard/upload.html", systems=systems)

@dashboard.route("/summary", methods=["GET"])
@login_required
def dashboard_summary():
    from lib.sqlite_module import SQLiteDatabase
    from lib.system_module import get_systems

    db = SQLiteDatabase()
    systems = get_systems(db)
    return render_template("dashboard/summary.html", systems=systems.get("result", []))


