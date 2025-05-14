# hr_system/performance.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.exceptions import abort
from datetime import datetime
import sqlite3

from hr_system.auth import login_required, manager_required
from hr_system.db import get_db

bp = Blueprint('performance', __name__, url_prefix='/performance')

# --- Employee Route ---

@bp.route('/my-reviews')
@login_required
def my_reviews():
    """Display a list of the logged-in employee's performance reviews."""
    db = get_db()
    employee_id = g.user['id']
    
    reviews = db.execute(
        '''SELECT pr.*, m.full_name as manager_name 
           FROM performance_reviews pr
           LEFT JOIN users m ON pr.manager_user_id = m.id -- Use LEFT JOIN in case manager was deleted
           WHERE pr.employee_user_id = ? 
           ORDER BY pr.review_period_end DESC''',
        (employee_id,)
    ).fetchall()
    
    return render_template('performance/my_reviews.html', reviews=reviews)

# --- Manager Routes ---

@bp.route('/manage')
@manager_required
def manage_performance():
    """Display manager's direct reports and reviews they have submitted."""
    db = get_db()
    manager_id = g.user['id']
    
    # Find direct reports
    direct_reports = db.execute(
        'SELECT id, full_name, department FROM users WHERE manager_id = ? ORDER BY full_name',
        (manager_id,)
    ).fetchall()
    
    # --- ADDED: Fetch reviews created BY this manager ---
    submitted_reviews = db.execute(
        '''SELECT pr.*, e.full_name as employee_name 
           FROM performance_reviews pr 
           JOIN users e ON pr.employee_user_id = e.id 
           WHERE pr.manager_user_id = ? 
           ORDER BY pr.review_date DESC''',
        (manager_id,)
    ).fetchall()
    # --- END ADDED ---
    
    # Pass both lists to the template
    return render_template(
        'performance/manage_reviews.html', 
        reports=direct_reports, 
        submitted_reviews=submitted_reviews # Pass the submitted reviews
        )

@bp.route('/review/new/<int:employee_id>', methods=('GET', 'POST'))
@manager_required
def create_review(employee_id):
    """Create a new performance review for a direct report."""
    db = get_db()
    manager_id = g.user['id']
    
    # Verify the employee reports to this manager
    employee = db.execute(
        'SELECT id, full_name FROM users WHERE id = ? AND manager_id = ?',
        (employee_id, manager_id)
    ).fetchone()
    
    if not employee:
        flash('Employee not found or does not report to you.', 'error')
        return redirect(url_for('performance.manage_performance'))
        
    if request.method == 'POST':
        start_date = request.form.get('review_period_start')
        end_date = request.form.get('review_period_end')
        rating = request.form.get('overall_rating') # Get as string first
        manager_comments = request.form.get('manager_comments')
        
        # Convert rating to int, handle empty string
        rating_int = None
        if rating:
            try:
                rating_int = int(rating)
            except ValueError:
                flash('Invalid rating value provided.', 'error')
                return render_template('performance/create_review.html', employee=employee)
        
        error = None
        if not start_date or not end_date:
            error = 'Review Period Start and End Dates are required.'
        elif start_date > end_date:
             error = 'Review period start date cannot be after the end date.'
        elif rating_int is not None and not (1 <= rating_int <= 5): 
             error = 'Rating must be between 1 and 5.'
             
        if error:
            flash(error, 'error')
        else:
            try:
                db.execute(
                    '''INSERT INTO performance_reviews 
                       (employee_user_id, manager_user_id, review_period_start, review_period_end, 
                        overall_rating, manager_comments, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (employee_id, manager_id, start_date, end_date, rating_int, manager_comments, 'Completed')
                )
                db.commit()
                flash(f"Performance review for {employee['full_name']} created successfully.", 'success')
                return redirect(url_for('performance.manage_performance'))
            except sqlite3.Error as e:
                 flash(f"Database error creating review: {e}", "error")
                 db.rollback()

    # For GET request
    return render_template('performance/create_review.html', employee=employee)


@bp.route('/review/<int:review_id>')
@login_required
def view_review(review_id):
    """View details of a specific performance review."""
    db = get_db()
    user_id = g.user['id']
    user_role = g.user['role']
    
    review = db.execute(
        '''SELECT pr.*, e.full_name as employee_name, m.full_name as manager_name 
           FROM performance_reviews pr 
           JOIN users e ON pr.employee_user_id = e.id 
           LEFT JOIN users m ON pr.manager_user_id = m.id -- LEFT JOIN in case manager deleted
           WHERE pr.id = ?''',
        (review_id,)
    ).fetchone()

    if not review:
        flash('Performance review not found.', 'error')
        if user_role in ['admin', 'manager']:
            return redirect(url_for('performance.manage_performance'))
        else:
            return redirect(url_for('performance.my_reviews'))

    # Authorization check
    is_authorized = False
    if user_role == 'admin':
        is_authorized = True
    elif review['employee_user_id'] == user_id: 
        is_authorized = True
    # Check if the logged-in user is the manager who conducted the review
    elif user_role == 'manager' and review['manager_user_id'] == user_id: 
         is_authorized = True
    # Check if the logged-in user is the current manager of the employee (even if they didn't write the review)
    elif user_role == 'manager':
         current_manager = db.execute("SELECT manager_id FROM users WHERE id = ?", (review['employee_user_id'],)).fetchone()
         if current_manager and current_manager['manager_id'] == user_id:
             is_authorized = True


    if not is_authorized:
        flash('You are not authorized to view this review.', 'error')
        if user_role in ['admin', 'manager']:
            return redirect(url_for('performance.manage_performance'))
        else:
            return redirect(url_for('performance.my_reviews'))

    return render_template('performance/view_review.html', review=review)

# TODO: Add routes for editing reviews 
