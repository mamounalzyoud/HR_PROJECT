# hr_system/payroll.py

import sqlite3
from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for,
    Response, current_app # Added current_app for logger, Response for PDF
)
from werkzeug.exceptions import abort
from datetime import datetime
import pytz # Import pytz for explicit UTC times

# WeasyPrint import is deferred to the download_payslip_pdf function
# try:
#     from weasyprint import HTML, CSS
#     WEASYPRINT_AVAILABLE = True
# except ImportError:
#     WEASYPRINT_AVAILABLE = False
#     # This print will go to console, not Flask logger, if module not found at import time
#     print("WARNING: WeasyPrint not found. PDF export will not be available.")
#     print("Install with: pip install WeasyPrint (and ensure system dependencies like Pango/Cairo are installed)")


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
        # In a production app, might log this failure too
        pass

# --- Admin Routes ---

@bp.route('/admin/runs')
@admin_required
def view_payroll_runs():
    """Display past payroll runs and option to start a new one."""
    db = get_db()
    # Fetch run_date as a formatted UTC string
    runs = db.execute(
        '''SELECT pr.id, pr.pay_period_year, pr.pay_period_month, 
                  strftime('%Y-%m-%d %H:%M:%S', pr.run_date) as run_date_utc_str, 
                  pr.processed_by_user_id, pr.status, pr.notes, 
                  u.full_name as processor_name
           FROM payroll_runs pr
           LEFT JOIN users u ON pr.processed_by_user_id = u.id
           ORDER BY pr.pay_period_year DESC, pr.pay_period_month DESC'''
    ).fetchall()

    now = datetime.now() # For default year/month in modal
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

    payroll_run_id = None
    try:
        db.execute('BEGIN') # Start transaction

        # 1. Create Payroll Run Record
        # run_date is handled by DEFAULT CURRENT_TIMESTAMP (UTC in SQLite)
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
               WHERE u.role != 'admin' ''' # Exclude admin from payroll
        ).fetchall()

        processed_count = 0
        skipped_count = 0
        error_messages = []


        # 3. Process each employee
        for emp in employees:
            user_id = emp['id']
            basic_salary = emp['basic_salary']
            pay_frequency = emp['pay_frequency']
            currency = emp['currency']

            # --- Basic Salary Adjustment to Monthly ---
            monthly_basic = 0.0
            if pay_frequency == 'Monthly': monthly_basic = basic_salary
            elif pay_frequency == 'Annually': monthly_basic = basic_salary / 12.0
            elif pay_frequency == 'Weekly': monthly_basic = basic_salary * (52 / 12.0) # Approximation
            elif pay_frequency == 'Bi-Weekly': monthly_basic = basic_salary * (26 / 12.0) # Approximation
            # Add other frequencies if necessary

            if monthly_basic <= 0:
                 current_app.logger.warning(f"Skipping payroll for {emp['full_name']} (ID: {user_id}) - Basic salary {basic_salary} with frequency '{pay_frequency}' resulted in non-positive monthly basic.")
                 error_messages.append(f"Skipped {emp['full_name']}: Non-positive monthly basic salary.")
                 skipped_count += 1
                 continue

            # --- Fetch Monthly Components ---
            components = db.execute(
                '''SELECT component_type, component_name, calculation_type, amount,
                          percentage_rate, calculation_basis, upper_limit
                   FROM salary_components
                   WHERE user_id = ? AND frequency = 'Monthly' ''', # Assuming payroll runs monthly
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
                    basis_amount = 0.0 # Default basis
                    if comp['calculation_basis'] == 'basic_salary':
                        basis_amount = monthly_basic
                    # Add 'gross_pay' basis if needed, though it's complex for pre-calculation here
                    # basis_amount would need to be calculated iteratively if components depend on gross pay.

                    if comp['upper_limit'] is not None and basis_amount > comp['upper_limit']:
                        basis_amount = comp['upper_limit'] # Apply cap

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
            # generated_at is handled by DEFAULT CURRENT_TIMESTAMP (UTC in SQLite)
            payslip_cursor = db.execute(
                '''INSERT INTO payslips (payroll_run_id, user_id, basic_salary_used, pay_frequency_used,
                                        gross_pay, total_allowances, total_deductions, net_pay, currency)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (payroll_run_id, user_id, monthly_basic, 'Monthly', # Storing as 'Monthly' as calculations are monthly
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
            notes += f' Skipped {skipped_count} employees. Details: ' + " | ".join(error_messages)
        
        db.execute('UPDATE payroll_runs SET status = ?, notes = ? WHERE id = ?', (final_status, notes, payroll_run_id))
        db.commit() # Commit transaction
        flash(f'Payroll for {datetime(year, month, 1).strftime("%B %Y")} processed. {notes}', 'success' if skipped_count == 0 else 'warning')

    except sqlite3.Error as e:
        db.rollback() # Rollback on database error
        current_app.logger.error(f"Database error during payroll processing for {year}-{month}: {e}")
        flash(f'Database error during payroll processing: {e}', 'error')
        if payroll_run_id: try_update_run_status(db, payroll_run_id, 'Failed', f"DB Error: {e}")
    except ValueError as e: # Catch specific ValueError for year/month
        db.rollback()
        current_app.logger.error(f"Value error during payroll processing for {year}-{month}: {e}")
        flash(f'Error during payroll processing: {e}', 'error')
        if payroll_run_id: try_update_run_status(db, payroll_run_id, 'Failed', f"Config Error: {e}")
    except Exception as e:
        db.rollback() # Rollback on any other unexpected error
        current_app.logger.error(f"Unexpected error during payroll processing for {year}-{month}: {e}", exc_info=True)
        flash(f'An unexpected error occurred during payroll processing: {e}', 'error')
        if payroll_run_id: try_update_run_status(db, payroll_run_id, 'Failed', f"Unexpected Error: {e}")

    return redirect(url_for('payroll.view_payroll_runs'))


@bp.route('/admin/run/<int:run_id>/details')
@admin_required
def view_run_details(run_id):
    """Display details of a specific payroll run, including generated payslips."""
    db = get_db()

    # Fetch run_date as a formatted UTC string
    run_info = db.execute(
        '''SELECT pr.id, pr.pay_period_year, pr.pay_period_month, 
                  strftime('%Y-%m-%d %H:%M:%S', pr.run_date) as run_date_utc_str, 
                  pr.processed_by_user_id, pr.status, pr.notes,
                  u.full_name as processor_name
           FROM payroll_runs pr
           LEFT JOIN users u ON pr.processed_by_user_id = u.id
           WHERE pr.id = ?''',
        (run_id,)
    ).fetchone()

    if not run_info:
        flash('Payroll run not found.', 'error')
        return redirect(url_for('payroll.view_payroll_runs'))

    # Fetch generated_at as UTC string if you plan to display it in this table
    # Currently, run_detail.html doesn't display individual payslip generated_at,
    # but it's good practice if you add it later.
    payslips_in_run = db.execute(
        '''SELECT p.id as payslip_id, p.net_pay, p.currency,
                  strftime('%Y-%m-%d %H:%M:%S', p.generated_at) as generated_at_utc_str, 
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

    # Fetch generated_at as a formatted UTC string
    payslips = db.execute(
        '''SELECT p.id, p.payroll_run_id, p.user_id, p.basic_salary_used, p.pay_frequency_used,
                  p.gross_pay, p.total_allowances, p.total_deductions, p.net_pay, p.currency,
                  strftime('%Y-%m-%d %H:%M:%S', p.generated_at) as generated_at_utc_str, 
                  pr.pay_period_year, pr.pay_period_month
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

    # Fetch generated_at as a formatted UTC string
    payslip = db.execute(
        '''SELECT p.id, p.payroll_run_id, p.user_id, p.basic_salary_used, p.pay_frequency_used,
                  p.gross_pay, p.total_allowances, p.total_deductions, p.net_pay, p.currency,
                  strftime('%Y-%m-%d %H:%M:%S', p.generated_at) as generated_at_utc_str, 
                  pr.pay_period_year, pr.pay_period_month, u.full_name, u.department
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

    pdf_available_check = False
    try:
        from weasyprint import HTML 
        pdf_available_check = True
    except ImportError:
        pdf_available_check = False
        current_app.logger.warning("WeasyPrint not found. PDF export for payslips will not be available.")


    return render_template('payroll/payslip_detail.html',
                           payslip=payslip,
                           allowances=allowances,
                           deductions=deductions,
                           pdf_available=pdf_available_check)


# --- PDF Export Route ---
@bp.route('/payslip/<int:payslip_id>/pdf')
@login_required
def download_payslip_pdf(payslip_id):
    """Generate and download a PDF version of the payslip."""
    try:
        from weasyprint import HTML, CSS
    except ImportError:
        current_app.logger.error("WeasyPrint not found when trying to generate PDF. PDF export is unavailable.")
        flash('PDF generation library (WeasyPrint) not found or system dependencies missing.', 'error')
        return redirect(url_for('payroll.view_payslip_detail', payslip_id=payslip_id))

    db = get_db()
    user_id = g.user['id']

    # Fetch payslip data. We need generated_at as a datetime object for the PDF template's strftime.
    # The database stores it as a string 'YYYY-MM-DD HH:MM:SS' (UTC).
    payslip_row = db.execute(
        '''SELECT p.*, pr.pay_period_year, pr.pay_period_month, u.full_name, u.department
           FROM payslips p
           JOIN payroll_runs pr ON p.payroll_run_id = pr.id
           JOIN users u ON p.user_id = u.id
           WHERE p.id = ? AND p.user_id = ?''',
        (payslip_id, user_id)
    ).fetchone()

    if not payslip_row:
        flash('Payslip not found or access denied for PDF generation.', 'error')
        return redirect(url_for('payroll.view_my_payslips'))

    # Convert to dictionary and parse generated_at string to datetime object
    payslip_data_for_pdf = dict(payslip_row)
    if payslip_data_for_pdf.get('generated_at'):
        try:
            # Assuming generated_at from DB is like 'YYYY-MM-DD HH:MM:SS' and is UTC
            naive_dt = datetime.strptime(str(payslip_data_for_pdf['generated_at']), '%Y-%m-%d %H:%M:%S')
            # Make it timezone-aware (UTC) then convert to user's local timezone for PDF display
            utc_dt = pytz.utc.localize(naive_dt)
            user_timezone_str = g.user_timezone if hasattr(g, 'user_timezone') and g.user_timezone else current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
            try:
                local_tz = pytz.timezone(user_timezone_str)
                payslip_data_for_pdf['generated_at_localized_dt'] = utc_dt.astimezone(local_tz)
            except pytz.exceptions.UnknownTimeZoneError:
                current_app.logger.warning(f"Unknown timezone '{user_timezone_str}' for PDF, using UTC for generated_at.")
                payslip_data_for_pdf['generated_at_localized_dt'] = utc_dt # Fallback to UTC
        except ValueError:
            current_app.logger.error(f"Could not parse generated_at string '{payslip_data_for_pdf['generated_at']}' for PDF.")
            # If parsing fails, the template might error or display it raw.
            # It's better to handle this gracefully, perhaps by passing the raw string.
            # For now, we'll let it proceed and the template's strftime will fail if it's not a datetime.
            # A more robust solution would be to ensure it's always a datetime object or handle the error.
            pass # Let template handle if 'generated_at_localized_dt' is not set
    else:
        # If generated_at is None or not present, ensure 'generated_at_localized_dt' is also None or a sensible default
        payslip_data_for_pdf['generated_at_localized_dt'] = None


    components = db.execute(
        'SELECT * FROM payslip_components WHERE payslip_id = ? ORDER BY component_type, component_name',
        (payslip_id,)
    ).fetchall()

    allowances = [c for c in components if c['component_type'] == 'allowance']
    deductions = [c for c in components if c['component_type'] == 'deduction']

    try:
        # The payslip_pdf.html template expects `payslip.generated_at` to be a datetime object
        # if it uses `.strftime`. We pass `generated_at_localized_dt` for this.
        html_string = render_template('payroll/payslip_pdf.html',
                                    payslip=payslip_data_for_pdf, # Pass the modified dict
                                    allowances=allowances, 
                                    deductions=deductions)
        
        # Using the CSS from the payslip_pdf.html template itself for consistency
        pdf_css_string = """
            @page { size: A4; margin: 1.5cm; }
            body { font-family: 'Helvetica', 'Arial', sans-serif; font-size: 10pt; line-height: 1.4; }
            .payslip-container { border: 1px solid #ddd; padding: 20px; width: 100%; box-sizing: border-box; }
            .header { border-bottom: 2px solid #eee; padding-bottom: 10px; margin-bottom: 20px; text-align: center; }
            .header h3 { margin: 0 0 5px 0; font-size: 16pt; color: #333; } .header p { margin: 0; font-size: 10pt;color: #555;}
            .employee-info { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px dashed #eee;}
            .employee-info p { margin: 3px 0; font-size: 10pt; } .row { display: flex; width: 100%; margin-bottom: 15px; flex-wrap: nowrap; }
            .col-6 { width: 50%; padding: 0 15px; box-sizing: border-box;} .col-6:first-child { border-right: 1px solid #eee; padding-left: 0;}
            .col-6:last-child { padding-right: 0;} h5 { margin-top: 0; margin-bottom: 8px; font-size: 12pt; border-bottom: 1px solid #ccc; padding-bottom: 4px; color: #444;}
            .text-success { color: #198754; } .text-danger { color: #dc3545; } table { width: 100%; border-collapse: collapse; margin-bottom: 15px; font-size: 9.5pt; }
            td, th { padding: 5px 8px; text-align: left; vertical-align: top;} .text-end { text-align: right; } .fw-bold { font-weight: bold; }
            .table-light { background-color: #f8f9fa; } .table-summary td { border-top: 1px solid #dee2e6; }
            .net-pay-section { margin-top: 20px; padding-top: 10px; border-top: 2px solid #aaa;}
            .net-pay-row td { font-weight: bold;font-size: 13pt; padding: 8px; background-color: #e9ecef; border-top: 2px solid #555;}
            .ps-3 { padding-left: 1rem !important; } .text-muted { color: #6c757d; } .fst-italic { font-style: italic; }
            .footer { margin-top: 30px; text-align: center; font-size: 8pt; color: #888; border-top: 1px solid #eee; padding-top: 10px;}
        """
        
        pdf_bytes = HTML(string=html_string).write_pdf(stylesheets=[CSS(string=pdf_css_string)])
        filename = f"Payslip_{payslip_data_for_pdf['pay_period_month']:02d}_{payslip_data_for_pdf['pay_period_year']}_{payslip_data_for_pdf['full_name'].replace(' ','_')}.pdf"
        return Response(pdf_bytes, mimetype='application/pdf', headers={'Content-Disposition': f'attachment;filename={filename}'})

    except Exception as e:
        current_app.logger.error(f"Error generating PDF for payslip {payslip_id}: {e}", exc_info=True)
        flash(f'Error generating PDF payslip: {e}', 'error')
        return redirect(url_for('payroll.view_payslip_detail', payslip_id=payslip_id))
