# hr_system/main.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, url_for, jsonify, current_app
)
from werkzeug.exceptions import abort
from werkzeug.security import check_password_hash, generate_password_hash
import sqlite3
from datetime import datetime, date, timedelta
import pytz 

from hr_system.auth import login_required
from hr_system.db import get_db
from hr_system.onboarding import trigger_pending_task_reminders
from hr_system.utils import format_datetime_user_timezone


bp = Blueprint('main', __name__)

@bp.route('/')
@login_required
def index():
    return redirect(url_for('main.dashboard'))

@bp.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    user_id = g.user['id']
    user_role = g.user['role']
    # user_timezone_str = g.user_timezone # This is set by auth.py

    try:
        trigger_pending_task_reminders(db, user_id)
        current_app.logger.info(f"Proactive reminder check triggered for user {user_id} from dashboard.")
    except Exception as e_reminder:
        current_app.logger.error(f"Error triggering proactive reminders from dashboard for user {user_id}: {e_reminder}")

    active_clock_entry = db.execute(
        "SELECT id, strftime('%Y-%m-%d %H:%M:%S', clock_in) as clock_in_utc_str FROM time_clock WHERE user_id = ? AND status = ? ORDER BY clock_in DESC LIMIT 1",
        (user_id, 'active')
    ).fetchone()
    
    is_clocked_in = active_clock_entry is not None
    clock_in_display_time_str = None 
    hours_since_clock_in = None

    if is_clocked_in:
        clock_in_utc_str = active_clock_entry['clock_in_utc_str']
        try:
            clock_in_display_time_str = format_datetime_user_timezone(clock_in_utc_str, None, '%I:%M %p')
            clock_in_naive_dt = datetime.strptime(clock_in_utc_str, '%Y-%m-%d %H:%M:%S')
            clock_in_aware_utc = pytz.utc.localize(clock_in_naive_dt)
            now_aware_utc = datetime.now(pytz.utc)
            duration = now_aware_utc - clock_in_aware_utc
            hours_since_clock_in = round(duration.total_seconds() / 3600, 1)
        except ValueError as ve:
            current_app.logger.error(f"Dashboard: Error parsing or formatting clock_in_utc_str '{clock_in_utc_str}' for user {user_id}: {ve}")
            clock_in_display_time_str = "Error"
            hours_since_clock_in = "N/A"
        except Exception as e_format:
            current_app.logger.error(f"Dashboard: General error processing clock_in time for user {user_id}: {e_format}")
            clock_in_display_time_str = "Error"
            hours_since_clock_in = "N/A"

    annual_entitlement = g.user['annual_leave_entitlement'] if g.user and 'annual_leave_entitlement' in g.user.keys() and g.user['annual_leave_entitlement'] is not None else 0.0
    
    approved_vacation_taken_query = """
        SELECT SUM(JULIANDAY(end_date) - JULIANDAY(start_date) + 1) as total_taken
        FROM leaves
        WHERE user_id = ? AND leave_type = 'Vacation' AND status = 'approved'
    """
    approved_vacation_taken_row = db.execute(approved_vacation_taken_query, (user_id,)).fetchone()
    total_vacation_taken = approved_vacation_taken_row['total_taken'] if approved_vacation_taken_row and approved_vacation_taken_row['total_taken'] else 0
    leave_balance = annual_entitlement - total_vacation_taken

    recent_attendance = db.execute(
        'SELECT date, status, hours_worked FROM attendance WHERE user_id = ? ORDER BY date DESC LIMIT 1',
        (user_id,)
    ).fetchone()

    recent_leave_request = db.execute(
        # Fetch created_at as UTC string for potential display or sorting consistency
        '''SELECT leave_type, start_date, end_date, status, 
                  strftime('%Y-%m-%d %H:%M:%S', created_at) as created_at_utc_str 
           FROM leaves WHERE user_id = ? ORDER BY created_at DESC LIMIT 1''',
        (user_id,)
    ).fetchone()

    active_benefits = db.execute(
        "SELECT benefit_type, details FROM benefits WHERE user_id = ? AND status = 'active' ORDER BY start_date DESC LIMIT 2",
        (user_id,)
    ).fetchall()

    recent_announcements_raw = db.execute(
        "SELECT title, content, strftime('%Y-%m-%d %H:%M:%S', created_at) as created_at_utc_str FROM announcements ORDER BY created_at DESC LIMIT 2"
    ).fetchall()
    
    pending_onboarding_tasks_count = db.execute(
        "SELECT COUNT(id) FROM employee_onboarding_status WHERE employee_user_id = ? AND status = 'Pending'",
        (user_id,)
    ).fetchone()[0]

    team_members = None
    pending_team_leaves = None
    if user_role in ['manager', 'admin']:
        team_query = "SELECT id, full_name, role, department FROM users WHERE manager_id = ?"
        team_params = (user_id,)
        if user_role == 'admin':
            team_query = "SELECT id, full_name, role, department FROM users WHERE role != 'admin' ORDER BY full_name" 
            team_params = ()
        team_members = db.execute(team_query, team_params).fetchall()

        leave_status_filter = "('pending')"
        pending_leaves_query = f"""
            SELECT l.id, u.full_name as employee_name, l.leave_type, l.start_date, l.end_date,
                   strftime('%Y-%m-%d %H:%M:%S', l.created_at) as created_at_utc_str
            FROM leaves l JOIN users u ON l.user_id = u.id
            WHERE l.status IN {leave_status_filter}
        """
        pending_leaves_params = []
        if user_role == 'manager':
            pending_leaves_query += " AND u.manager_id = ?"
            pending_leaves_params.append(user_id)
        pending_leaves_query += " ORDER BY l.start_date ASC LIMIT 3"
        pending_team_leaves = db.execute(pending_leaves_query, tuple(pending_leaves_params)).fetchall()

    # Define Quick Actions based on role
    quick_actions = []
    if user_role == 'employee':
        quick_actions.extend([
            {'label': 'Request Leave', 'url': url_for('leaves.new_leave'), 'icon': 'fa-plane-departure'},
            {'label': 'Submit Expense', 'url': url_for('expenses.submit_expense'), 'icon': 'fa-receipt'},
            {'label': 'View My Payslips', 'url': url_for('payroll.view_my_payslips'), 'icon': 'fa-file-invoice-dollar'},
        ])
    elif user_role == 'manager':
        quick_actions.extend([
            {'label': 'Manage Team Leaves', 'url': url_for('leaves.manage_leaves'), 'icon': 'fa-user-clock'},
            {'label': 'Manage Team Expenses', 'url': url_for('expenses.manage_expenses'), 'icon': 'fa-cash-register'},
            {'label': 'Team Attendance', 'url': url_for('attendance.team_attendance'), 'icon': 'fa-users'},
            {'label': 'Team Performance', 'url': url_for('performance.manage_performance'), 'icon': 'fa-clipboard-check'},
        ])
    elif user_role == 'admin':
        quick_actions.extend([
            {'label': 'User Management', 'url': url_for('users.view_users'), 'icon': 'fa-user-edit'},
            {'label': 'Run Payroll', 'url': url_for('payroll.view_payroll_runs'), 'icon': 'fa-calculator'},
            {'label': 'Manage Onboarding', 'url': url_for('onboarding.admin_tracking_view'), 'icon': 'fa-project-diagram'},
            {'label': 'View Reports', 'url': url_for('reports.index'), 'icon': 'fa-chart-bar'},
        ])
    # Common action for all logged-in users
    quick_actions.append({'label': 'View My Profile', 'url': url_for('main.profile'), 'icon': 'fa-id-card'})


    return render_template(
        'main/dashboard.html',
        is_clocked_in=is_clocked_in,
        clock_in_time=clock_in_display_time_str, 
        hours_since_clock_in=hours_since_clock_in,
        leave_balance=leave_balance,
        recent_attendance=recent_attendance, 
        recent_leave_request=recent_leave_request, 
        active_benefits=active_benefits,
        recent_announcements=recent_announcements_raw, 
        team_members=team_members,
        pending_team_leaves=pending_team_leaves, 
        pending_onboarding_tasks_count=pending_onboarding_tasks_count,
        quick_actions=quick_actions # Pass quick actions to template
    )


