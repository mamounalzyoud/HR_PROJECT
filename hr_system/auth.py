# hr_system/auth.py

import functools
from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, current_app
)
from werkzeug.security import check_password_hash, generate_password_hash
from hr_system.db import get_db
from datetime import datetime # Import datetime

bp = Blueprint('auth', __name__, url_prefix='/auth')

# --- Decorators ---
def login_required(view):
    """View decorator that redirects anonymous users to the login page."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('auth.login'))
        return view(**kwargs)
    return wrapped_view

def admin_required(view):
    """View decorator that restricts access to admin users."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
             flash('Please log in to access this page.', 'error')
             return redirect(url_for('auth.login'))
        if g.user['role'] != 'admin': # g.user is a sqlite3.Row, access via['key']
            flash('Admin access required for this page.', 'error')
            return redirect(url_for('main.dashboard'))
        return view(**kwargs)
    return wrapped_view

def manager_required(view):
    """View decorator that restricts access to managers and admins."""
    @functools.wraps(view)
    def wrapped_view(**kwargs):
        if g.user is None:
             flash('Please log in to access this page.', 'error')
             return redirect(url_for('auth.login'))
        if g.user['role'] not in ['admin', 'manager']: # g.user is a sqlite3.Row
            flash('Manager or Admin access required for this page.', 'error')
            return redirect(url_for('main.dashboard'))
        return view(**kwargs)
    return wrapped_view

# --- Routes ---
@bp.route('/login', methods=('GET', 'POST'))
def login():
    """Handles user login."""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        error = None
        # Ensure all necessary fields including 'timezone' are selected
        user = db.execute(
            'SELECT * FROM users WHERE username = ?', (username,)
        ).fetchone()

        if user is None or not check_password_hash(user['password'], password):
            error = 'Incorrect username or password.'

        if error is None:
            session.clear()
            session['user_id'] = user['id']
            # Store user's preferred timezone in session if it exists in the user record
            if user['timezone']: # Check if the timezone field has a value
                session['user_timezone'] = user['timezone']
                current_app.logger.info(f"User {user['username']} logged in. Timezone '{user['timezone']}' loaded into session.")
            else:
                # If user has no timezone set, clear it from session or set to app default
                session.pop('user_timezone', None) # Remove if exists
                current_app.logger.info(f"User {user['username']} logged in. No timezone preference set in profile. Session timezone cleared.")


            flash(f"Welcome back, {user['full_name']}!", "success")
            return redirect(url_for('main.dashboard'))

        flash(error, 'error')
        return render_template('auth/login.html', now=datetime.utcnow())


    return render_template('auth/login.html', now=datetime.utcnow())

@bp.route('/logout')
def logout():
    """Clear the current session, including the user id and timezone."""
    session.pop('user_id', None)
    session.pop('user_timezone', None) # Also clear user_timezone from session
    g.user = None
    g.user_timezone = None # Clear from g as well
    flash("You have been successfully logged out.", "info")
    return redirect(url_for('index'))

@bp.before_app_request
def load_logged_in_user():
    """If a user id is stored in the session, load the user object from
    the database into ``g.user``. Also loads user timezone preference."""
    user_id = session.get('user_id')

    if user_id is None:
        g.user = None
        g.user_timezone = current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC') # Default for anonymous
    else:
        # Ensure all necessary fields including 'timezone' are selected
        g.user = get_db().execute(
            'SELECT * FROM users WHERE id = ?', (user_id,)
        ).fetchone()

        if g.user and g.user['timezone']:
            g.user_timezone = g.user['timezone']
            # Ensure session is also up-to-date if user is loaded
            session['user_timezone'] = g.user['timezone']
        elif 'user_timezone' in session: # If it's in session but not (or no longer) on user record
             g.user_timezone = session['user_timezone']
        else:
            g.user_timezone = current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
            session.pop('user_timezone', None) # Clear from session if not in user profile and not already set

    # current_app.logger.debug(f"Loaded user: {g.user['username'] if g.user else 'None'}. Timezone in g: {g.user_timezone}")
