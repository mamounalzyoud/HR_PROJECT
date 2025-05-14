# hr_system/payroll.py

import sqlite3
from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for,
    Response # Added for PDF response
)
from werkzeug.exceptions import abort
from datetime import datetime

# --- PDF library import is now DEFERRED ---
# try:
#     from weasyprint import HTML, CSS
#     WEASYPRINT_AVAILABLE = True
# except ImportError:
#     WEASYPRINT_AVAILABLE = False
#     print("WARNING: WeasyPrint not found. PDF export will not be available.")
#     print("Install with: pip install WeasyPrint (and ensure system dependencies like Pango/Cairo are installed)")
# --- End Deferral ---


from hr_system.auth import login_required, admin_required
from hr_system.db import get_db

bp = Blueprint('payroll', __name__, url_prefix='/payroll')

# --- Helper Function ---
def try_update_run_status(db, run_id, status, notes):
    """Attempt to update run status during error handling, ignoring further errors."""
    try:
        db.execute('UPDATE payroll_runs SET status = ?, notes = ? WHERE id = ?', (status, notes, run_id))
        db.commit()
    except Exception:
        pass

# --- Admin Routes ---

@bp.route('/admin/runs')
@admin_required
def view_payroll_runs():
    """Display past payroll runs and option to start a new one."""
    db = get_db()
    runs = db.execute(
        '''SELECT pr.*, u.full_name as processor_name
           FROM payroll_runs pr
           LEFT JOIN users u ON pr.processed_by_user_id = u.id
           ORDER BY pr.pay_period_year DESC, pr.pay_period_month DESC'''
    ).fetchall()

    now = datetime.now()
    current_year = now.year
    current_month = now.month

    return render_template('payroll/runs.html',
                           runs=runs,
                           current_year=current_year,
                           current_month=current_month)

