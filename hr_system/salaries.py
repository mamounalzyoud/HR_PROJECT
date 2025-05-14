# hr_system/salaries.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, current_app
)
from werkzeug.exceptions import abort
from datetime import datetime
import sqlite3 
import pytz # Import pytz

from hr_system.auth import login_required, admin_required
from hr_system.db import get_db

bp = Blueprint('salaries', __name__, url_prefix='/admin/salaries')

@bp.route('/')
@admin_required
def manage_salaries():
    """Display a list of employees and their salary information."""
    db = get_db()
    # Fetch updated_at as a formatted UTC string
    users_with_salaries = db.execute('''
        SELECT u.id, u.full_name, u.department, u.role,
               s.basic_salary, s.pay_frequency, s.effective_date, s.currency,
               strftime('%Y-%m-%d %H:%M:%S', s.updated_at) as salary_updated_at_utc_str
        FROM users u
        LEFT JOIN employee_salaries s ON u.id = s.user_id
        WHERE u.role != 'admin'
        ORDER BY u.full_name
    ''').fetchall()
    return render_template('salaries/manage_salaries.html', users_data=users_with_salaries)

@bp.route('/<int:user_id>/manage', methods=('GET', 'POST'))
@admin_required
def edit_employee_salary(user_id):
    """View and handle form submission for editing an employee's base salary
       and display their salary components."""
    db = get_db()
    employee = db.execute(
        'SELECT id, full_name, department FROM users WHERE id = ? AND role != "admin"',
        (user_id,)
    ).fetchone()

    if not employee:
        flash('Employee not found or cannot manage salary for this role.', 'error')
        return redirect(url_for('salaries.manage_salaries'))

    # Fetch updated_at as a formatted UTC string
    current_salary = db.execute(
        '''SELECT id, user_id, basic_salary, pay_frequency, effective_date, currency,
                  strftime('%Y-%m-%d %H:%M:%S', updated_at) as updated_at_utc_str
           FROM employee_salaries WHERE user_id = ?''', 
        (user_id,)
    ).fetchone()

    # Fetch created_at for components as a formatted UTC string
    components = db.execute(
        '''SELECT id, user_id, component_type, component_name, calculation_type, amount,
                  percentage_rate, calculation_basis, upper_limit, frequency, notes,
                  employer_contribution_percent, employer_contribution_fixed, is_statutory,
                  strftime('%Y-%m-%d %H:%M:%S', created_at) as created_at_utc_str
           FROM salary_components 
           WHERE user_id = ? ORDER BY component_type, component_name''',
        (user_id,)
    ).fetchall()

    if request.method == 'POST':
        try:
            basic_salary = request.form.get('basic_salary', type=float)
        except (ValueError, TypeError):
             flash('Invalid value entered for Basic Salary.', 'error')
             return render_template('salaries/edit_salary.html', employee=employee, salary=current_salary, components=components)

        pay_frequency = request.form.get('pay_frequency')
        effective_date = request.form.get('effective_date')
        currency = request.form.get('currency', 'USD')
        
        # Explicitly set updated_at to current UTC time
        updated_at_utc_str = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')

        if basic_salary is None or not pay_frequency or not effective_date:
            flash('Basic Salary, Pay Frequency, and Effective Date are required.', 'error')
        else:
            try:
                if current_salary:
                    db.execute('''
                        UPDATE employee_salaries
                        SET basic_salary = ?, pay_frequency = ?, effective_date = ?, currency = ?, updated_at = ?
                        WHERE user_id = ?
                    ''', (basic_salary, pay_frequency, effective_date, currency, updated_at_utc_str, user_id))
                else:
                    # updated_at will be set by DEFAULT CURRENT_TIMESTAMP on insert if not provided,
                    # or we can provide it explicitly like in UPDATE.
                    # For consistency, let's provide it.
                    db.execute('''
                        INSERT INTO employee_salaries (user_id, basic_salary, pay_frequency, effective_date, currency, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (user_id, basic_salary, pay_frequency, effective_date, currency, updated_at_utc_str))
                db.commit()
                flash(f"Basic salary information for {employee['full_name']} updated successfully.", 'success')
                return redirect(url_for('salaries.edit_employee_salary', user_id=user_id))
            except sqlite3.Error as e:
                db.rollback()
                flash(f"Database error updating salary: {e}", "error")


    return render_template('salaries/edit_salary.html', employee=employee, salary=current_salary, components=components)

@bp.route('/component/<int:component_id>/edit', methods=('GET', 'POST'))
@admin_required
def edit_salary_component(component_id):
    """Edit an existing salary component."""
    db = get_db()
    # Fetch created_at as a formatted UTC string
    component = db.execute(
        '''SELECT sc.id, sc.user_id, sc.component_type, sc.component_name, sc.calculation_type, 
                  sc.amount, sc.percentage_rate, sc.calculation_basis, sc.upper_limit, sc.frequency, 
                  sc.notes, sc.employer_contribution_percent, sc.employer_contribution_fixed, sc.is_statutory,
                  strftime('%Y-%m-%d %H:%M:%S', sc.created_at) as created_at_utc_str,
                  u.full_name as employee_name
           FROM salary_components sc
           JOIN users u ON sc.user_id = u.id
           WHERE sc.id = ?''',
        (component_id,)
    ).fetchone()

    if not component:
        flash('Salary component not found.', 'error')
        return redirect(url_for('salaries.manage_salaries'))

    employee = {'id': component['user_id'], 'full_name': component['employee_name']}

    if request.method == 'POST':
        component_type = request.form.get('component_type')
        component_name = request.form.get('component_name')
        calculation_type = request.form.get('calculation_type')
        frequency = request.form.get('frequency')
        notes = request.form.get('notes')
        is_statutory = 1 if request.form.get('is_statutory') == 'on' else 0

        amount = None
        percentage_rate = None
        calculation_basis = None
        upper_limit = None
        employer_contribution_percent = None
        employer_contribution_fixed = None
        error = None

        if not component_type or not component_name or not calculation_type or not frequency:
            error = "Component Type, Name, Calculation Type, and Frequency are required."
        # ... (rest of your validation logic for component fields) ...
        elif component_type not in ['allowance', 'deduction']:
            error = 'Invalid Component Type.'
        elif calculation_type not in ['fixed', 'percentage']:
            error = 'Invalid Calculation Type.'

        if not error:
            try:
                if calculation_type == 'fixed':
                    amount_str = request.form.get('amount_fixed')
                    amount = float(amount_str) if amount_str else None
                    if amount is None: error = 'Amount is required for fixed calculation type.'
                    
                    emp_contrib_fixed_str = request.form.get('employer_contribution_fixed')
                    employer_contribution_fixed = float(emp_contrib_fixed_str) if emp_contrib_fixed_str else None

                elif calculation_type == 'percentage':
                    percentage_rate_str = request.form.get('percentage_rate')
                    percentage_rate = float(percentage_rate_str) if percentage_rate_str else None
                    calculation_basis = request.form.get('calculation_basis')
                    
                    upper_limit_str = request.form.get('upper_limit')
                    upper_limit = float(upper_limit_str) if upper_limit_str else None
                    
                    emp_contrib_percent_str = request.form.get('employer_contribution_percent')
                    employer_contribution_percent = float(emp_contrib_percent_str) if emp_contrib_percent_str else None


                    if percentage_rate is None or not calculation_basis:
                        error = 'Percentage Rate and Calculation Basis are required for percentage type.'
                    elif calculation_basis not in ['basic_salary', 'gross_pay']:
                         error = 'Invalid Calculation Basis selected.'
            except (ValueError, TypeError):
                 error = 'Invalid numeric value entered for amount/percentage/limit.'


        if error:
            flash(error, 'error')
            # Re-render form with existing data (component already has created_at_utc_str)
            return render_template('salaries/edit_salary_component.html', component=component, employee=employee)
        else:
            try:
                # created_at is not updated here, only on creation
                db.execute('''
                    UPDATE salary_components SET
                        component_type = ?, component_name = ?, calculation_type = ?, amount = ?,
                        percentage_rate = ?, calculation_basis = ?, upper_limit = ?, frequency = ?, notes = ?,
                        employer_contribution_percent = ?, employer_contribution_fixed = ?, is_statutory = ?
                    WHERE id = ?
                ''', (component_type, component_name, calculation_type, amount,
                      percentage_rate, calculation_basis, upper_limit, frequency, notes,
                      employer_contribution_percent, employer_contribution_fixed, is_statutory,
                      component_id))
                db.commit()
                flash(f"Salary component '{component_name}' updated successfully.", 'success')
                return redirect(url_for('salaries.edit_employee_salary', user_id=component['user_id']))
            except sqlite3.Error as e:
                 flash(f"Database error updating component: {e}", "error")
                 db.rollback()
                 return render_template('salaries/edit_salary_component.html', component=component, employee=employee)

    return render_template('salaries/edit_salary_component.html', component=component, employee=employee)


@bp.route('/<int:user_id>/component/add', methods=('GET', 'POST'))
@admin_required
def add_salary_component(user_id):
    db = get_db()
    employee = db.execute('SELECT id, full_name FROM users WHERE id = ?', (user_id,)).fetchone()
    if not employee:
         flash('Employee not found.', 'error')
         return redirect(url_for('salaries.manage_salaries'))

    if request.method == 'POST':
        component_type = request.form.get('component_type')
        component_name = request.form.get('component_name')
        calculation_type = request.form.get('calculation_type', 'fixed')
        frequency = request.form.get('frequency', 'Monthly')
        notes = request.form.get('notes')
        is_statutory = 1 if request.form.get('is_statutory') == 'on' else 0

        amount = None
        percentage_rate = None
        calculation_basis = None
        upper_limit = None
        employer_contribution_percent = None
        employer_contribution_fixed = None
        error = None

        if not component_type or not component_name:
             error = 'Component Type and Name are required.'
        # ... (rest of your validation logic) ...
        elif component_type not in ['allowance', 'deduction']:
             error = 'Invalid Component Type.'
        elif calculation_type not in ['fixed', 'percentage']:
             error = 'Invalid Calculation Type.'


        if not error:
            try:
                if calculation_type == 'fixed':
                    amount_str = request.form.get('amount_fixed')
                    amount = float(amount_str) if amount_str else None # Allow empty if not required based on logic
                    if amount is None and calculation_type == 'fixed': # Example: make it required for fixed
                        error = 'Amount is required for fixed calculation type.'
                    
                    emp_contrib_fixed_str = request.form.get('employer_contribution_fixed')
                    employer_contribution_fixed = float(emp_contrib_fixed_str) if emp_contrib_fixed_str else None

                elif calculation_type == 'percentage':
                    percentage_rate_str = request.form.get('percentage_rate')
                    percentage_rate = float(percentage_rate_str) if percentage_rate_str else None
                    calculation_basis = request.form.get('calculation_basis')
                    
                    upper_limit_str = request.form.get('upper_limit')
                    upper_limit = float(upper_limit_str) if upper_limit_str else None
                    
                    emp_contrib_percent_str = request.form.get('employer_contribution_percent')
                    employer_contribution_percent = float(emp_contrib_percent_str) if emp_contrib_percent_str else None


                    if percentage_rate is None or not calculation_basis: # Example: make these required
                        error = 'Percentage Rate and Calculation Basis are required for percentage type.'
                    elif calculation_basis not in ['basic_salary', 'gross_pay']:
                         error = 'Invalid Calculation Basis selected.'
            except (ValueError, TypeError):
                 error = 'Invalid numeric value entered for amount/percentage/limit.'


        if error:
            flash(error, 'error')
            return render_template('salaries/add_salary_component.html', employee=employee, form_data=request.form)
        else:
            try:
                # created_at is handled by DEFAULT CURRENT_TIMESTAMP (UTC)
                db.execute('''
                    INSERT INTO salary_components
                    (user_id, component_type, component_name, calculation_type, amount,
                     percentage_rate, calculation_basis, upper_limit, frequency, notes,
                     employer_contribution_percent, employer_contribution_fixed, is_statutory)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, component_type, component_name, calculation_type, amount,
                      percentage_rate, calculation_basis, upper_limit, frequency, notes,
                      employer_contribution_percent, employer_contribution_fixed, is_statutory))
                db.commit()
                flash(f"{calculation_type.title()} {component_type.title()} '{component_name}' added for {employee['full_name']}.", 'success')
                return redirect(url_for('salaries.edit_employee_salary', user_id=user_id))
            except sqlite3.Error as e:
                 flash(f"Database error: {e}", "error")
                 db.rollback()
                 return render_template('salaries/add_salary_component.html', employee=employee, form_data=request.form)

    return render_template('salaries/add_salary_component.html', employee=employee)


@bp.route('/component/<int:component_id>/delete', methods=('POST',))
@admin_required
def delete_salary_component(component_id):
    """Delete a specific salary component."""
    db = get_db()
    component = db.execute('SELECT id, user_id FROM salary_components WHERE id = ?', (component_id,)).fetchone()
    if component:
        user_id = component['user_id']
        try:
            db.execute('DELETE FROM salary_components WHERE id = ?', (component_id,))
            db.commit()
            flash('Salary component deleted successfully.', 'success')
        except sqlite3.Error as e:
             flash(f"Database error deleting component: {e}", "error")
             db.rollback()
        return redirect(url_for('salaries.edit_employee_salary', user_id=user_id))
    else:
        flash('Salary component not found.', 'error')
        return redirect(url_for('salaries.manage_salaries'))
