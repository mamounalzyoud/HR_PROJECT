# hr_system/attendance.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, current_app
)
from werkzeug.exceptions import abort
from datetime import datetime, timedelta
import pytz # Import pytz

from hr_system.auth import login_required, manager_required
from hr_system.db import get_db

bp = Blueprint('attendance', __name__, url_prefix='/attendance')

@bp.route('/')
@login_required
def view_attendance():
    """Show the logged-in user's attendance records."""
    db = get_db()
    user_id = g.user['id'] 
    # Determine user's local timezone for 'today' calculation
    user_timezone_str = g.user_timezone if hasattr(g, 'user_timezone') and g.user_timezone else current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
    try:
        user_local_tz = pytz.timezone(user_timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        current_app.logger.warning(f"Unknown timezone '{user_timezone_str}' for user {user_id}. Defaulting to UTC for 'today'.")
        user_local_tz = pytz.utc
    
    today = datetime.now(user_local_tz) 

    # Default to current month if no dates are provided
    start_date_str = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', today.strftime('%Y-%m-%d'))

    # Assuming 'date' in attendance is stored as 'YYYY-MM-DD'
    # and 'clock_in' in time_clock is stored as UTC 'YYYY-MM-DD HH:MM:SS'
    attendance_records = db.execute(
        'SELECT * FROM attendance WHERE user_id = ? AND date BETWEEN ? AND ? ORDER BY date DESC',
        (user_id, start_date_str, end_date_str)
    ).fetchall()
    
    # Fetch clock_in/out as strings; template will use localdatetime filter
    time_clock_records_raw = db.execute(
        """SELECT id, user_id, 
                  strftime('%Y-%m-%d %H:%M:%S', clock_in) as clock_in_utc_str, 
                  strftime('%Y-%m-%d %H:%M:%S', clock_out) as clock_out_utc_str, 
                  status 
           FROM time_clock 
           WHERE user_id = ? AND date(clock_in) BETWEEN date(?) AND date(?) 
           ORDER BY clock_in DESC""",
        (user_id, start_date_str, end_date_str)
    ).fetchall()
    
    total_hours = sum(rec['hours_worked'] or 0 for rec in attendance_records)
    present_days = sum(1 for rec in attendance_records if rec['status'] == 'present')
    
    return render_template(
        'attendance/attendance.html', 
        attendance_records=attendance_records, 
        time_clock_records=time_clock_records_raw, # Pass raw UTC strings
        start_date=start_date_str,
        end_date=end_date_str,
        total_hours=total_hours,
        present_days=present_days
    )

@bp.route('/team')
@manager_required 
def team_attendance():
    db = get_db()
    manager_user = g.user 
    user_timezone_str = g.user_timezone if hasattr(g, 'user_timezone') and g.user_timezone else current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
    try:
        user_local_tz = pytz.timezone(user_timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        user_local_tz = pytz.utc # Fallback
    
    today = datetime.now(user_local_tz)
    start_date_str = request.args.get('start_date', today.replace(day=1).strftime('%Y-%m-%d'))
    end_date_str = request.args.get('end_date', today.strftime('%Y-%m-%d'))

    team_members = []
    if manager_user['role'] == 'admin':
        team_members = db.execute('SELECT id, full_name, department FROM users WHERE role != "admin" ORDER BY full_name').fetchall()
    else: 
        team_members = db.execute(
            'SELECT id, full_name, department FROM users WHERE manager_id = ? ORDER BY full_name',
            (manager_user['id'],)
        ).fetchall()

    team_attendance_data = []
    for member in team_members:
        summary = db.execute(
            '''SELECT SUM(hours_worked) as total_hours,
                      SUM(CASE WHEN status = 'present' THEN 1 ELSE 0 END) as present_days,
                      SUM(CASE WHEN status = 'absent' THEN 1 ELSE 0 END) as absent_days
               FROM attendance WHERE user_id = ? AND date BETWEEN ? AND ?''',
            (member['id'], start_date_str, end_date_str)
        ).fetchone()
        team_attendance_data.append({'user': member, 'summary': summary})
        
    return render_template(
        'attendance/team_attendance.html', 
        team_data=team_attendance_data,
        start_date=start_date_str,
        end_date=end_date_str
    )

@bp.route('/clock_in', methods=('POST',))
@login_required
def clock_in():
    db = get_db()
    user_id = g.user['id']
    active_clock = db.execute('SELECT id FROM time_clock WHERE user_id = ? AND status = "active"', (user_id,)).fetchone()

    if active_clock:
        flash('You are already clocked in.', 'warning')
    else:
        now_utc = datetime.now(pytz.utc) 
        # Store without microseconds for consistent parsing later
        db.execute('INSERT INTO time_clock (user_id, clock_in) VALUES (?, ?)', 
                   (user_id, now_utc.strftime('%Y-%m-%d %H:%M:%S'))) 
        db.commit()
        flash('You have successfully clocked in.', 'success')
    return redirect(url_for('main.dashboard')) 

@bp.route('/clock_out', methods=('POST',))
@login_required
def clock_out():
    db = get_db()
    user_id = g.user['id']
    active_clock = db.execute('SELECT id, clock_in FROM time_clock WHERE user_id = ? AND status = "active"', (user_id,)).fetchone()

    if not active_clock:
        flash('You are not currently clocked in.', 'warning')
    else:
        now_utc = datetime.now(pytz.utc) 
        
        try:
            clock_in_utc_str_from_db = active_clock['clock_in']
            # Ensure it's a string before processing
            if not isinstance(clock_in_utc_str_from_db, str):
                clock_in_utc_str_from_db = str(clock_in_utc_str_from_db)
            
            # Truncate microseconds if they exist before parsing
            # This handles old data that might have microseconds and ensures new data (without them) also parses.
            if '.' in clock_in_utc_str_from_db:
                clock_in_utc_str_to_parse = clock_in_utc_str_from_db.split('.')[0]
            else:
                clock_in_utc_str_to_parse = clock_in_utc_str_from_db
            
            # Parse the (potentially truncated) string
            clock_in_naive = datetime.strptime(clock_in_utc_str_to_parse, '%Y-%m-%d %H:%M:%S')
            clock_in_aware_utc = pytz.utc.localize(clock_in_naive) # Make it timezone-aware UTC

        except ValueError as ve:
            current_app.logger.error(f"Error parsing clock_in time '{active_clock['clock_in']}' for user {user_id}: {ve}")
            flash("Error processing clock out due to invalid clock-in time. Please contact support.", "error")
            return redirect(url_for('main.dashboard'))

        # Store clock_out without microseconds
        db.execute('UPDATE time_clock SET clock_out = ?, status = "completed" WHERE id = ?', 
                   (now_utc.strftime('%Y-%m-%d %H:%M:%S'), active_clock['id']))
        
        # Calculate duration using timezone-aware UTC datetimes
        hours_worked_session = (now_utc - clock_in_aware_utc).total_seconds() / 3600
        
        # Determine the date for the attendance record.
        # Using the UTC date of clock-out. This might need refinement if strict local day accounting is needed.
        date_str = now_utc.strftime('%Y-%m-%d') 
        existing_attendance = db.execute('SELECT id, hours_worked FROM attendance WHERE user_id = ? AND date = ?', (user_id, date_str)).fetchone()
        
        if existing_attendance:
            new_total_hours = (existing_attendance['hours_worked'] or 0) + hours_worked_session
            db.execute('UPDATE attendance SET hours_worked = ?, status = "present" WHERE id = ?', (new_total_hours, existing_attendance['id']))
        else:
            db.execute('INSERT INTO attendance (user_id, date, status, hours_worked) VALUES (?, ?, ?, ?)', (user_id, date_str, 'present', hours_worked_session))
        
        db.commit()
        flash('You have successfully clocked out. Your hours have been recorded.', 'success')
    return redirect(url_for('main.dashboard'))
