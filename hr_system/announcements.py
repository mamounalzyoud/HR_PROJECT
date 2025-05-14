# hr_system/announcements.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for
)
from werkzeug.exceptions import abort
from datetime import datetime

from hr_system.auth import login_required, manager_required
from hr_system.db import get_db

bp = Blueprint('announcements', __name__, url_prefix='/announcements')

@bp.route('/')
@login_required
def view_announcements():
    """Show all announcements, ordered by most recent."""
    db = get_db()
    announcements_list = db.execute(
        '''SELECT a.*, u.full_name as creator_name 
           FROM announcements a JOIN users u ON a.created_by = u.id 
           ORDER BY a.created_at DESC'''
    ).fetchall()
    # Pass user role to template to conditionally show delete button
    user_role = g.user['role'] if g.user else None
    user_id = g.user['id'] if g.user else None
    return render_template(
        'announcements/announcements.html', 
        announcements=announcements_list,
        user_role=user_role,
        user_id=user_id
        ) 

@bp.route('/new', methods=('GET', 'POST'))
@manager_required # Only managers/admins can create announcements
def new_announcement():
    """Handle creation of a new announcement."""
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        user_id = session['user_id'] # Creator is the logged-in user
        
        error = None
        if not title:
            error = 'Title is required.'
        elif not content:
            error = 'Content is required.'

        if error:
            flash(error, 'error')
        else:
            db = get_db()
            db.execute(
                'INSERT INTO announcements (title, content, created_by) VALUES (?, ?, ?)',
                (title, content, user_id)
            )
            db.commit()
            flash('Announcement created successfully.', 'success')
            return redirect(url_for('announcements.view_announcements')) # Redirect to announcement list
            
    # For GET request or if POST fails validation
    return render_template('announcements/new_announcement.html')

@bp.route('/<int:announcement_id>/delete', methods=('POST',))
@manager_required # Use manager_required, as admins are also managers implicitly here
def delete_announcement(announcement_id):
    """Delete an announcement (creator manager or admin only)."""
    db = get_db()
    
    # Fetch announcement to check ownership
    announcement = db.execute(
        'SELECT id, created_by FROM announcements WHERE id = ?', (announcement_id,)
    ).fetchone()
        
    if not announcement:
        flash('Announcement not found.', 'error')
        return redirect(url_for('announcements.view_announcements'))

    # Check permissions: Admin or the manager who created it
    if g.user['role'] == 'admin' or announcement['created_by'] == g.user['id']:
        db.execute('DELETE FROM announcements WHERE id = ?', (announcement_id,))
        db.commit()
        flash('Announcement deleted successfully.', 'success')
    else:
        flash('You do not have permission to delete this announcement.', 'error')
        
    return redirect(url_for('announcements.view_announcements'))

