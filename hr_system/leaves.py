# hr_system/leaves.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, current_app
)
from werkzeug.exceptions import abort
from datetime import datetime, timedelta 
import pytz # Import pytz for explicit UTC times
import sqlite3 # Import for exception handling

from hr_system.auth import login_required, manager_required
from hr_system.db import get_db

bp = Blueprint('leaves', __name__, url_prefix='/leaves')

def calculate_leave_days(start_date_str, end_date_str):
    """Calculate the number of days between two dates (inclusive)."""
    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
        return (end_dt - start_dt).days + 1
    except (ValueError, TypeError):
        return 0

@bp.route('/')
@login_required
def view_leaves():
    """Show the logged-in user's leave requests and balance."""
    db = get_db()
    user_id = g.user['id']
    
    entitlement = g.user['annual_leave_entitlement'] or 0.0

    approved_leaves = db.execute(
        "SELECT start_date, end_date FROM leaves WHERE user_id = ? AND status = 'approved' AND leave_type = 'Vacation'",
        (user_id,)
    ).fetchall()

    approved_days_taken = sum(calculate_leave_days(l['start_date'], l['end_date']) for l in approved_leaves)
    remaining_balance = entitlement - approved_days_taken

    # Fetch timestamps as formatted UTC strings
    leave_requests = db.execute(
        '''SELECT id, leave_type, start_date, end_date, status, reason, 
                  strftime('%Y-%m-%d %H:%M:%S', created_at) as created_at_utc_str, 
                  actioned_by_user_id, 
                  strftime('%Y-%m-%d %H:%M:%S', actioned_at) as actioned_at_utc_str
           FROM leaves 
           WHERE user_id = ? ORDER BY created_at DESC''', 
        (user_id,)
    ).fetchall()
    
    return render_template(
        'leaves/leaves.html', 
        leave_requests=leave_requests,
        entitlement=entitlement,
        approved_days_taken=approved_days_taken,
        remaining_balance=remaining_balance
        )

@bp.route('/new', methods=('GET', 'POST'))
@login_required
def new_leave():
    """Handle creation of a new leave request, checking balance."""
    db = get_db()
    user_id = g.user['id']
    entitlement = g.user['annual_leave_entitlement'] or 0.0
    
    approved_leaves = db.execute(
        "SELECT start_date, end_date FROM leaves WHERE user_id = ? AND status = 'approved' AND leave_type = 'Vacation'", 
        (user_id,)
    ).fetchall()
    approved_days_taken = sum(calculate_leave_days(l['start_date'], l['end_date']) for l in approved_leaves)
    remaining_balance = entitlement - approved_days_taken

    if request.method == 'POST':
        leave_type = request.form['leave_type']
        start_date = request.form['start_date']
        end_date = request.form['end_date']
        reason = request.form.get('reason') 

        error = None
        if not leave_type or not start_date or not end_date:
             error = 'Leave Type, Start Date, and End Date are required.'
        elif start_date > end_date:
            error = 'Leave start date cannot be after the end date.'
        
        if not error:
            requested_days = calculate_leave_days(start_date, end_date)
            if requested_days <= 0:
                 error = "Invalid date range selected."
            elif leave_type == 'Vacation' and requested_days > remaining_balance:
                 error = f"Insufficient leave balance. Requested: {requested_days} days, Available: {remaining_balance} days."
            else:
                overlapping = db.execute(
                    '''SELECT id FROM leaves WHERE user_id = ? AND status != 'rejected' AND
                       NOT (end_date < ? OR start_date > ?)''',
                    (user_id, start_date, end_date)
                ).fetchone()
                if overlapping:
                    error = 'You have an overlapping leave request for this period.'
        if error:
            flash(error, 'error')
        else:
            # created_at is handled by DEFAULT CURRENT_TIMESTAMP (UTC in SQLite)
            # actioned_at is NULL until an action is taken
            db.execute(
                'INSERT INTO leaves (user_id, leave_type, start_date, end_date, reason) VALUES (?, ?, ?, ?, ?)',
                (user_id, leave_type, start_date, end_date, reason)
            )
            db.commit()
            flash('Leave request submitted successfully.', 'success')
            return redirect(url_for('leaves.view_leaves')) 

    return render_template('leaves/new_leave.html', remaining_balance=remaining_balance)

@bp.route('/manage')
@manager_required 
def manage_leaves():
    db = get_db()
    manager_user = g.user 
    leave_requests_to_manage = []
    
    # Define base fields including formatted UTC timestamps
    base_query_fields = '''
        l.id, l.user_id, l.leave_type, l.start_date, l.end_date, l.status, l.reason,
        strftime('%Y-%m-%d %H:%M:%S', l.created_at) as created_at_utc_str, 
        l.actioned_by_user_id, 
        strftime('%Y-%m-%d %H:%M:%S', l.actioned_at) as actioned_at_utc_str,
        u.full_name as employee_name, u.department as employee_department, u.manager_id as emp_manager_id
    '''

    if manager_user['role'] == 'admin':
        current_app.logger.debug("manage_leaves: Admin fetching all pending leaves.")
        leave_requests_to_manage = db.execute(
            f'''SELECT {base_query_fields}
               FROM leaves l JOIN users u ON l.user_id = u.id
               WHERE l.status = 'pending' 
               ORDER BY l.created_at DESC''' 
        ).fetchall()
    elif manager_user['role'] == 'manager':
        manager_id_to_check = manager_user['id'] 
        current_app.logger.debug(f"manage_leaves: Manager (ID: {manager_id_to_check}) fetching pending leaves for their reports.")
        leave_requests_to_manage = db.execute(
            f'''SELECT {base_query_fields}
               FROM leaves l JOIN users u ON l.user_id = u.id
               WHERE u.manager_id = ? AND l.status = 'pending' 
               ORDER BY l.created_at DESC''',
            (manager_id_to_check,) 
        ).fetchall()
    return render_template('leaves/manage_leaves.html', leave_requests=leave_requests_to_manage)

@bp.route('/<int:leave_id>/action', methods=('POST',))
@manager_required
def leave_action(leave_id):
    action = request.form.get('action') 
    db = get_db()
    leave_request = db.execute(
        'SELECT l.*, u.manager_id FROM leaves l JOIN users u ON l.user_id = u.id WHERE l.id = ?', 
        (leave_id,)
    ).fetchone()
    manager_user = g.user
    if not leave_request:
        flash('Leave request not found.', 'error')
        return redirect(url_for('leaves.manage_leaves'))
    if manager_user['role'] != 'admin' and leave_request['manager_id'] != manager_user['id']:
        flash('You are not authorized to manage this leave request.', 'error')
        return redirect(url_for('leaves.manage_leaves'))
    
    if action in ['approve', 'reject']:
        new_status = 'approved' if action == 'approve' else 'rejected'
        # Set actioned_at to current UTC time, explicitly formatted
        actioned_at_utc_str = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            db.execute(
                'UPDATE leaves SET status = ?, actioned_by_user_id = ?, actioned_at = ? WHERE id = ?', 
                (new_status, manager_user['id'], actioned_at_utc_str, leave_id)
            )
            db.commit()
            flash(f'Leave request has been {new_status}.', 'success')
        except sqlite3.Error as e:
            db.rollback()
            flash(f"Database error processing leave action: {e}", "error")
            current_app.logger.error(f"DB error in leave_action for leave_id {leave_id}: {e}")
    else:
        flash('Invalid action specified.', 'error')
    return redirect(url_for('leaves.manage_leaves'))
