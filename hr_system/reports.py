# hr_system/reports.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, current_app, jsonify
)
from werkzeug.exceptions import abort
from datetime import datetime, timedelta, date 
import pytz 
from collections import defaultdict 
import calendar # For month names

from hr_system.auth import login_required, manager_required, admin_required
from hr_system.db import get_db

bp = Blueprint('reports', __name__, url_prefix='/reports')

def calculate_leave_days(start_date_str, end_date_str):
    """Calculate the number of days between two dates (inclusive)."""
    try:
        start_dt = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date_str, '%Y-%m-%d')
        return (end_dt - start_dt).days + 1
    except (ValueError, TypeError):
        return 0

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
    user_timezone_str = g.user_timezone if hasattr(g, 'user_timezone') and g.user_timezone else current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
    try:
        user_local_tz = pytz.timezone(user_timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        current_app.logger.warning(f"Unknown timezone '{user_timezone_str}' for user {manager_user['id'] if manager_user else 'Unknown'}. Defaulting to UTC for 'today'.")
        user_local_tz = pytz.utc
    
    today = datetime.now(user_local_tz) 
    start_date_str = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', today.strftime('%Y-%m-%d'))

    users_for_report = []
    if manager_user['role'] == 'admin':
        users_for_report = db.execute(
            'SELECT id, full_name, department FROM users WHERE role != "admin" ORDER BY full_name'
            ).fetchall()
    elif manager_user['role'] == 'manager': 
        users_for_report = db.execute(
            'SELECT id, full_name, department FROM users WHERE manager_id = ? ORDER BY full_name',
            (manager_user['id'],)
        ).fetchall()
    else: 
        flash("You are not authorized to view this report.", "error")
        return redirect(url_for('main.dashboard'))

    report_data = []
    chart_labels = []
    chart_overtime_data = []
    chart_total_hours_data = []
    chart_standard_hours_data = []

    for user_item in users_for_report:
        summary = db.execute(
            '''SELECT SUM(hours_worked) as total_hours,
                      SUM(CASE WHEN status = 'present' THEN 1 ELSE 0 END) as present_days
               FROM attendance WHERE user_id = ? AND date BETWEEN ? AND ?''',
            (user_item['id'], start_date_str, end_date_str)
        ).fetchone()

        standard_hours_period = 0
        try:
            current_date_dt = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date_dt = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            temp_date = current_date_dt
            while temp_date <= end_date_dt:
                if temp_date.weekday() < 5: 
                    standard_hours_period += 8 
                temp_date += timedelta(days=1)
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
        
        chart_labels.append(user_item['full_name'])
        chart_overtime_data.append(overtime_hours)
        chart_total_hours_data.append(total_worked)
        chart_standard_hours_data.append(standard_hours_period)

    attendance_chart_data = {
        "labels": chart_labels,
        "datasets": [
            {
                "label": "Total Hours Worked",
                "data": chart_total_hours_data,
                "backgroundColor": "rgba(54, 162, 235, 0.6)", 
                "borderColor": "rgba(54, 162, 235, 1)",
                "borderWidth": 1
            },
            {
                "label": "Standard Hours",
                "data": chart_standard_hours_data,
                "backgroundColor": "rgba(75, 192, 192, 0.6)", 
                "borderColor": "rgba(75, 192, 192, 1)",
                "borderWidth": 1
            },
            {
                "label": "Overtime Hours",
                "data": chart_overtime_data,
                "backgroundColor": "rgba(255, 99, 132, 0.6)", 
                "borderColor": "rgba(255, 99, 132, 1)",
                "borderWidth": 1
            }
        ]
    }
    return render_template(
        'reports/attendance_report.html',
        report_data=report_data,
        start_date=start_date_str,
        end_date=end_date_str,
        attendance_chart_data=attendance_chart_data
    )

@bp.route('/leave', methods=('GET',))
@manager_required 
def leave_report():
    db = get_db()
    manager_user = g.user
    user_timezone_str = g.user_timezone if hasattr(g, 'user_timezone') and g.user_timezone else current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
    try:
        user_local_tz = pytz.timezone(user_timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        user_local_tz = pytz.utc 
    
    today = datetime.now(user_local_tz)
    # Filter for leave taken within a period
    filter_start_date_str = request.args.get('filter_start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    filter_end_date_str = request.args.get('filter_end_date', today.strftime('%Y-%m-%d'))

    users_for_report = []
    if manager_user['role'] == 'admin':
        users_for_report = db.execute(
            'SELECT id, full_name, department, annual_leave_entitlement FROM users WHERE role != "admin" ORDER BY full_name'
            ).fetchall()
    elif manager_user['role'] == 'manager':
        users_for_report = db.execute(
            'SELECT id, full_name, department, annual_leave_entitlement FROM users WHERE manager_id = ? ORDER BY full_name',
            (manager_user['id'],)
        ).fetchall()
    else:
        flash("You are not authorized to view this report.", "error")
        return redirect(url_for('main.dashboard'))

    report_data = []
    chart_labels = []
    chart_entitlement_data = []
    chart_taken_data = []
    chart_balance_data = []

    for user_item in users_for_report:
        user_id = user_item['id']
        entitlement = user_item['annual_leave_entitlement'] or 0.0

        # Leave taken within the filtered period (for "Taken in Period" column)
        leave_taken_in_period_list = db.execute(
            """SELECT start_date, end_date FROM leaves
               WHERE user_id = ? AND status = 'approved' AND leave_type = 'Vacation' AND
                     ( (start_date BETWEEN ? AND ?) OR (end_date BETWEEN ? AND ?) OR
                       (start_date < ? AND end_date > ?) )""", # Complex overlap check
            (user_id, filter_start_date_str, filter_end_date_str, filter_start_date_str, filter_end_date_str, filter_start_date_str, filter_end_date_str)
        ).fetchall()
        
        taken_in_period_days = 0
        for leave in leave_taken_in_period_list:
            # Adjust start/end dates to be within the filter period for accurate day count
            actual_start = max(datetime.strptime(leave['start_date'], '%Y-%m-%d').date(), datetime.strptime(filter_start_date_str, '%Y-%m-%d').date())
            actual_end = min(datetime.strptime(leave['end_date'], '%Y-%m-%d').date(), datetime.strptime(filter_end_date_str, '%Y-%m-%d').date())
            if actual_start <= actual_end:
                taken_in_period_days += (actual_end - actual_start).days + 1
        
        # Total approved vacation leave (all time for balance calculation)
        all_approved_vacation_leaves = db.execute(
             "SELECT start_date, end_date FROM leaves WHERE user_id = ? AND status = 'approved' AND leave_type = 'Vacation'",
             (user_id,)
        ).fetchall()
        approved_days_taken_total = sum(calculate_leave_days(l['start_date'], l['end_date']) for l in all_approved_vacation_leaves)
        remaining_balance = entitlement - approved_days_taken_total

        report_data.append({
            'user': user_item,
            'taken_in_period': taken_in_period_days, 
            'entitlement': entitlement,
            'approved_taken_total': approved_days_taken_total, 
            'remaining_balance': remaining_balance
            })
        
        chart_labels.append(user_item['full_name'])
        chart_entitlement_data.append(entitlement)
        chart_taken_data.append(approved_days_taken_total)
        chart_balance_data.append(remaining_balance)

    leave_balance_chart_data = {
        "labels": chart_labels,
        "datasets": [
            {"label": "Annual Entitlement", "data": chart_entitlement_data, "backgroundColor": "rgba(255, 206, 86, 0.6)", "borderColor": "rgba(255, 206, 86, 1)"}, # Yellow
            {"label": "Total Approved Taken", "data": chart_taken_data, "backgroundColor": "rgba(255, 99, 132, 0.6)", "borderColor": "rgba(255, 99, 132, 1)"}, # Red
            {"label": "Remaining Balance", "data": chart_balance_data, "backgroundColor": "rgba(75, 192, 192, 0.6)", "borderColor": "rgba(75, 192, 192, 1)"}  # Green
        ]
    }
    return render_template(
        'reports/leave_report.html',
        report_data=report_data,
        filter_start_date=filter_start_date_str, # Pass filter dates
        filter_end_date=filter_end_date_str,
        leave_balance_chart_data=leave_balance_chart_data # Pass chart data
    )

@bp.route('/employee-demographics', methods=['GET'])
@admin_required 
def employee_demographics_report():
    # ... (logic from reports_py_payroll_summary remains the same) ...
    db = get_db()
    selected_department = request.args.get('department', 'all')
    selected_role = request.args.get('role', 'all')
    query = "SELECT id, full_name, department, role, hire_date FROM users WHERE role != 'admin'"
    params = []
    if selected_department != 'all':
        query += " AND department = ?"
        params.append(selected_department)
    if selected_role != 'all':
        query += " AND role = ?"
        params.append(selected_role)
    employees = db.execute(query, params).fetchall()
    departments = defaultdict(int)
    roles = defaultdict(int)
    tenure_brackets = {
        "0-1 Year": 0, "1-3 Years": 0, "3-5 Years": 0, 
        "5-10 Years": 0, "10+ Years": 0, "Unknown": 0
    }
    today_date = date.today()
    for emp in employees:
        departments[emp['department'] or 'N/A'] += 1
        roles[emp['role']] += 1
        if emp['hire_date']:
            try:
                hire_dt = datetime.strptime(emp['hire_date'], '%Y-%m-%d').date()
                delta = today_date - hire_dt
                years_of_service = delta.days / 365.25
                if years_of_service < 1: tenure_brackets["0-1 Year"] += 1
                elif years_of_service < 3: tenure_brackets["1-3 Years"] += 1
                elif years_of_service < 5: tenure_brackets["3-5 Years"] += 1
                elif years_of_service < 10: tenure_brackets["5-10 Years"] += 1
                else: tenure_brackets["10+ Years"] += 1
            except ValueError: tenure_brackets["Unknown"] += 1
        else: tenure_brackets["Unknown"] += 1
    distinct_departments = [row['department'] for row in db.execute("SELECT DISTINCT department FROM users WHERE department IS NOT NULL ORDER BY department").fetchall()]
    distinct_roles = [row['role'] for row in db.execute("SELECT DISTINCT role FROM users ORDER BY role").fetchall()]
    department_chart_data = {"labels": list(departments.keys()), "data": list(departments.values())}
    role_chart_data = {"labels": list(roles.keys()), "data": list(roles.values())}
    tenure_chart_data = {"labels": ["0-1 Year", "1-3 Years", "3-5 Years", "5-10 Years", "10+ Years", "Unknown"],
                         "data": [tenure_brackets[label] for label in ["0-1 Year", "1-3 Years", "3-5 Years", "5-10 Years", "10+ Years", "Unknown"]]}
    return render_template('reports/employee_demographics_report.html',
                           employees=employees, departments_summary=dict(departments),
                           roles_summary=dict(roles), tenure_summary=tenure_brackets,
                           distinct_departments=distinct_departments, distinct_roles=distinct_roles,
                           selected_department=selected_department, selected_role=selected_role,
                           department_chart_data=department_chart_data, role_chart_data=role_chart_data,
                           tenure_chart_data=tenure_chart_data)

@bp.route('/payroll-summary', methods=['GET'])
@admin_required 
def payroll_summary_report():
    # ... (logic from reports_py_payroll_summary remains the same) ...
    db = get_db()
    current_year = datetime.now().year
    available_years = list(range(current_year - 5, current_year + 2)) 
    selected_year = request.args.get('year', default=current_year, type=int)
    selected_month_str = request.args.get('month', 'all') 
    months_for_filter = []
    for i in range(1, 13): months_for_filter.append({'value': str(i), 'name': calendar.month_name[i]})
    query = """
        SELECT pr.pay_period_year, pr.pay_period_month, COUNT(ps.id) as employees_paid,
            SUM(ps.gross_pay) as total_gross_pay, SUM(ps.total_allowances) as total_allowances,
            SUM(ps.total_deductions) as total_deductions, SUM(ps.net_pay) as total_net_pay, ps.currency 
        FROM payroll_runs pr JOIN payslips ps ON pr.id = ps.payroll_run_id
        WHERE (pr.status = 'Completed' OR pr.status = 'Completed with warnings') """
    params = []
    if selected_year: query += " AND pr.pay_period_year = ? "; params.append(selected_year)
    if selected_month_str != 'all':
        try:
            selected_month_int = int(selected_month_str)
            if 1 <= selected_month_int <= 12: query += " AND pr.pay_period_month = ? "; params.append(selected_month_int)
            else: selected_month_str = 'all'; flash("Invalid month selected, showing for whole year.", "warning")
        except ValueError: selected_month_str = 'all'; flash("Invalid month format, showing for whole year.", "warning")
    query += " GROUP BY pr.pay_period_year, pr.pay_period_month, ps.currency ORDER BY pr.pay_period_year, pr.pay_period_month"
    summary_data = db.execute(query, params).fetchall()
    chart_labels = []; chart_gross_pay = []; chart_net_pay = []; chart_deductions = []
    if selected_month_str == 'all' and selected_year: 
        for month_num in range(1, 13):
            month_name_abbr = calendar.month_abbr[month_num]
            chart_labels.append(f"{month_name_abbr} {selected_year}")
            gross = 0; net = 0; deduct = 0
            for row in summary_data: 
                if row['pay_period_month'] == month_num:
                    gross += row['total_gross_pay'] or 0; net += row['total_net_pay'] or 0; deduct += row['total_deductions'] or 0
            chart_gross_pay.append(gross); chart_net_pay.append(net); chart_deductions.append(deduct)
    else: 
        for row in summary_data:
            month_name_abbr = calendar.month_abbr[row['pay_period_month']]
            chart_labels.append(f"{month_name_abbr} {row['pay_period_year']}")
            chart_gross_pay.append(row['total_gross_pay'] or 0); chart_net_pay.append(row['total_net_pay'] or 0); chart_deductions.append(row['total_deductions'] or 0)
    payroll_chart_data = {
        "labels": chart_labels,
        "datasets": [
            {"label": "Total Gross Pay", "data": chart_gross_pay, "borderColor": "rgba(54, 162, 235, 1)", "backgroundColor": "rgba(54, 162, 235, 0.5)"},
            {"label": "Total Net Pay", "data": chart_net_pay, "borderColor": "rgba(75, 192, 192, 1)", "backgroundColor": "rgba(75, 192, 192, 0.5)"},
            {"label": "Total Deductions", "data": chart_deductions, "borderColor": "rgba(255, 99, 132, 1)", "backgroundColor": "rgba(255, 99, 132, 0.5)"}]}
    display_month_name = ""
    if selected_month_str != 'all':
        try: display_month_name = calendar.month_name[int(selected_month_str)]
        except (ValueError, IndexError): display_month_name = ""
    return render_template('reports/payroll_summary_report.html',
                           summary_data=summary_data, available_years=available_years,
                           months_for_filter=months_for_filter, selected_year=selected_year,
                           selected_month_str=selected_month_str, display_month_name=display_month_name,
                           payroll_chart_data=payroll_chart_data)
