# hr_system/benefits.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.exceptions import abort

from hr_system.auth import login_required, admin_required
from hr_system.db import get_db

bp = Blueprint('benefits', __name__, url_prefix='/benefits')

@bp.route('/')
@login_required
def view_benefits():
    """Show the logged-in user's active benefits."""
    db = get_db()
    user_id = session['user_id']
    active_benefits = db.execute(
        'SELECT * FROM benefits WHERE user_id = ? AND status = "active" ORDER BY benefit_type', (user_id,)
    ).fetchall()
    return render_template('benefits/benefits.html', benefits=active_benefits)

@bp.route('/manage')
@admin_required # Only admins can manage benefits system-wide
def manage_benefits():
    """Show all users and their benefits for management (admin only)."""
    db = get_db()
    # Fetch all users to assign/view benefits
    users_list = db.execute('SELECT id, full_name, department FROM users ORDER BY full_name').fetchall()
    user_benefits_data = []
    for user_item in users_list:
        benefits_for_user = db.execute(
            'SELECT * FROM benefits WHERE user_id = ? ORDER BY benefit_type', (user_item['id'],)
        ).fetchall()
        user_benefits_data.append({'user': user_item, 'benefits': benefits_for_user})
        
    return render_template('benefits/manage_benefits.html', user_benefits=user_benefits_data)

@bp.route('/add', methods=('GET', 'POST'))
@admin_required
def add_benefit():
    """Add a new benefit assignment for an employee (admin only)."""
    db = get_db()
    if request.method == 'POST':
        user_id = request.form.get('user_id') # Use get to avoid KeyError if missing
        benefit_type = request.form.get('benefit_type')
        details = request.form.get('details') 
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date') 
        status = request.form.get('status', 'active')

        if not user_id or not benefit_type or not start_date:
            flash('Employee, Benefit Type, and Start Date are required.', 'error')
        else:
            db.execute(
                'INSERT INTO benefits (user_id, benefit_type, details, start_date, end_date, status) VALUES (?, ?, ?, ?, ?, ?)',
                (user_id, benefit_type, details, start_date, end_date if end_date else None, status)
            )
            db.commit()
            flash('Benefit added successfully.', 'success')
            return redirect(url_for('benefits.manage_benefits')) # Redirect to manage benefits page
            
    # For GET request or if POST fails validation
    users_list = db.execute('SELECT id, full_name FROM users WHERE role != "admin" ORDER BY full_name').fetchall() # Exclude admin from dropdown
    return render_template('benefits/add_benefit.html', users=users_list)

@bp.route('/<int:benefit_id>/toggle', methods=('POST',))
@admin_required
def toggle_benefit(benefit_id):
    """Toggle the status (active/inactive) of a benefit assignment."""
    db = get_db()
    benefit = db.execute('SELECT id, status FROM benefits WHERE id = ?', (benefit_id,)).fetchone()
    
    if benefit:
        new_status = 'inactive' if benefit['status'] == 'active' else 'active'
        db.execute('UPDATE benefits SET status = ? WHERE id = ?', (new_status, benefit_id))
        db.commit()
        flash(f'Benefit status updated to {new_status}.', 'success')
    else:
        flash('Benefit not found.', 'error')
        
    return redirect(url_for('benefits.manage_benefits'))

