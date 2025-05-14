# hr_system/notifications_ui.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, jsonify, current_app
)
from werkzeug.exceptions import abort
import sqlite3
from datetime import datetime

from hr_system.auth import login_required
from hr_system.db import get_db

bp = Blueprint('notifications_ui', __name__, url_prefix='/notifications')

@bp.route('/')
@login_required
def list_notifications():
    """Display a list of all notifications for the logged-in user."""
    db = get_db()
    user_id = g.user['id']

    # --- Debugging Timezone ---
    current_app.logger.info("--- Debug Timezone in list_notifications route ---")
    if hasattr(g, 'user') and g.user is not None:
        # sqlite3.Row can be checked for key existence using 'in' with row.keys()
        user_timezone_from_db = g.user['timezone'] if 'timezone' in g.user.keys() else 'timezone key not found'
        current_app.logger.info(f"g.user['timezone'] (from DB via auth): {user_timezone_from_db}")
    else:
        current_app.logger.info("g.user not set or is None.")
    
    current_app.logger.info(f"session.get('user_timezone'): {session.get('user_timezone')}")
    
    if hasattr(g, 'user_timezone'):
        current_app.logger.info(f"g.user_timezone (resolved in auth or default): {g.user_timezone}")
    else:
        current_app.logger.info("g.user_timezone attribute not found on g.")
        current_app.logger.info(f"Fallback check: current_app.config['USER_DEFAULT_TIMEZONE']: {current_app.config.get('USER_DEFAULT_TIMEZONE')}")
    current_app.logger.info("--- End Debug Timezone ---")
    # --- End Debugging ---

    notifications = db.execute(
        """SELECT id, message, link_url, created_at, is_read, related_entity_type, related_entity_id
           FROM app_notifications
           WHERE user_id = ?
           ORDER BY created_at DESC""",
        (user_id,)
    ).fetchall()

    return render_template('notifications/list.html', notifications=notifications)

@bp.route('/api/unread_count')
@login_required
def api_unread_count():
    """API endpoint to get the count of unread notifications."""
    db = get_db()
    user_id = g.user['id']
    count_row = db.execute(
        "SELECT COUNT(id) as unread_count FROM app_notifications WHERE user_id = ? AND is_read = 0",
        (user_id,)
    ).fetchone()
    
    unread_count = count_row['unread_count'] if count_row else 0
    return jsonify({'unread_count': unread_count})

@bp.route('/api/recent_unread')
@login_required
def api_recent_unread_notifications():
    """API endpoint to get a few recent unread notifications for a dropdown."""
    db = get_db()
    user_id = g.user['id']
    limit = request.args.get('limit', 5, type=int)

    recent_unread = db.execute(
        """SELECT id, message, link_url, created_at, related_entity_type, related_entity_id
           FROM app_notifications
           WHERE user_id = ? AND is_read = 0
           ORDER BY created_at DESC
           LIMIT ?""",
        (user_id, limit)
    ).fetchall()
    
    notifications_list = [dict(notification) for notification in recent_unread]
    
    return jsonify({'notifications': notifications_list})


@bp.route('/mark_as_read', methods=['POST'])
@login_required
def mark_as_read():
    """API endpoint to mark specific or all notifications as read."""
    db = get_db()
    user_id = g.user['id']
    data = request.get_json()
    
    if data is None:
        data = {}

    notification_ids = data.get('ids')
    single_id_to_mark = data.get('id')

    try:
        if single_id_to_mark is not None:
             db.execute("UPDATE app_notifications SET is_read = 1 WHERE user_id = ? AND id = ?", (user_id, int(single_id_to_mark)))
             current_app.logger.info(f"Marked single notification ID {single_id_to_mark} as read for user {user_id}")
        elif notification_ids and isinstance(notification_ids, list) and len(notification_ids) > 0:
            safe_notification_ids = [int(id_val) for id_val in notification_ids]
            placeholders = ','.join('?' for _ in safe_notification_ids)
            query = f"UPDATE app_notifications SET is_read = 1 WHERE user_id = ? AND id IN ({placeholders})"
            params = [user_id] + safe_notification_ids
            db.execute(query, params)
            current_app.logger.info(f"Marked specific notifications as read for user {user_id}: {safe_notification_ids}")
        elif notification_ids is None or (isinstance(notification_ids, list) and len(notification_ids) == 0) :
            db.execute("UPDATE app_notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (user_id,))
            current_app.logger.info(f"Marked all unread notifications as read for user {user_id}")
        
        db.commit()
        return jsonify({'success': True, 'message': 'Notifications marked as read.'})
    except ValueError:
        db.rollback() 
        current_app.logger.error(f"Invalid notification ID format provided for user {user_id}.")
        return jsonify({'success': False, 'message': 'Invalid notification ID format.'}), 400
    except sqlite3.Error as e:
        db.rollback()
        current_app.logger.error(f"Database error in mark_as_read for user {user_id}: {e}")
        return jsonify({'success': False, 'message': 'Error marking notifications as read.'}), 500
    except Exception as e: 
        db.rollback()
        current_app.logger.error(f"Unexpected error in mark_as_read for user {user_id}: {e}")
        return jsonify({'success': False, 'message': 'An unexpected error occurred.'}), 500
