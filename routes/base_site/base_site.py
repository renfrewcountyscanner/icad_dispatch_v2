from flask import Blueprint, request, jsonify, flash, redirect, url_for, render_template, current_app, session, abort

base_site = Blueprint('base_site', __name__)

@base_site.route('/')
def base_site_index():
    return redirect(url_for('base_site.base_site_login'))

@base_site.route('/login')
def base_site_login():
    return render_template('base_site/login.html')
