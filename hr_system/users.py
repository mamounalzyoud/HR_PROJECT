# hr_system/users.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, url_for, current_app
)
from werkzeug.exceptions import abort
from werkzeug.security import generate_password_hash
import sqlite3 
import pytz # Import pytz

from hr_system.auth import login_required, admin_required
from hr_system.db import get_db
from hr_system.onboarding import assign_checklist_to_employee 

bp = Blueprint('users', __name__, url_prefix='/users')

def get_potential_managers(db, exclude_user_id=None):
    query = "SELECT id, full_name FROM users WHERE role IN ('manager', 'admin')"
    params = []
    if exclude_user_id:
        query += " AND id != ?"
        params.append(exclude_user_id)
    query += " ORDER BY full_name"
    return db.execute(query, params).fetchall()

@bp.route('/')
@admin_required
def view_users():
    db = get_db()
    # Fetch created_at as a formatted UTC string
    users_list = db.execute(
        '''SELECT u.id, u.username, u.full_name, u.email, u.role, u.department, u.hire_date,
                  u.annual_leave_entitlement, u.timezone,
                  strftime('%Y-%m-%d %H:%M:%S', u.created_at) as created_at_utc_str, 
                  m.full_name as manager_name
           FROM users u LEFT JOIN users m ON u.manager_id = m.id
           ORDER BY u.full_name'''
    ).fetchall()
    return render_template('users/users.html', users=users_list)

