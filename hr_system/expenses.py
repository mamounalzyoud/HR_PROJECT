# hr_system/expenses.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.exceptions import abort
from datetime import datetime
import sqlite3

from hr_system.auth import login_required, manager_required
from hr_system.db import get_db

bp = Blueprint('expenses', __name__, url_prefix='/expenses')

# --- Employee Routes ---

@bp.route('/')
@login_required
def view_my_expenses():
    """Show the logged-in user's expense claims."""
    db = get_db()
    user_id = g.user['id']
    
    claims = db.execute(
        '''SELECT e.*, a.full_name as approver_name 
           FROM expenses e 
           LEFT JOIN users a ON e.approved_by_user_id = a.id
           WHERE e.user_id = ? 
           ORDER BY e.submitted_at DESC''',
        (user_id,)
    ).fetchall()
    
    return render_template('expenses/my_expenses.html', claims=claims)

@bp.route('/new', methods=('GET', 'POST'))
@login_required
def submit_expense():
    """Allow employees to submit a new expense claim."""
    if request.method == 'POST':
        expense_date = request.form.get('expense_date')
        category = request.form.get('category')
        try:
            amount = request.form.get('amount', type=float)
        except (ValueError, TypeError):
            amount = None # Will trigger error below
        currency = request.form.get('currency', 'USD')
        description = request.form.get('description')
        user_id = g.user['id']
        
        error = None
        if not expense_date: error = 'Expense date is required.'
        elif not category: error = 'Category is required.'
        elif amount is None or amount <= 0: error = 'A valid positive amount is required.'
        elif not description: error = 'Description is required.'
        
        if error:
            flash(error, 'error')
        else:
            db = get_db()
            try:
                db.execute(
                    '''INSERT INTO expenses 
                       (user_id, expense_date, category, amount, currency, description, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (user_id, expense_date, category, amount, currency, description, 'Pending')
                )
                db.commit()
                flash('Expense claim submitted successfully.', 'success')
                return redirect(url_for('expenses.view_my_expenses'))
            except sqlite3.Error as e:
                flash(f"Database error submitting expense: {e}", "error")
                db.rollback()

    # Common expense categories - can be moved to config or DB later
    categories = ['Travel', 'Meals', 'Supplies', 'Training', 'Client Entertainment', 'Other']
    return render_template('expenses/new_expense.html', categories=categories)

# --- Manager/Admin Routes ---

@bp.route('/manage')
@manager_required
def manage_expenses():
    """Allow managers/admins to view and manage expense claims."""
    db = get_db()
    manager_user = g.user
    
    claims_to_manage = []
    # Admins see all pending claims
    if manager_user['role'] == 'admin':
        claims_to_manage = db.execute(
            '''SELECT e.*, u.full_name as employee_name, u.department 
               FROM expenses e JOIN users u ON e.user_id = u.id 
               WHERE e.status = 'Pending' 
               ORDER BY e.submitted_at ASC'''
        ).fetchall()
    # Managers see pending claims from their direct reports
    elif manager_user['role'] == 'manager':
        claims_to_manage = db.execute(
            '''SELECT e.*, u.full_name as employee_name, u.department 
               FROM expenses e JOIN users u ON e.user_id = u.id 
               WHERE u.manager_id = ? AND e.status = 'Pending' 
               ORDER BY e.submitted_at ASC''',
            (manager_user['id'],)
        ).fetchall()
        
    return render_template('expenses/manage_expenses.html', claims=claims_to_manage)

@bp.route('/<int:claim_id>/action', methods=('POST',))
@manager_required
def expense_action(claim_id):
    """Approve or reject an expense claim."""
    action = request.form.get('action') # 'approve' or 'reject'
    db = get_db()
    manager_user = g.user
    
    # Fetch claim details including submitter's manager for authorization
    claim = db.execute(
        'SELECT e.*, u.manager_id FROM expenses e JOIN users u ON e.user_id = u.id WHERE e.id = ?',
        (claim_id,)
    ).fetchone()

    if not claim:
        flash('Expense claim not found.', 'error')
        return redirect(url_for('expenses.manage_expenses'))
        
    # Authorization: Admin or direct manager can action
    is_authorized = False
    if manager_user['role'] == 'admin':
        is_authorized = True
    elif manager_user['role'] == 'manager' and claim['manager_id'] == manager_user['id']:
        is_authorized = True
        
    if not is_authorized:
        flash('You are not authorized to manage this expense claim.', 'error')
        return redirect(url_for('expenses.manage_expenses'))
        
    # Prevent actioning non-pending claims
    if claim['status'] != 'Pending':
         flash(f'This claim has already been {claim["status"].lower()}.', 'warning')
         return redirect(url_for('expenses.manage_expenses'))

    if action in ['approve', 'reject']:
        new_status = 'Approved' if action == 'approve' else 'Rejected'
        approved_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S') if new_status == 'Approved' else None
        
        try:
            db.execute(
                'UPDATE expenses SET status = ?, approved_by_user_id = ?, approved_at = ? WHERE id = ?',
                (new_status, manager_user['id'], approved_at, claim_id)
            )
            db.commit()
            flash(f'Expense claim has been {new_status.lower()}.', 'success')
        except sqlite3.Error as e:
            db.rollback()
            flash(f'Database error processing claim: {e}', 'error')
    else:
        flash('Invalid action specified.', 'error')
        
    return redirect(url_for('expenses.manage_expenses'))

