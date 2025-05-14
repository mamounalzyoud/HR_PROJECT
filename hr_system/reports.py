# hr_system/reports.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.exceptions import abort
from datetime import datetime, timedelta

from hr_system.auth import login_required, manager_required
from hr_system.db import get_db

# Import or define the helper function
# from hr_system.leaves import calculate_leave_days # Assumes helper exists
# OR define locally:
def calculate_leave_days(start_date_str, end_date_str):
    """Calculate the number of days between two dates (inclusive)."""
    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
        return (end_dt - start_dt).days + 1
    except (ValueError, TypeError):
        return 0

bp = Blueprint('reports', __name__, url_prefix='/reports')

@bp.route('/')
@manager_required
def index():
    """Show the main reports dashboard."""
    return render_template('reports/reports.html')

@bp.route('/attendance', methods=('GET',))
@manager_required
def attendance_report():
    """Generate and display the attendance report."""
    db = get_db()
    manager_user = g.user
    today = datetime.now()
    start_date_str = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', today.strftime('%Y-%m-%d'))

    users_for_report = []
    if manager_user['role'] == 'admin':
        users_for_report = db.execute(
            'SELECT id, full_name, department FROM users ORDER BY full_name'
            ).fetchall()
    else:
        users_for_report = db.execute(
            'SELECT id, full_name, department FROM users WHERE department = ? ORDER BY full_name',
            (manager_user['department'],)
        ).fetchall()

    report_data = []
    for user_item in users_for_report:
        summary = db.execute(
            '''SELECT SUM(hours_worked) as total_hours,
                      SUM(CASE WHEN status = 'present' THEN 1 ELSE 0 END) as present_days
               FROM attendance WHERE user_id = ? AND date BETWEEN ? AND ?''',
            (user_item['id'], start_date_str, end_date_str)
        ).fetchone()

        standard_hours_period = 0
        try:
            current_date = datetime.strptime(start_date_str, '%Y-%m-%d')
            end_date_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
            while current_date <= end_date_dt:
                if current_date.weekday() < 5:
                    standard_hours_period += 8
                current_date += timedelta(days=1)
        except ValueError:
             flash("Invalid date format provided for filtering.", "warning")
             standard_hours_period = 0

        total_worked = summary['total_hours'] if summary and summary['total_hours'] is not None else 0
        overtime_hours = max(0, total_worked - standard_hours_period)

        report_data.append({
            'user': user_item,
            'summary': summary,
            'standard_hours': standard_hours_period,
            'overtime_hours': overtime_hours
        })

    return render_template(
        'reports/attendance_report.html',
        report_data=report_data,
        start_date=start_date_str,
        end_date=end_date_str
    )

@bp.route('/leave', methods=('GET',))
@manager_required
def leave_report():
    """Generate and display the leave report including balances."""
    db = get_db()
    manager_user = g.user
    today = datetime.now()
    start_date_str = request.args.get('start_date', today.replace(month=1, day=1).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', today.strftime('%Y-%m-%d'))

    users_for_report = []
    if manager_user['role'] == 'admin':
        # Fetch entitlement along with other user details
        users_for_report = db.execute(
            'SELECT id, full_name, department, annual_leave_entitlement FROM users ORDER BY full_name'
            ).fetchall()
    else:
        users_for_report = db.execute(
            'SELECT id, full_name, department, annual_leave_entitlement FROM users WHERE department = ? ORDER BY full_name',
            (manager_user['department'],)
        ).fetchall()

    report_data = []
    for user_item in users_for_report:
        user_id = user_item['id']
        entitlement = user_item['annual_leave_entitlement'] or 0.0

        # Fetch approved leave summary within the date range (for display)
        # Note: This date range filtering for summary might not be perfect for balance calculation
        leave_summary = db.execute(
            '''SELECT leave_type, COUNT(*) as count,
                      SUM(JULIANDAY(end_date) - JULIANDAY(start_date) + 1) as total_days
               FROM leaves
               WHERE user_id = ? AND status = 'approved' AND leave_type = 'Vacation' AND
                     ( (start_date BETWEEN ? AND ?) OR (end_date BETWEEN ? AND ?) OR
                       (start_date < ? AND end_date > ?) )
               GROUP BY leave_type ORDER BY leave_type''',
            (user_id, start_date_str, end_date_str, start_date_str, end_date_str, start_date_str, end_date_str)
        ).fetchall() # This summary is for the REPORT PERIOD

        # Calculate total approved days taken (ALL TIME for balance)
        all_approved_leaves = db.execute(
             "SELECT start_date, end_date FROM leaves WHERE user_id = ? AND status = 'approved' AND leave_type = 'Vacation'",
             (user_id,)
        ).fetchall()
        approved_days_taken = sum(calculate_leave_days(l['start_date'], l['end_date']) for l in all_approved_leaves)
        remaining_balance = entitlement - approved_days_taken

        report_data.append({
            'user': user_item,
            'leave_summary': leave_summary, # Summary for the selected period
            'entitlement': entitlement,
            'approved_taken_total': approved_days_taken, # Total taken ever
            'remaining_balance': remaining_balance # Current balance
            })

    return render_template(
        'reports/leave_report.html',
        report_data=report_data,
        start_date=start_date_str,
        end_date=end_date_str
    )