@bp.route('/new', methods=('GET', 'POST'))
@admin_required
def new_user():
    db = get_db()
    checklists = db.execute('SELECT id, name, is_default FROM onboarding_checklists ORDER BY is_default DESC, name').fetchall()
    default_checklist_id = next((chk['id'] for chk in checklists if chk['is_default']), None)
    
    available_timezones = pytz.common_timezones # For timezone dropdown

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        full_name = request.form['full_name']
        email = request.form['email']
        role = request.form['role']
        department = request.form.get('department')
        hire_date = request.form.get('hire_date')
        phone_number = request.form.get('phone_number')
        address = request.form.get('address')
        emergency_contact_name = request.form.get('emergency_contact_name')
        emergency_contact_phone = request.form.get('emergency_contact_phone')
        manager_id = int(request.form.get('manager_id')) if request.form.get('manager_id') else None
        user_timezone = request.form.get('timezone') or current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')

        onboarding_checklist_id_str = request.form.get('onboarding_checklist_id')
        onboarding_checklist_id_to_assign = None
        if onboarding_checklist_id_str:
            try:
                onboarding_checklist_id_to_assign = int(onboarding_checklist_id_str)
            except ValueError: 
                onboarding_checklist_id_to_assign = None 
        elif default_checklist_id:
             onboarding_checklist_id_to_assign = default_checklist_id

        try:
            annual_leave_entitlement = float(request.form.get('annual_leave_entitlement', 20.0))
        except (ValueError, TypeError):
            annual_leave_entitlement = 20.0 
            flash("Invalid Annual Leave Entitlement, using default.", "warning")

        error = None
        if not all([username, password, full_name, email, role]): error = "Username, Password, Full Name, Email, and Role are required."
        elif db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone(): error = f"Username '{username}' taken."
        elif db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone(): error = f"Email '{email}' registered."
        elif manager_id and not db.execute("SELECT id FROM users WHERE id = ? AND role IN ('manager', 'admin')", (manager_id,)).fetchone(): error = "Invalid manager selected."
        elif annual_leave_entitlement < 0: error = "Leave entitlement must be non-negative."
        elif user_timezone and user_timezone not in available_timezones: error = "Invalid timezone selected."
        elif onboarding_checklist_id_to_assign and not db.execute("SELECT id FROM onboarding_checklists WHERE id = ?", (onboarding_checklist_id_to_assign,)).fetchone(): 
            error = "Invalid onboarding checklist selected/defaulted."
        
        if error: flash(error, 'error')
        else:
            try:
                # created_at is handled by DEFAULT CURRENT_TIMESTAMP (UTC)
                cursor = db.execute(
                    '''INSERT INTO users (username, password, full_name, email, role, department, 
                                       hire_date, phone_number, address, emergency_contact_name, 
                                       emergency_contact_phone, manager_id, annual_leave_entitlement, timezone)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (username, generate_password_hash(password), full_name, email, role, department, 
                     hire_date, phone_number, address, emergency_contact_name, 
                     emergency_contact_phone, manager_id, annual_leave_entitlement, user_timezone))
                new_user_id = cursor.lastrowid
                db.commit() 
                flash(f'User {full_name} created successfully.', 'success')

                if new_user_id and onboarding_checklist_id_to_assign:
                    assign_ok, assign_msg = assign_checklist_to_employee(get_db(), new_user_id, onboarding_checklist_id_to_assign, g.user['id'])
                    flash(assign_msg, 'success' if assign_ok else 'error')
                elif new_user_id and not onboarding_checklist_id_to_assign:
                    flash("No onboarding checklist was assigned (either '-- None --' selected or no default available).", "info")
                return redirect(url_for('users.view_users'))
            except sqlite3.Error as e: 
                 flash(f"Database error creating user: {e}", "error")
                 db.rollback()
        
        managers = get_potential_managers(db)
        return render_template('users/new_user.html', managers=managers, checklists=checklists, 
                               form_data=request.form, default_checklist_id=default_checklist_id, 
                               available_timezones=available_timezones)

    managers = get_potential_managers(db)
    return render_template('users/new_user.html', managers=managers, checklists=checklists, 
                           default_checklist_id=default_checklist_id, available_timezones=available_timezones)

@bp.route('/<int:user_id>/edit', methods=('GET', 'POST'))
@admin_required
def edit_user(user_id):
    db = get_db()
    # Fetch created_at as UTC string for potential display
    user_to_edit = db.execute(
        '''SELECT id, username, password, full_name, email, role, department, hire_date, 
                  phone_number, address, emergency_contact_name, emergency_contact_phone, 
                  manager_id, annual_leave_entitlement, timezone,
                  strftime('%Y-%m-%d %H:%M:%S', created_at) as created_at_utc_str 
           FROM users WHERE id = ?''', 
        (user_id,)
    ).fetchone()

    if not user_to_edit: 
        flash('User not found.', 'error')
        return redirect(url_for('users.view_users'))

    assigned_checklist_info = db.execute("""
        SELECT oc.id FROM employee_onboarding_status eos
        JOIN onboarding_tasks ot ON eos.task_id = ot.id
        JOIN onboarding_checklists oc ON ot.checklist_id = oc.id
        WHERE eos.employee_user_id = ? GROUP BY oc.id LIMIT 1 
    """, (user_id,)).fetchone()
    current_assigned_checklist_id = assigned_checklist_info['id'] if assigned_checklist_info else None
    
    available_timezones = pytz.common_timezones

    if request.method == 'POST':
        full_name = request.form['full_name']
        email = request.form['email']
        role = request.form['role']
        department = request.form.get('department')
        hire_date = request.form.get('hire_date')
        new_password = request.form.get('password')
        phone_number = request.form.get('phone_number')
        address = request.form.get('address')
        emergency_contact_name = request.form.get('emergency_contact_name')
        emergency_contact_phone = request.form.get('emergency_contact_phone')
        manager_id = int(request.form.get('manager_id')) if request.form.get('manager_id') else None
        user_timezone = request.form.get('timezone') or user_to_edit['timezone'] or current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
        
        onboarding_checklist_id_str = request.form.get('onboarding_checklist_id')
        onboarding_checklist_id_to_assign = int(onboarding_checklist_id_str) if onboarding_checklist_id_str else None
        
        try:
            annual_leave_entitlement_str = request.form.get('annual_leave_entitlement', str(user_to_edit['annual_leave_entitlement']))
            annual_leave_entitlement = float(annual_leave_entitlement_str) if annual_leave_entitlement_str else user_to_edit['annual_leave_entitlement']
        except (ValueError, TypeError):
            annual_leave_entitlement = user_to_edit['annual_leave_entitlement'] 
            flash("Invalid Annual Leave Entitlement value, keeping previous or default.", "warning")

        error = None
        if not full_name or not email or not role: error = "Full Name, Email, and Role are required."
        elif email != user_to_edit['email'] and db.execute('SELECT id FROM users WHERE email = ? AND id != ?', (email, user_id)).fetchone(): error = f"Email '{email}' is already registered by another user."
        elif manager_id == user_id: error = "User cannot be their own manager."
        elif manager_id and not db.execute("SELECT id FROM users WHERE id = ? AND role IN ('manager', 'admin')", (manager_id,)).fetchone(): error = "Invalid manager selected."
        elif annual_leave_entitlement is not None and annual_leave_entitlement < 0: error = "Annual Leave Entitlement must be a non-negative number."
        elif user_timezone and user_timezone not in available_timezones: error = "Invalid timezone selected."
        elif onboarding_checklist_id_to_assign and not db.execute("SELECT id FROM onboarding_checklists WHERE id = ?", (onboarding_checklist_id_to_assign,)).fetchone(): error = "Invalid onboarding checklist selected."

        if error: flash(error, 'error')
        else:
            try:
                update_fields = {
                    'full_name': full_name, 'email': email, 'role': role, 'department': department, 
                    'hire_date': hire_date, 'phone_number': phone_number, 'address': address,
                    'emergency_contact_name': emergency_contact_name, 'emergency_contact_phone': emergency_contact_phone,
                    'manager_id': manager_id, 'annual_leave_entitlement': annual_leave_entitlement,
                    'timezone': user_timezone # Add timezone to update
                }
                if new_password: 
                    if not user_to_edit['password'] or not generate_password_hash(new_password): # Simplified check
                        flash("Error processing new password.", "error") # Should have more robust password check
                    else:
                        update_fields['password'] = generate_password_hash(new_password)
                
                set_clauses = ", ".join([f"{key} = ?" for key in update_fields.keys()])
                params_list = list(update_fields.values()) + [user_id] 
                
                db.execute(f"UPDATE users SET {set_clauses} WHERE id = ?", params_list)
                
                if onboarding_checklist_id_to_assign and onboarding_checklist_id_to_assign != current_assigned_checklist_id:
                    assign_ok, assign_msg = assign_checklist_to_employee(get_db(), user_id, onboarding_checklist_id_to_assign, g.user['id'])
                    flash(assign_msg, 'success' if assign_ok else 'error')
                elif not onboarding_checklist_id_to_assign and current_assigned_checklist_id:
                    db.execute("DELETE FROM employee_onboarding_status WHERE employee_user_id = ?", (user_id,))
                    flash(f"Onboarding checklist unassigned and all associated tasks cleared for {full_name}.", "info")
                
                db.commit() 
                flash(f'User {full_name} updated successfully.', 'success')
                return redirect(url_for('users.view_users'))
            except sqlite3.Error as e: 
                flash(f"Database error updating user: {e}", "error")
                db.rollback()
        
        managers = get_potential_managers(db, exclude_user_id=user_id)
        checklists = db.execute('SELECT id, name, is_default FROM onboarding_checklists ORDER BY is_default DESC, name').fetchall()
        # Ensure form_data_on_error includes all fields, especially created_at_utc_str if needed by template
        form_data_on_error = {**dict(user_to_edit), **request.form} 
        return render_template('users/edit_user.html', user=form_data_on_error, managers=managers, 
                               checklists=checklists, assigned_checklist_id=current_assigned_checklist_id,
                               available_timezones=available_timezones)

    managers = get_potential_managers(db, exclude_user_id=user_id)
    checklists = db.execute('SELECT id, name, is_default FROM onboarding_checklists ORDER BY is_default DESC, name').fetchall()
    return render_template('users/edit_user.html', user=user_to_edit, managers=managers, 
                           checklists=checklists, assigned_checklist_id=current_assigned_checklist_id,
                           available_timezones=available_timezones)

@bp.route('/<int:user_id>/delete', methods=('POST',))
@admin_required
def delete_user(user_id):
    db = get_db()
    user_to_delete = db.execute('SELECT role FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user_to_delete: flash('User not found.', 'error')
    elif user_to_delete['role'] == 'admin' and db.execute('SELECT COUNT(id) FROM users WHERE role = "admin"').fetchone()[0] <= 1:
        flash('Cannot delete the only admin user.', 'error')
    else:
        try:
            db.execute("DELETE FROM employee_onboarding_status WHERE employee_user_id = ?", (user_id,)) 
            db.execute("UPDATE users SET manager_id = NULL WHERE manager_id = ?", (user_id,))
            # Add deletion of other related data if necessary (e.g., benefits, salaries, etc.)
            db.execute('DELETE FROM users WHERE id = ?', (user_id,))
            db.commit()
            flash('User deleted successfully.', 'success')
        except sqlite3.Error as e: 
            flash(f"Database error deleting user: {e}", "error")
            db.rollback()
    return redirect(url_for('users.view_users'))