@bp.route('/profile', methods=('GET', 'POST'))
@login_required
def profile():
    db = get_db()
    user_id = g.user['id']
    available_timezones = pytz.common_timezones

    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        phone_number = request.form.get('phone_number')
        address = request.form.get('address')
        emergency_contact_name = request.form.get('emergency_contact_name')
        emergency_contact_phone = request.form.get('emergency_contact_phone')
        user_selected_timezone = request.form.get('timezone') 

        user_for_update = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
        error = None

        if not full_name: error = 'Full name is required.'
        elif not email: error = 'Email is required.'
        if user_selected_timezone and user_selected_timezone not in pytz.common_timezones:
            error = 'Invalid timezone selected.'
        
        if new_password:
            if not current_password:
                error = 'Current password is required to set a new password.'
            elif not check_password_hash(user_for_update['password'], current_password):
                error = 'Incorrect current password.'
            elif new_password != confirm_password:
                error = 'New passwords do not match.'
            
            if not error:
                try:
                    db.execute(
                        'UPDATE users SET password = ? WHERE id = ?',
                        (generate_password_hash(new_password), user_id)
                    )
                    flash('Password updated successfully.', 'success')
                except sqlite3.Error as e:
                    error = f"Database error updating password: {e}"
                    db.rollback() # Rollback if password update fails before profile update

        if not error: # Proceed only if password update (if attempted) was successful or not attempted
            try:
                db.execute(
                    '''UPDATE users SET full_name = ?, email = ?, phone_number = ?,
                       address = ?, emergency_contact_name = ?, emergency_contact_phone = ?,
                       timezone = ?
                       WHERE id = ?''',
                    (full_name, email, phone_number, address, emergency_contact_name, emergency_contact_phone,
                     user_selected_timezone if user_selected_timezone else user_for_update['timezone'], # Keep old if empty
                     user_id)
                )
                db.commit()
                flash('Profile updated successfully.', 'success')
                
                updated_user_data = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
                if updated_user_data:
                    g.user = updated_user_data 
                    if updated_user_data['timezone']:
                        session['user_timezone'] = updated_user_data['timezone']
                        g.user_timezone = updated_user_data['timezone']
                    else:
                        session.pop('user_timezone', None)
                        g.user_timezone = current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
                return redirect(url_for('main.profile'))
            except sqlite3.IntegrityError:
                error = f"Email '{email}' may already be registered by another user."
                db.rollback()
            except sqlite3.Error as e:
                error = f"Database error updating profile: {e}"
                db.rollback()
        
        if error:
            flash(error, 'error')

    user_data_for_template = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone() # Re-fetch fresh data
    if user_data_for_template is None: # Should not happen if user is logged in
        flash('User not found.', 'error')
        return redirect(url_for('auth.logout'))

    return render_template('main/profile.html',
                           user_profile=user_data_for_template,
                           available_timezones=available_timezones)


@bp.route('/api/time-clock/status')
@login_required
def api_time_clock_status():
    db = get_db()
    user_id = g.user['id']
    active_clock_entry = db.execute(
        "SELECT id, strftime('%Y-%m-%d %H:%M:%S', clock_in) as clock_in_utc_str FROM time_clock WHERE user_id = ? AND status = ? ORDER BY clock_in DESC LIMIT 1",
        (user_id, 'active')
    ).fetchone()

    is_clocked_in = active_clock_entry is not None
    clock_in_time_utc_iso_str = None 
    if is_clocked_in:
        clock_in_time_utc_iso_str = active_clock_entry['clock_in_utc_str']
    return jsonify({
        'is_clocked_in': is_clocked_in,
        'clock_in_time': clock_in_time_utc_iso_str 
    })