@bp.route('/admin/run/new', methods=('POST',))
@admin_required
def run_new_payroll():
    """Process payroll for a given month and year, handling different component types."""
    db = get_db()
    admin_user_id = g.user['id']

    try:
        year = request.form.get('year', type=int)
        month = request.form.get('month', type=int)
        if not year or not month or not (1 <= month <= 12):
            raise ValueError("Invalid year or month.")
    except (ValueError, TypeError):
        flash('Invalid year or month selected.', 'error')
        return redirect(url_for('payroll.view_payroll_runs'))

    existing_run = db.execute(
        'SELECT id FROM payroll_runs WHERE pay_period_year = ? AND pay_period_month = ?',
        (year, month)
    ).fetchone()
    if existing_run:
        flash(f'Payroll for {datetime(year, month, 1).strftime("%B %Y")} has already been processed.', 'warning')
        return redirect(url_for('payroll.view_payroll_runs'))

    # --- Start Payroll Processing ---
    payroll_run_id = None
    try:
        db.execute('BEGIN')

        # 1. Create Payroll Run Record
        run_cursor = db.execute(
            'INSERT INTO payroll_runs (pay_period_year, pay_period_month, processed_by_user_id, status) VALUES (?, ?, ?, ?)',
            (year, month, admin_user_id, 'Processing')
        )
        payroll_run_id = run_cursor.lastrowid

        # 2. Fetch eligible employees
        employees = db.execute(
            '''SELECT u.id, u.full_name, s.basic_salary, s.pay_frequency, s.currency
               FROM users u
               JOIN employee_salaries s ON u.id = s.user_id
               WHERE u.role != 'admin' '''
        ).fetchall()

        processed_count = 0
        skipped_count = 0

        # 3. Process each employee
        for emp in employees:
            user_id = emp['id']
            basic_salary = emp['basic_salary']
            pay_frequency = emp['pay_frequency']
            currency = emp['currency']

            # --- Basic Salary Adjustment ---
            monthly_basic = 0.0
            if pay_frequency == 'Monthly': monthly_basic = basic_salary
            elif pay_frequency == 'Annually': monthly_basic = basic_salary / 12.0
            elif pay_frequency == 'Weekly': monthly_basic = basic_salary * (52 / 12.0)
            elif pay_frequency == 'Bi-Weekly': monthly_basic = basic_salary * (26 / 12.0)

            if monthly_basic <= 0:
                 flash(f"Skipping {emp['full_name']} - Could not determine monthly basic salary from frequency '{pay_frequency}'.", "warning")
                 skipped_count += 1
                 continue

            # --- Fetch Monthly Components ---
            components = db.execute(
                '''SELECT component_type, component_name, calculation_type, amount,
                          percentage_rate, calculation_basis, upper_limit
                   FROM salary_components
                   WHERE user_id = ? AND frequency = 'Monthly' ''',
                (user_id,)
            ).fetchall()

            calculated_allowances = []
            calculated_deductions = []
            total_allowances = 0.0
            total_deductions = 0.0

            # --- Calculate each component ---
            for comp in components:
                calculated_amount = 0.0
                if comp['calculation_type'] == 'fixed':
                    calculated_amount = comp['amount'] or 0.0
                elif comp['calculation_type'] == 'percentage':
                    rate = comp['percentage_rate'] or 0.0
                    basis_amount = 0.0
                    if comp['calculation_basis'] == 'basic_salary':
                        basis_amount = monthly_basic

                    if comp['upper_limit'] is not None and basis_amount > comp['upper_limit']:
                        basis_amount = comp['upper_limit']

                    calculated_amount = (basis_amount * rate) / 100.0

                component_detail = {
                    'name': comp['component_name'],
                    'amount': calculated_amount,
                    'type': comp['component_type']
                }

                if comp['component_type'] == 'allowance':
                    total_allowances += calculated_amount
                    calculated_allowances.append(component_detail)
                elif comp['component_type'] == 'deduction':
                    total_deductions += calculated_amount
                    calculated_deductions.append(component_detail)

            # --- Calculate Final Payslip Figures ---
            gross_pay = monthly_basic + total_allowances
            net_pay = gross_pay - total_deductions

            # --- 4. Insert Payslip Record ---
            payslip_cursor = db.execute(
                '''INSERT INTO payslips (payroll_run_id, user_id, basic_salary_used, pay_frequency_used,
                                        gross_pay, total_allowances, total_deductions, net_pay, currency)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (payroll_run_id, user_id, monthly_basic, 'Monthly',
                 gross_pay, total_allowances, total_deductions, net_pay, currency)
            )
            payslip_id = payslip_cursor.lastrowid

            # --- 5. Insert Calculated Payslip Component Records ---
            for comp_detail in calculated_allowances + calculated_deductions:
                db.execute(
                    '''INSERT INTO payslip_components (payslip_id, component_type, component_name, amount)
                       VALUES (?, ?, ?, ?)''',
                    (payslip_id, comp_detail['type'], comp_detail['name'], comp_detail['amount'])
                )

            processed_count += 1

        # 6. Update Payroll Run Status
        final_status = 'Completed'
        notes = f'Processed {processed_count} employees.'
        if skipped_count > 0:
            final_status = 'Completed with warnings'
            notes += f' Skipped {skipped_count} employees.'

        db.execute('UPDATE payroll_runs SET status = ?, notes = ? WHERE id = ?', (final_status, notes, payroll_run_id))

        db.commit()
        flash(f'Payroll for {datetime(year, month, 1).strftime("%B %Y")} processed. {notes}', 'success' if skipped_count == 0 else 'warning')

    except sqlite3.Error as e:
        db.rollback()
        flash(f'Database error during payroll processing: {e}', 'error')
        if payroll_run_id: try_update_run_status(db, payroll_run_id, 'Failed', str(e))
    except Exception as e:
        db.rollback()
        flash(f'An unexpected error occurred during payroll processing: {e}', 'error')
        if payroll_run_id: try_update_run_status(db, payroll_run_id, 'Failed', str(e))

    return redirect(url_for('payroll.view_payroll_runs'))


@bp.route('/admin/run/<int:run_id>/details')
@admin_required
def view_run_details(run_id):
    """Display details of a specific payroll run, including generated payslips."""
    db = get_db()

    run_info = db.execute(
        '''SELECT pr.*, u.full_name as processor_name
           FROM payroll_runs pr
           LEFT JOIN users u ON pr.processed_by_user_id = u.id
           WHERE pr.id = ?''',
        (run_id,)
    ).fetchone()

    if not run_info:
        flash('Payroll run not found.', 'error')
        return redirect(url_for('payroll.view_payroll_runs'))

    payslips_in_run = db.execute(
        '''SELECT p.id as payslip_id, p.net_pay, p.currency,
                  u.id as user_id, u.full_name, u.department
           FROM payslips p
           JOIN users u ON p.user_id = u.id
           WHERE p.payroll_run_id = ?
           ORDER BY u.full_name''',
        (run_id,)
    ).fetchall()

    return render_template('payroll/run_detail.html',
                           run_info=run_info,
                           payslips=payslips_in_run)


# --- Employee Routes ---

@bp.route('/payslips')
@login_required
def view_my_payslips():
    """Display a list of the logged-in user's payslips."""
    db = get_db()
    user_id = g.user['id']

    payslips = db.execute(
        '''SELECT p.*, pr.pay_period_year, pr.pay_period_month
           FROM payslips p
           JOIN payroll_runs pr ON p.payroll_run_id = pr.id
           WHERE p.user_id = ?
           ORDER BY pr.pay_period_year DESC, pr.pay_period_month DESC''',
        (user_id,)
    ).fetchall()

    return render_template('payroll/payslip_list.html', payslips=payslips)

@bp.route('/payslip/<int:payslip_id>')
@login_required
def view_payslip_detail(payslip_id):
    """Display the details of a specific payslip."""
    db = get_db()
    user_id = g.user['id']

    payslip = db.execute(
        '''SELECT p.*, pr.pay_period_year, pr.pay_period_month, u.full_name, u.department
           FROM payslips p
           JOIN payroll_runs pr ON p.payroll_run_id = pr.id
           JOIN users u ON p.user_id = u.id
           WHERE p.id = ? AND p.user_id = ?''',
        (payslip_id, user_id)
    ).fetchone()

    if not payslip:
        flash('Payslip not found or access denied.', 'error')
        return redirect(url_for('payroll.view_my_payslips'))

    components = db.execute(
        'SELECT * FROM payslip_components WHERE payslip_id = ? ORDER BY component_type, component_name',
        (payslip_id,)
    ).fetchall()

    allowances = [c for c in components if c['component_type'] == 'allowance']
    deductions = [c for c in components if c['component_type'] == 'deduction']

    # Check if WeasyPrint *can* be imported now, just before rendering
    pdf_available_check = False
    try:
        from weasyprint import HTML # Try importing here
        pdf_available_check = True
    except ImportError:
        pdf_available_check = False

    return render_template('payroll/payslip_detail.html',
                           payslip=payslip,
                           allowances=allowances,
                           deductions=deductions,
                           pdf_available=pdf_available_check) # Pass availability flag


# --- PDF Export Route ---
@bp.route('/payslip/<int:payslip_id>/pdf')
@login_required
def download_payslip_pdf(payslip_id):
    """Generate and download a PDF version of the payslip."""
    # --- Import WeasyPrint HERE ---
    try:
        from weasyprint import HTML, CSS
    except ImportError:
        flash('PDF generation library (WeasyPrint) not found or system dependencies missing.', 'error')
        return redirect(url_for('payroll.view_payslip_detail', payslip_id=payslip_id))
    # --- End Import ---


    db = get_db()
    user_id = g.user['id']

    # Fetch payslip data (same as view_payslip_detail)
    payslip = db.execute(
        '''SELECT p.*, pr.pay_period_year, pr.pay_period_month, u.full_name, u.department
           FROM payslips p
           JOIN payroll_runs pr ON p.payroll_run_id = pr.id
           JOIN users u ON p.user_id = u.id
           WHERE p.id = ? AND p.user_id = ?''',
        (payslip_id, user_id)
    ).fetchone()

    if not payslip:
        flash('Payslip not found or access denied.', 'error')
        return redirect(url_for('payroll.view_my_payslips'))

    components = db.execute(
        'SELECT * FROM payslip_components WHERE payslip_id = ? ORDER BY component_type, component_name',
        (payslip_id,)
    ).fetchall()

    allowances = [c for c in components if c['component_type'] == 'allowance']
    deductions = [c for c in components if c['component_type'] == 'deduction']

    # Render the HTML template to a string
    try:
        html_string = render_template('payroll/payslip_pdf.html', # Use the new template
                                    payslip=payslip, 
                                    allowances=allowances, 
                                    deductions=deductions)

        # --- Use WeasyPrint to generate PDF ---
        # Example basic CSS for PDF layout
        pdf_css = """
            @page { size: A4; margin: 1.5cm; }
            body { font-family: sans-serif; font-size: 10pt; }
            .card { border: 1px solid #ccc; margin-bottom: 1rem; }
            .card-header { background-color: #f8f9fa; padding: 0.5rem 1rem; font-weight: bold; }
            .card-body { padding: 1rem; }
            h1, h3, h5 { margin-bottom: 0.5rem; }
            table { width: 100%; border-collapse: collapse; margin-bottom: 1rem; }
            td, th { padding: 0.25rem 0.5rem; text-align: left; }
            .text-end { text-align: right; }
            .fw-bold { font-weight: bold; }
            .table-light { background-color: #f8f9fa; }
            .table-success { background-color: #d1e7dd; }
            .table-primary { background-color: #cfe2ff; }
            .border-top { border-top: 1px solid #dee2e6; }
            .border-2 { border-width: 2px !important; }
            .border-success { border-color: #198754 !important; }
            .border-primary { border-color: #0d6efd !important; }
            .ps-3 { padding-left: 1rem !important; }
            .pe-4 { padding-right: 1.5rem !important; }
            .ps-4 { padding-left: 1.5rem !important; }
            .text-success { color: #198754 !important; }
            .text-danger { color: #dc3545 !important; }
            .fs-5 { font-size: 1.25rem !important; }
            .fs-6 { font-size: 1rem !important; }
            .mb-3 { margin-bottom: 1rem !important; }
            /* Add more styles as needed */
        """
        css = CSS(string=pdf_css)
        pdf_bytes = HTML(string=html_string).write_pdf(stylesheets=[css])

        # --- Create filename ---
        filename = f"Payslip_{payslip['pay_period_month']:02d}_{payslip['pay_period_year']}_{payslip['full_name'].replace(' ','_')}.pdf"

        # --- Return PDF as a response ---
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={'Content-Disposition': f'attachment;filename={filename}'}
        )

    except Exception as e:
        print(f"Error generating PDF: {e}") # Log the error
        flash(f'Error generating PDF payslip: {e}', 'error')
        return redirect(url_for('payroll.view_payslip_detail', payslip_id=payslip_id))

