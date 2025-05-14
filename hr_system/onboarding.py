# hr_system/onboarding.py

from flask import (
    Blueprint, flash, g, redirect, render_template, request, url_for, current_app, send_from_directory, jsonify
)
from werkzeug.exceptions import abort
from werkzeug.utils import secure_filename
import sqlite3
from datetime import datetime, timedelta, date 
import pytz # Import pytz
import os
import shutil
import json

from hr_system.auth import login_required, admin_required, manager_required
from hr_system.db import get_db
from hr_system.notifications import (
    send_task_assignment_notification,
    send_prerequisite_met_notification,
    send_task_completed_notification,
    send_task_due_soon_reminder_notification,
    send_task_overdue_alert_notification,
    send_new_comment_notification
)

bp = Blueprint('onboarding', __name__, url_prefix='/onboarding')

ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'md'}
REMINDER_DEBOUNCE_HOURS = 23 
DUE_SOON_THRESHOLD_DAYS = 2

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# --- HELPER FUNCTION for Proactive Reminders ---
def trigger_pending_task_reminders(db, for_employee_id):
    """
    Checks all pending tasks for a given employee and sends due soon/overdue reminders
    if necessary, respecting the debounce period.
    """
    current_app.logger.info(f"Triggering pending task reminders check for employee_id: {for_employee_id}")
    today_date_obj = date.today()
    due_soon_date_obj = today_date_obj + timedelta(days=DUE_SOON_THRESHOLD_DAYS)
    now_datetime_utc = datetime.now(pytz.utc) # Use explicit UTC time

    # Fetch last_reminder_sent_at as a formatted UTC string
    pending_tasks_for_employee = db.execute(
        """
        SELECT eos.id as status_id, eos.status, 
               strftime('%Y-%m-%d %H:%M:%S', eos.last_reminder_sent_at) as last_reminder_sent_at_utc_str,
               ot.task_name, ot.due_days_after_start,
               oc.name as checklist_name,
               u_emp.id as employee_user_id, u_emp.full_name as employee_full_name, 
               u_emp.hire_date as employee_hire_date, u_emp.manager_id as employee_manager_id,
               ot.responsible_role 
        FROM employee_onboarding_status eos
        JOIN onboarding_tasks ot ON eos.task_id = ot.id
        JOIN onboarding_checklists oc ON ot.checklist_id = oc.id
        JOIN users u_emp ON eos.employee_user_id = u_emp.id
        WHERE eos.employee_user_id = ? AND eos.status = 'Pending'
        """, (for_employee_id,)
    ).fetchall()

    if not pending_tasks_for_employee:
        current_app.logger.info(f"No pending tasks found for employee_id: {for_employee_id} during reminder check.")
        return

    for task_instance_raw in pending_tasks_for_employee:
        task_instance = dict(task_instance_raw)
        task_due_date_dt = None
        task_due_date_str = "N/A"

        if task_instance.get('employee_hire_date') and task_instance.get('due_days_after_start') is not None:
            try:
                hire_date_obj = datetime.strptime(task_instance['employee_hire_date'], '%Y-%m-%d').date()
                task_due_date_dt = hire_date_obj + timedelta(days=task_instance['due_days_after_start'])
                task_due_date_str = task_due_date_dt.strftime('%Y-%m-%d')
            except ValueError:
                current_app.logger.warning(f"Invalid hire date for user {task_instance['employee_user_id']} on task {task_instance['task_name']}: {task_instance['employee_hire_date']}")
                continue 

        if not task_due_date_dt: 
            continue

        send_reminder_this_time = False
        last_reminder_utc_str = task_instance.get('last_reminder_sent_at_utc_str')
        last_reminder_dt_utc = None

        if last_reminder_utc_str:
            try:
                # Parse the UTC string from DB
                naive_dt = datetime.strptime(last_reminder_utc_str, '%Y-%m-%d %H:%M:%S')
                last_reminder_dt_utc = pytz.utc.localize(naive_dt) # Make it timezone-aware (UTC)
            except ValueError:
                current_app.logger.warning(f"Could not parse last_reminder_sent_at string: {last_reminder_utc_str} for status_id {task_instance['status_id']}")
        
        if last_reminder_dt_utc: # Compare aware datetime objects
            if (now_datetime_utc - last_reminder_dt_utc).total_seconds() / 3600 > REMINDER_DEBOUNCE_HOURS:
                send_reminder_this_time = True
        else: 
            send_reminder_this_time = True

        if send_reminder_this_time:
            responsible_user_id_for_this_task_instance = None
            # ... (logic to determine responsible_user_id_for_this_task_instance remains the same)
            if task_instance['responsible_role'] == 'Employee':
                responsible_user_id_for_this_task_instance = task_instance['employee_user_id']
            elif task_instance['responsible_role'] == 'Manager':
                responsible_user_id_for_this_task_instance = task_instance['employee_manager_id']
            elif task_instance['responsible_role'] == 'HR':
                hr_user = db.execute("SELECT id FROM users WHERE role = 'hr' LIMIT 1").fetchone()
                if hr_user: responsible_user_id_for_this_task_instance = hr_user['id']
            elif task_instance['responsible_role'] == 'IT':
                it_user = db.execute("SELECT id FROM users WHERE role = 'it' LIMIT 1").fetchone()
                if it_user: responsible_user_id_for_this_task_instance = it_user['id']

            if not responsible_user_id_for_this_task_instance:
                current_app.logger.warning(f"Could not determine responsible user for task instance {task_instance['status_id']} (Task: {task_instance['task_name']})")
                continue
            
            # Store last_reminder_sent_at as explicit UTC string
            now_utc_str_for_db = now_datetime_utc.strftime('%Y-%m-%d %H:%M:%S')

            if task_due_date_dt < today_date_obj:
                days_overdue = (today_date_obj - task_due_date_dt).days
                send_task_overdue_alert_notification(
                    task_instance['employee_user_id'], task_instance['task_name'], 
                    responsible_user_id_for_this_task_instance, 
                    task_instance['checklist_name'], task_due_date_str, days_overdue, 
                    related_status_id=task_instance['status_id']
                )
                db.execute("UPDATE employee_onboarding_status SET last_reminder_sent_at = ? WHERE id = ?", 
                           (now_utc_str_for_db, task_instance['status_id']))
                current_app.logger.info(f"Sent OVERDUE alert for task_status_id {task_instance['status_id']}")
            elif task_due_date_dt <= due_soon_date_obj:
                send_task_due_soon_reminder_notification(
                    task_instance['employee_user_id'], task_instance['task_name'], 
                    responsible_user_id_for_this_task_instance, 
                    task_instance['checklist_name'], task_due_date_str, 
                    related_status_id=task_instance['status_id']
                )
                db.execute("UPDATE employee_onboarding_status SET last_reminder_sent_at = ? WHERE id = ?", 
                           (now_utc_str_for_db, task_instance['status_id']))
                current_app.logger.info(f"Sent DUE SOON reminder for task_status_id {task_instance['status_id']}")
            db.commit()

# --- Helper Function to Assign Checklist ---
# (assign_checklist_to_employee logic remains largely the same, 
#  as it primarily deals with task definitions and initial assignments, not display of existing timestamps)
def assign_checklist_to_employee(db, employee_user_id, checklist_id, assigned_by_user_id=None):
    if not employee_user_id or not checklist_id:
        return False, "Missing employee ID or checklist ID for assignment."
    try:
        employee = db.execute("SELECT id, full_name, hire_date, email, manager_id FROM users WHERE id = ?", (employee_user_id,)).fetchone()
        checklist = db.execute("SELECT id, name FROM onboarding_checklists WHERE id = ?", (checklist_id,)).fetchone()
        if not employee: return False, f"Employee with ID {employee_user_id} not found."
        if not checklist: return False, f"Onboarding checklist with ID {checklist_id} not found."

        tasks_to_assign_raw = db.execute(
            "SELECT id, task_name, responsible_role, due_days_after_start, depends_on_task_id FROM onboarding_tasks WHERE checklist_id = ?",
            (checklist_id,)
        ).fetchall()

        task_ids_in_checklist = [task['id'] for task in tasks_to_assign_raw]
        if task_ids_in_checklist:
            placeholders = ','.join('?' for _ in task_ids_in_checklist)
            db.execute(
                f"DELETE FROM employee_onboarding_status WHERE employee_user_id = ? AND task_id IN ({placeholders})",
                [employee_user_id] + task_ids_in_checklist)
            current_app.logger.info(f"Cleared previous task statuses for employee {employee_user_id} for tasks in checklist {checklist_id}.")


        if not tasks_to_assign_raw:
            db.commit() # Commit even if no tasks, to clear previous ones if any
            current_app.logger.info(f"Checklist '{checklist['name']}' (ID: {checklist_id}) assigned to employee ID {employee_user_id}, but the checklist currently has no tasks defined.")
            return True, f"Checklist '{checklist['name']}' assigned to employee {employee['full_name']}. The checklist currently has no tasks."

        for task_def in tasks_to_assign_raw:
            # completed_date and last_reminder_sent_at will be NULL on new insertion
            status_cursor = db.execute(
                "INSERT INTO employee_onboarding_status (employee_user_id, task_id, status) VALUES (?, ?, ?)",
                (employee_user_id, task_def['id'], 'Pending'))
            new_status_id = status_cursor.lastrowid

            responsible_user_id_for_notification = None
            # ... (logic to determine responsible_user_id_for_notification remains the same) ...
            if task_def['responsible_role'] == 'Employee':
                responsible_user_id_for_notification = employee_user_id
            elif task_def['responsible_role'] == 'Manager':
                if employee['manager_id']:
                    responsible_user_id_for_notification = employee['manager_id']
            elif task_def['responsible_role'] == 'HR':
                hr_user = db.execute("SELECT id FROM users WHERE role = 'hr' LIMIT 1").fetchone()
                if hr_user: responsible_user_id_for_notification = hr_user['id']
            elif task_def['responsible_role'] == 'IT':
                it_user = db.execute("SELECT id FROM users WHERE role = 'it' LIMIT 1").fetchone()
                if it_user: responsible_user_id_for_notification = it_user['id']


            is_actionable_on_assign = not task_def['depends_on_task_id']

            if is_actionable_on_assign and responsible_user_id_for_notification:
                 due_date_str = None
                 if employee['hire_date'] and task_def['due_days_after_start'] is not None:
                     try:
                         hire_dt = datetime.strptime(employee['hire_date'], '%Y-%m-%d').date()
                         due_dt = hire_dt + timedelta(days=task_def['due_days_after_start'])
                         due_date_str = due_dt.strftime('%Y-%m-%d')
                     except ValueError: pass

                 send_task_assignment_notification(
                     employee_user_id,
                     task_def['task_name'],
                     responsible_user_id_for_notification,
                     checklist['name'],
                     due_date_str,
                     related_status_id=new_status_id
                 )
        db.commit()
        return True, f"Onboarding checklist '{checklist['name']}' and its tasks assigned successfully to employee {employee['full_name']}."
    except sqlite3.Error as e:
        db.rollback()
        current_app.logger.error(f"DB error assigning checklist: {e}")
        return False, f"Database error assigning checklist: {e}"
    except Exception as e:
        db.rollback()
        current_app.logger.error(f"Unexpected error assigning checklist: {e}")
        return False, f"An unexpected error occurred: {e}"

# --- Checklist Management Routes (Admin) ---
# (manage_checklists, create_checklist, edit_checklist, clone_checklist, delete_checklist
#  do not directly fetch or display timestamps requiring localization in their current form)
# ... (These routes remain unchanged regarding timestamp fetching for display) ...
@bp.route('/checklists')
@admin_required
def manage_checklists():
    db = get_db()
    checklists = db.execute('SELECT id, name, description, is_default FROM onboarding_checklists ORDER BY name').fetchall()
    return render_template('onboarding/manage_checklists.html', checklists=checklists)

@bp.route('/checklists/new', methods=('GET', 'POST'))
@admin_required
def create_checklist():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        is_default = 1 if request.form.get('is_default') == 'on' else 0
        if not name: flash('Checklist name is required.', 'error')
        else:
            db = get_db()
            try:
                if is_default == 1: db.execute('UPDATE onboarding_checklists SET is_default = 0 WHERE is_default = 1')
                db.execute('INSERT INTO onboarding_checklists (name, description, is_default) VALUES (?, ?, ?)',(name, description, is_default))
                db.commit()
                flash(f"Checklist '{name}' created.", 'success')
                return redirect(url_for('onboarding.manage_checklists'))
            except sqlite3.IntegrityError: flash(f"Checklist name '{name}' already exists.", 'error')
            except sqlite3.Error as e: flash(f"DB error: {e}", "error"); db.rollback()
    return render_template('onboarding/edit_checklist.html', checklist=None, title="Create New Onboarding Checklist", form_action_url=url_for('onboarding.create_checklist'))


@bp.route('/checklists/<int:checklist_id>/edit', methods=('GET', 'POST'))
@admin_required
def edit_checklist(checklist_id):
    db = get_db()
    checklist = db.execute('SELECT * FROM onboarding_checklists WHERE id = ?', (checklist_id,)).fetchone()
    if not checklist: flash('Checklist not found.', 'error'); return redirect(url_for('onboarding.manage_checklists'))
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        is_default = 1 if request.form.get('is_default') == 'on' else 0
        if not name: flash('Checklist name is required.', 'error')
        else:
            try:
                if is_default == 1 and checklist['is_default'] == 0:
                    db.execute('UPDATE onboarding_checklists SET is_default = 0 WHERE is_default = 1 AND id != ?', (checklist_id,))
                db.execute('UPDATE onboarding_checklists SET name = ?, description = ?, is_default = ? WHERE id = ?',
                           (name, description, is_default, checklist_id))
                db.commit()
                flash(f"Checklist '{name}' updated.", 'success')
                return redirect(url_for('onboarding.manage_checklists'))
            except sqlite3.IntegrityError: flash(f"Checklist name '{name}' already exists.", 'error')
            except sqlite3.Error as e: flash(f"DB error: {e}", "error"); db.rollback()
    return render_template('onboarding/edit_checklist.html', checklist=checklist, title=f"Edit: {checklist['name']}", form_action_url=url_for('onboarding.edit_checklist', checklist_id=checklist_id))

@bp.route('/checklists/<int:checklist_id>/clone', methods=['POST'])
@admin_required
def clone_checklist(checklist_id):
    db = get_db()
    original_checklist = db.execute('SELECT * FROM onboarding_checklists WHERE id = ?', (checklist_id,)).fetchone()

    if not original_checklist:
        flash('Original checklist not found.', 'error')
        return redirect(url_for('onboarding.manage_checklists'))

    new_checklist_name = f"Copy of {original_checklist['name']}"
    counter = 1
    temp_name = new_checklist_name
    while db.execute("SELECT id FROM onboarding_checklists WHERE name = ?", (temp_name,)).fetchone():
        counter += 1
        temp_name = f"{new_checklist_name} ({counter})"
    new_checklist_name = temp_name

    try:
        db.execute('BEGIN') 
        cursor = db.execute(
            'INSERT INTO onboarding_checklists (name, description, is_default) VALUES (?, ?, ?)',
            (new_checklist_name, original_checklist['description'], 0)
        )
        new_checklist_id = cursor.lastrowid

        original_tasks = db.execute(
            'SELECT * FROM onboarding_tasks WHERE checklist_id = ? ORDER BY id', 
            (checklist_id,)
        ).fetchall()

        old_task_id_to_new_task_id_map = {}

        for original_task in original_tasks:
            task_cursor = db.execute(
                '''INSERT INTO onboarding_tasks (checklist_id, task_name, description, responsible_role,
                                           due_days_after_start, display_order, depends_on_task_id)
                   VALUES (?, ?, ?, ?, ?, ?, NULL)''', 
                (new_checklist_id, original_task['task_name'], original_task['description'],
                 original_task['responsible_role'], original_task['due_days_after_start'],
                 original_task['display_order'])
            )
            new_task_id = task_cursor.lastrowid
            old_task_id_to_new_task_id_map[original_task['id']] = new_task_id

            original_attachments = db.execute(
                "SELECT * FROM onboarding_task_attachments WHERE onboarding_task_id = ?",
                (original_task['id'],)
            ).fetchall()

            for original_att in original_attachments:
                # uploaded_at for attachments is DEFAULT CURRENT_TIMESTAMP
                if original_att['attachment_type'] == 'template_link':
                    db.execute(
                        """INSERT INTO onboarding_task_attachments
                           (onboarding_task_id, file_name, stored_file_name, attachment_type, url)
                           VALUES (?, ?, ?, ?, ?)""",
                        (new_task_id, original_att['file_name'], original_att['stored_file_name'],
                         'template_link', original_att['url'])
                    )
                elif original_att['attachment_type'] == 'template_file' and original_att['stored_file_name']:
                    original_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], original_att['stored_file_name'])
                    if os.path.exists(original_file_path):
                        original_file_ext = os.path.splitext(original_att['file_name'])[1]
                        new_stored_filename = f"{datetime.now(pytz.utc).strftime('%Y%m%d%H%M%S%f')}_{secure_filename(os.path.splitext(original_att['file_name'])[0])}{original_file_ext}"

                        new_file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], new_stored_filename)
                        try:
                            shutil.copy2(original_file_path, new_file_path)
                            db.execute(
                                """INSERT INTO onboarding_task_attachments
                                   (onboarding_task_id, file_name, stored_file_name, attachment_type)
                                   VALUES (?, ?, ?, ?)""",
                                (new_task_id, original_att['file_name'], new_stored_filename, 'template_file')
                            )
                            current_app.logger.info(f"Copied attachment {original_att['stored_file_name']} to {new_stored_filename} for cloned task {new_task_id}")
                        except Exception as e_copy:
                            current_app.logger.error(f"Could not copy attachment file {original_file_path} for cloned task: {e_copy}")
                    else:
                        current_app.logger.warning(f"Original attachment file {original_att['stored_file_name']} not found for task ID {original_task['id']} during clone.")

        for original_task in original_tasks:
            if original_task['depends_on_task_id']:
                original_prerequisite_id = original_task['depends_on_task_id']
                new_prerequisite_id = old_task_id_to_new_task_id_map.get(original_prerequisite_id)
                current_cloned_task_id = old_task_id_to_new_task_id_map.get(original_task['id'])

                if new_prerequisite_id and current_cloned_task_id:
                    db.execute(
                        'UPDATE onboarding_tasks SET depends_on_task_id = ? WHERE id = ?',
                        (new_prerequisite_id, current_cloned_task_id)
                    )
        
        db.commit()
        flash(f"Checklist '{original_checklist['name']}' cloned successfully as '{new_checklist_name}'.", 'success')

    except sqlite3.Error as e:
        db.rollback()
        flash(f"Database error cloning checklist: {e}", "error")
        current_app.logger.error(f"DB error cloning checklist: {e}")
    except Exception as e:
        db.rollback()
        flash(f"An unexpected error occurred while cloning: {e}", "error")
        current_app.logger.error(f"Unexpected error cloning checklist: {e}")

    return redirect(url_for('onboarding.manage_checklists'))


@bp.route('/checklists/<int:checklist_id>/delete', methods=('POST',))
@admin_required
def delete_checklist(checklist_id):
    db = get_db()
    checklist = db.execute('SELECT name FROM onboarding_checklists WHERE id = ?', (checklist_id,)).fetchone()
    if not checklist: flash('Checklist not found.', 'error'); return redirect(url_for('onboarding.manage_checklists'))

    tasks_exist = db.execute('SELECT 1 FROM onboarding_tasks WHERE checklist_id = ? LIMIT 1', (checklist_id,)).fetchone()
    if tasks_exist:
        flash(f"Cannot delete '{checklist['name']}', it has tasks. Delete tasks first or ensure no tasks are assigned.", 'error')
        return redirect(url_for('onboarding.manage_checklists'))

    assigned_to_employee = db.execute(
        'SELECT 1 FROM employee_onboarding_status eos JOIN onboarding_tasks ot ON eos.task_id = ot.id WHERE ot.checklist_id = ? LIMIT 1',
        (checklist_id,)).fetchone()
    if assigned_to_employee:
        flash(f"Cannot delete '{checklist['name']}', its tasks are currently assigned to employees. Please resolve assignments first.", 'error')
        return redirect(url_for('onboarding.manage_checklists'))
    try:
        db.execute('DELETE FROM onboarding_checklists WHERE id = ?', (checklist_id,)); db.commit()
        flash(f"Checklist '{checklist['name']}' deleted.", 'success')
    except sqlite3.Error as e: flash(f"DB error: {e}", "error"); db.rollback()
    return redirect(url_for('onboarding.manage_checklists'))

# --- Task Management Routes (Admin) ---
# (manage_tasks, create_task, alter_onboarding_task, delete_task
#  do not directly fetch or display timestamps requiring localization in their current form,
#  they manage task definitions. Timestamps for task instances are handled in other routes.)
# ... (These routes remain unchanged regarding timestamp fetching for display) ...
@bp.route('/checklists/<int:checklist_id>/tasks')
@admin_required
def manage_tasks(checklist_id):
    db = get_db()
    checklist = db.execute('SELECT id, name FROM onboarding_checklists WHERE id = ?', (checklist_id,)).fetchone()
    if not checklist: flash('Checklist not found.', 'error'); return redirect(url_for('onboarding.manage_checklists'))
    tasks = db.execute(
        '''SELECT ot.*, pt.task_name as prerequisite_task_name
           FROM onboarding_tasks ot
           LEFT JOIN onboarding_tasks pt ON ot.depends_on_task_id = pt.id
           WHERE ot.checklist_id = ?
           ORDER BY ot.display_order, ot.task_name''', (checklist_id,)
    ).fetchall()
    return render_template('onboarding/manage_tasks.html', checklist=checklist, tasks=tasks)

@bp.route('/tasks/<int:task_id>/move_up', methods=['POST'])
@admin_required
def move_task_up(task_id):
    db = get_db()
    current_task = db.execute("SELECT id, checklist_id, display_order FROM onboarding_tasks WHERE id = ?", (task_id,)).fetchone()
    if not current_task:
        flash("Task not found.", "error")
        return redirect(request.referrer or url_for('onboarding.manage_checklists'))

    other_task = db.execute(
        """SELECT id, display_order FROM onboarding_tasks
           WHERE checklist_id = ? AND (display_order < ? OR (display_order = ? AND id < ?))
           ORDER BY display_order DESC, id DESC LIMIT 1""",
        (current_task['checklist_id'], current_task['display_order'], current_task['display_order'], current_task['id'])
    ).fetchone()

    if other_task:
        try:
            db.execute('BEGIN')
            current_display_order = current_task['display_order']
            other_display_order = other_task['display_order']
            db.execute("UPDATE onboarding_tasks SET display_order = ? WHERE id = ?", (other_display_order, current_task['id']))
            db.execute("UPDATE onboarding_tasks SET display_order = ? WHERE id = ?", (current_display_order, other_task['id']))
            db.commit()
            flash("Task moved up.", "success")
        except sqlite3.Error as e:
            db.rollback()
            flash(f"Database error moving task: {e}", "error")
    else:
        flash("Task is already at the top or cannot be moved further up.", "info")

    return redirect(url_for('onboarding.manage_tasks', checklist_id=current_task['checklist_id']))

@bp.route('/tasks/<int:task_id>/move_down', methods=['POST'])
@admin_required
def move_task_down(task_id):
    db = get_db()
    current_task = db.execute("SELECT id, checklist_id, display_order FROM onboarding_tasks WHERE id = ?", (task_id,)).fetchone()
    if not current_task:
        flash("Task not found.", "error")
        return redirect(request.referrer or url_for('onboarding.manage_checklists'))

    other_task = db.execute(
        """SELECT id, display_order FROM onboarding_tasks
           WHERE checklist_id = ? AND (display_order > ? OR (display_order = ? AND id > ?))
           ORDER BY display_order ASC, id ASC LIMIT 1""",
        (current_task['checklist_id'], current_task['display_order'], current_task['display_order'], current_task['id'])
    ).fetchone()

    if other_task:
        try:
            db.execute('BEGIN')
            current_display_order = current_task['display_order']
            other_display_order = other_task['display_order']
            db.execute("UPDATE onboarding_tasks SET display_order = ? WHERE id = ?", (other_display_order, current_task['id']))
            db.execute("UPDATE onboarding_tasks SET display_order = ? WHERE id = ?", (current_display_order, other_task['id']))
            db.commit()
            flash("Task moved down.", "success")
        except sqlite3.Error as e:
            db.rollback()
            flash(f"Database error moving task: {e}", "error")
    else:
        flash("Task is already at the bottom or cannot be moved further down.", "info")

    return redirect(url_for('onboarding.manage_tasks', checklist_id=current_task['checklist_id']))


@bp.route('/checklists/<int:checklist_id>/tasks/new', methods=('GET', 'POST'))
@admin_required
def create_task(checklist_id):
    db = get_db()
    checklist = db.execute('SELECT id, name FROM onboarding_checklists WHERE id = ?', (checklist_id,)).fetchone()
    if not checklist: flash('Checklist not found.', 'error'); return redirect(url_for('onboarding.manage_checklists'))
    potential_prerequisites = db.execute("SELECT id, task_name, display_order FROM onboarding_tasks WHERE checklist_id = ? ORDER BY display_order, task_name", (checklist_id,)).fetchall()

    if request.method == 'POST':
        task_name = request.form.get('task_name')
        description = request.form.get('description')
        responsible_role = request.form.get('responsible_role')
        due_days_str = request.form.get('due_days_after_start')
        display_order_str = request.form.get('display_order')
        depends_on_task_id_str = request.form.get('depends_on_task_id')
        resource_url = request.form.get('resource_url', '').strip()
        resource_file = request.files.get('resource_file')

        due_days = int(due_days_str) if due_days_str and due_days_str.strip().isdigit() else None
        display_order = 0
        if display_order_str and display_order_str.strip().isdigit() and int(display_order_str) >= 0:
            display_order = int(display_order_str)
        else:
            max_order_result = db.execute("SELECT MAX(display_order) FROM onboarding_tasks WHERE checklist_id = ?", (checklist_id,)).fetchone()
            display_order = (max_order_result[0] or 0) + 10
        depends_on_task_id = int(depends_on_task_id_str) if depends_on_task_id_str and depends_on_task_id_str.strip().isdigit() else None
        error = None
        # ... (validation logic remains the same) ...
        if not task_name: error = 'Task name is required.'
        elif not responsible_role: error = 'Responsible role required.'
        elif responsible_role not in ['Employee', 'Manager', 'HR', 'IT']: error = 'Invalid responsible role.'
        if depends_on_task_id:
            valid_dependency = db.execute("SELECT id FROM onboarding_tasks WHERE id = ? AND checklist_id = ?", (depends_on_task_id, checklist_id)).fetchone()
            if not valid_dependency: error = "Invalid prerequisite task selected."


        stored_filename_for_db = None
        original_filename_for_db = None
        if resource_file and resource_file.filename:
            if not allowed_file(resource_file.filename): error = "Invalid file type for attachment."
            else:
                original_filename_for_db = secure_filename(resource_file.filename)
                # Use UTC time for filenames to ensure uniqueness and avoid timezone issues
                stored_filename_for_db = f"{datetime.now(pytz.utc).strftime('%Y%m%d%H%M%S%f')}_{original_filename_for_db}"
                try:
                    if not os.path.exists(current_app.config['UPLOAD_FOLDER']): os.makedirs(current_app.config['UPLOAD_FOLDER'])
                    resource_file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db))
                except Exception as e: error = f"Could not save uploaded file: {e}"; stored_filename_for_db = None; original_filename_for_db = None
        if error:
            flash(error, 'error')
            # ... (error handling for file removal remains the same) ...
            if stored_filename_for_db and os.path.exists(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db)):
                try: os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db))
                except OSError: current_app.logger.error(f"Could not remove partially uploaded file: {stored_filename_for_db}")
            form_data = {**request.form, 'display_order': display_order_str or str(display_order)} # Repopulate form
            return render_template('onboarding/edit_task.html', title=f"Add Task to: {checklist['name']}", checklist=checklist, task=form_data, form_action_url=url_for('onboarding.create_task', checklist_id=checklist_id), potential_prerequisites=potential_prerequisites, ALLOWED_EXTENSIONS=ALLOWED_EXTENSIONS)

        else:
            try:
                db.execute('BEGIN')
                cursor = db.execute(
                    '''INSERT INTO onboarding_tasks (checklist_id, task_name, description, responsible_role,
                        due_days_after_start, display_order, depends_on_task_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (checklist_id, task_name, description, responsible_role, due_days, display_order, depends_on_task_id))
                new_task_id = cursor.lastrowid
                # uploaded_at for attachments is DEFAULT CURRENT_TIMESTAMP
                if resource_url:
                    db.execute(
                        """INSERT INTO onboarding_task_attachments
                           (onboarding_task_id, file_name, stored_file_name, attachment_type, url)
                           VALUES (?, ?, ?, ?, ?)""",
                        (new_task_id, resource_url, resource_url, 'template_link', resource_url)
                    )
                if stored_filename_for_db and original_filename_for_db:
                    db.execute(
                        """INSERT INTO onboarding_task_attachments
                           (onboarding_task_id, file_name, stored_file_name, attachment_type)
                           VALUES (?, ?, ?, ?)""",
                        (new_task_id, original_filename_for_db, stored_filename_for_db, 'template_file')
                    )
                db.commit()
                flash(f"Task '{task_name}' added.", 'success')
                return redirect(url_for('onboarding.manage_tasks', checklist_id=checklist_id))
            except sqlite3.Error as e:
                db.rollback()
                flash(f"DB error: {e}", "error")
                # ... (error handling for file removal remains the same) ...
                if stored_filename_for_db and os.path.exists(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db)):
                    try: os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db))
                    except OSError: current_app.logger.error(f"Could not remove partially uploaded file after DB error: {stored_filename_for_db}")


    return render_template('onboarding/edit_task.html', title=f"Add Task to: {checklist['name']}", checklist=checklist, task=None, form_action_url=url_for('onboarding.create_task', checklist_id=checklist_id), potential_prerequisites=potential_prerequisites, ALLOWED_EXTENSIONS=ALLOWED_EXTENSIONS)


@bp.route('/task-entry/<int:task_id_val>/alter', methods=('GET', 'POST'))
@admin_required
def alter_onboarding_task(task_id_val):
    db = get_db()
    task = db.execute('SELECT * FROM onboarding_tasks WHERE id = ?', (task_id_val,)).fetchone()
    if not task: flash('Task not found.', 'error'); return redirect(url_for('onboarding.manage_checklists'))
    checklist = db.execute('SELECT id, name FROM onboarding_checklists WHERE id = ?', (task['checklist_id'],)).fetchone()
    if not checklist: flash('Parent checklist not found.', 'error'); return redirect(url_for('onboarding.manage_checklists'))
    potential_prerequisites = db.execute("SELECT id, task_name, display_order FROM onboarding_tasks WHERE checklist_id = ? AND id != ? ORDER BY display_order, task_name", (task['checklist_id'], task_id_val)).fetchall()
    
    # Fetch uploaded_at for existing attachments as UTC string if to be displayed
    existing_attachments = db.execute(
        "SELECT id, onboarding_task_id, employee_onboarding_status_id, uploader_user_id, file_name, stored_file_name, attachment_type, url, strftime('%Y-%m-%d %H:%M:%S', uploaded_at) as uploaded_at_utc_str FROM onboarding_task_attachments WHERE onboarding_task_id = ?", 
        (task_id_val,)
    ).fetchall()


    if request.method == 'POST':
        task_name = request.form.get('task_name')
        description = request.form.get('description')
        responsible_role = request.form.get('responsible_role')
        due_days_str = request.form.get('due_days_after_start')
        display_order_str = request.form.get('display_order', str(task['display_order']))
        depends_on_task_id_str = request.form.get('depends_on_task_id')
        resource_url = request.form.get('resource_url', '').strip()
        resource_file = request.files.get('resource_file')
        attachments_to_delete_ids = request.form.getlist('delete_attachment')
        due_days = int(due_days_str) if due_days_str and due_days_str.strip().isdigit() else None
        display_order = task['display_order'] # Default to current if not changed or invalid
        if display_order_str and display_order_str.strip().isdigit() and int(display_order_str) >= 0 :
            display_order = int(display_order_str)
        depends_on_task_id = int(depends_on_task_id_str) if depends_on_task_id_str and depends_on_task_id_str.strip().isdigit() else None
        error = None
        # ... (validation logic remains the same) ...
        if not task_name: error = 'Task name is required.'
        elif not responsible_role: error = 'Responsible role required.'
        elif responsible_role not in ['Employee', 'Manager', 'HR', 'IT']: error = 'Invalid responsible role.'
        if depends_on_task_id:
            if depends_on_task_id == task_id_val: error = "A task cannot depend on itself."
            else:
                valid_dependency = db.execute("SELECT id FROM onboarding_tasks WHERE id = ? AND checklist_id = ?", (depends_on_task_id, task['checklist_id'])).fetchone()
                if not valid_dependency: error = "Invalid prerequisite task selected."

        stored_filename_for_db = None
        original_filename_for_db = None
        if resource_file and resource_file.filename:
            if not allowed_file(resource_file.filename): error = "Invalid file type for attachment."
            else:
                original_filename_for_db = secure_filename(resource_file.filename)
                stored_filename_for_db = f"{datetime.now(pytz.utc).strftime('%Y%m%d%H%M%S%f')}_{original_filename_for_db}"
                try:
                    if not os.path.exists(current_app.config['UPLOAD_FOLDER']): os.makedirs(current_app.config['UPLOAD_FOLDER'])
                    resource_file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db))
                except Exception as e: error = f"Could not save uploaded file: {e}"; stored_filename_for_db = None; original_filename_for_db = None
        if error:
            flash(error, 'error')
            # ... (error handling for file removal remains the same) ...
            if stored_filename_for_db and os.path.exists(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db)):
                try: os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db))
                except OSError: current_app.logger.error(f"Could not remove partially uploaded file: {stored_filename_for_db}")
            current_form_data = {**dict(task), **request.form, 'display_order': display_order_str} # Repopulate form
            return render_template('onboarding/edit_task.html', title=f"Edit Task: {task['task_name']}", checklist=checklist, task=current_form_data, form_action_url=url_for('onboarding.alter_onboarding_task', task_id_val=task_id_val), potential_prerequisites=potential_prerequisites, existing_attachments=existing_attachments, ALLOWED_EXTENSIONS=ALLOWED_EXTENSIONS)

        else:
            try:
                db.execute('BEGIN')
                db.execute(
                    '''UPDATE onboarding_tasks SET task_name = ?, description = ?, responsible_role = ?,
                       due_days_after_start = ?, display_order = ?, depends_on_task_id = ?
                       WHERE id = ?''',
                    (task_name, description, responsible_role, due_days, display_order, depends_on_task_id, task_id_val))
                # ... (attachment deletion logic remains the same) ...
                if attachments_to_delete_ids:
                    for attachment_id_str in attachments_to_delete_ids:
                        try:
                            attachment_id_to_delete = int(attachment_id_str)
                            attachment_to_remove = db.execute("SELECT stored_file_name, attachment_type FROM onboarding_task_attachments WHERE id = ? AND onboarding_task_id = ?",
                                                              (attachment_id_to_delete, task_id_val)).fetchone()
                            if attachment_to_remove:
                                if attachment_to_remove['attachment_type'] == 'template_file' and attachment_to_remove['stored_file_name']:
                                    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment_to_remove['stored_file_name'])
                                    if os.path.exists(file_path):
                                        try: os.remove(file_path)
                                        except OSError as e_del: current_app.logger.error(f"Error deleting template attachment file {file_path}: {e_del}")
                                db.execute("DELETE FROM onboarding_task_attachments WHERE id = ?", (attachment_id_to_delete,))
                        except ValueError: current_app.logger.error(f"Invalid attachment ID for deletion: {attachment_id_str}")
                # uploaded_at for new attachments is DEFAULT CURRENT_TIMESTAMP
                if resource_url:
                    db.execute("INSERT INTO onboarding_task_attachments (onboarding_task_id, file_name, stored_file_name, attachment_type, url) VALUES (?, ?, ?, ?, ?)",
                               (task_id_val, resource_url, resource_url, 'template_link', resource_url))
                if stored_filename_for_db and original_filename_for_db:
                    db.execute("INSERT INTO onboarding_task_attachments (onboarding_task_id, file_name, stored_file_name, attachment_type) VALUES (?, ?, ?, ?)",
                               (task_id_val, original_filename_for_db, stored_filename_for_db, 'template_file'))
                db.commit()
                flash(f"Task '{task_name}' updated.", 'success')
                return redirect(url_for('onboarding.manage_tasks', checklist_id=checklist['id']))
            except sqlite3.Error as e:
                db.rollback()
                flash(f"DB error: {e}", "error")
                # ... (error handling for file removal remains the same) ...
                if stored_filename_for_db and os.path.exists(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db)):
                    try: os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename_for_db))
                    except OSError: current_app.logger.error(f"Could not remove partially uploaded file after DB error: {stored_filename_for_db}")


    return render_template('onboarding/edit_task.html', title=f"Edit Task: {task['task_name']}", checklist=checklist, task=task, form_action_url=url_for('onboarding.alter_onboarding_task', task_id_val=task_id_val), potential_prerequisites=potential_prerequisites, existing_attachments=existing_attachments, ALLOWED_EXTENSIONS=ALLOWED_EXTENSIONS)


@bp.route('/tasks/<int:task_id>/delete', methods=('POST',))
@admin_required
def delete_task(task_id):
    # ... (delete_task logic remains the same as it doesn't display timestamps) ...
    db = get_db()
    task = db.execute('SELECT id, task_name, checklist_id FROM onboarding_tasks WHERE id = ?', (task_id,)).fetchone()
    if not task: flash('Task not found.', 'error'); return redirect(url_for('onboarding.manage_checklists'))
    checklist_id = task['checklist_id']
    dependent_tasks = db.execute("SELECT COUNT(id) FROM onboarding_tasks WHERE depends_on_task_id = ?", (task_id,)).fetchone()[0]
    if dependent_tasks > 0:
        flash(f"Cannot delete task '{task['task_name']}' because {dependent_tasks} other task(s) depend on it. Please update dependencies first.", 'error')
        return redirect(url_for('onboarding.manage_tasks', checklist_id=checklist_id))
    assigned_count = db.execute("SELECT COUNT(id) FROM employee_onboarding_status WHERE task_id = ?", (task_id,)).fetchone()[0]
    if assigned_count > 0:
        flash(f"Cannot delete '{task['task_name']}', assigned to {assigned_count} employee(s).", 'error')
        return redirect(url_for('onboarding.manage_tasks', checklist_id=checklist_id))

    attachments_to_delete = db.execute("SELECT id, stored_file_name, attachment_type FROM onboarding_task_attachments WHERE onboarding_task_id = ?", (task_id,)).fetchall()
    try:
        db.execute('BEGIN')
        for att in attachments_to_delete:
            if att['attachment_type'] == 'template_file' and att['stored_file_name']:
                file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], att['stored_file_name'])
                if os.path.exists(file_path):
                    try: os.remove(file_path)
                    except OSError as e_del: current_app.logger.error(f"Error deleting template attachment file {file_path}: {e_del}")
        db.execute("DELETE FROM onboarding_task_attachments WHERE onboarding_task_id = ?", (task_id,))
        db.execute('DELETE FROM onboarding_tasks WHERE id = ?', (task_id,));
        db.commit()
        flash(f"Task '{task['task_name']}' and its template attachments deleted.", 'success')
    except sqlite3.Error as e:
        db.rollback()
        flash(f"DB error deleting task: {e}", "error")
    return redirect(url_for('onboarding.manage_tasks', checklist_id=checklist_id))

# --- Assign Checklist to Existing Employee (Admin/Manager) ---
# (assign_onboarding_to_employee_view does not display timestamps needing localization)
# ... (This route remains unchanged) ...
@bp.route('/assign', methods=('GET', 'POST'))
@manager_required # Or admin_required if only admins can do this
def assign_onboarding_to_employee_view():
    db = get_db()
    if request.method == 'POST':
        employee_user_id = request.form.get('employee_user_id', type=int)
        checklist_id = request.form.get('checklist_id', type=int)
        if not employee_user_id or not checklist_id: flash("Employee and Checklist must be selected.", "error")
        else:
            assign_ok, assign_msg = assign_checklist_to_employee(db, employee_user_id, checklist_id, g.user['id'])
            flash(assign_msg, 'success' if assign_ok else 'error')
            if assign_ok:
                # Redirect based on role or to a more general tracking page
                if g.user['role'] == 'admin': return redirect(url_for('onboarding.admin_tracking_view'))
                else: return redirect(url_for('onboarding.responsible_tasks_view')) # Or dashboard

    # Filter employees for managers if needed
    employees_query = "SELECT id, full_name, department FROM users WHERE role != 'admin'" # Exclude admin from being assigned
    params = []
    if g.user['role'] == 'manager': # If manager, only show their direct reports or all non-admins if preferred
        # This example shows all non-admins. Adjust if managers should only assign to their reports.
        # employees_query += " AND manager_id = ? " 
        # params.append(g.user['id'])
        pass
    employees_query += " ORDER BY full_name"
    employees = db.execute(employees_query, params).fetchall()
    checklists = db.execute("SELECT id, name, is_default FROM onboarding_checklists ORDER BY is_default DESC, name").fetchall()
    return render_template('onboarding/assign_onboarding.html', employees=employees, checklists=checklists)


# --- Helper to get task statuses for an employee ---
def get_employee_task_statuses(db, employee_user_id):
    statuses_raw = db.execute( """SELECT ot.id as task_id, eos.status FROM employee_onboarding_status eos JOIN onboarding_tasks ot ON eos.task_id = ot.id WHERE eos.employee_user_id = ?""", (employee_user_id,)).fetchall()
    return {row['task_id']: row['status'] for row in statuses_raw}

# --- Helper to get task template attachments ---
def get_task_template_attachments(db, task_id):
    # Fetch uploaded_at as UTC string if it will be displayed directly by template
    rows = db.execute(
        "SELECT id, onboarding_task_id, file_name, stored_file_name, attachment_type, url, strftime('%Y-%m-%d %H:%M:%S', uploaded_at) as uploaded_at_utc_str FROM onboarding_task_attachments WHERE onboarding_task_id = ? AND attachment_type IN ('template_file', 'template_link')", 
        (task_id,)
    ).fetchall()
    return [dict(row) for row in rows]


# --- Helper to get user submitted attachments for a task instance ---
def get_user_submitted_attachments(db, employee_onboarding_status_id):
    # Fetch uploaded_at as UTC string if it will be displayed directly by template
    rows = db.execute(
        "SELECT id, employee_onboarding_status_id, uploader_user_id, file_name, stored_file_name, attachment_type, url, strftime('%Y-%m-%d %H:%M:%S', uploaded_at) as uploaded_at_utc_str FROM onboarding_task_attachments WHERE employee_onboarding_status_id = ? AND attachment_type = 'user_submission'", 
        (employee_onboarding_status_id,)
    ).fetchall()
    return [dict(row) for row in rows]


# --- Employee Onboarding Task View ---
@bp.route('/my-tasks')
@bp.route('/employee-tasks/<int:employee_id_override>')
@login_required
def my_onboarding_tasks(employee_id_override=None):
    db = get_db()
    target_employee_id = None; is_own_view = False ; page_title = "My Onboarding Tasks"
    
    if employee_id_override:
        # ... (authorization logic remains the same) ...
        can_view_other = False
        target_employee_data = db.execute("SELECT id, full_name, hire_date, manager_id FROM users WHERE id = ?", (employee_id_override,)).fetchone()
        if not target_employee_data: flash("Employee not found.", "error"); return redirect(url_for('onboarding.admin_tracking_view') if g.user['role'] == 'admin' else url_for('main.dashboard'))
        if g.user['role'] == 'admin' or (g.user['role'] == 'manager' and target_employee_data['manager_id'] == g.user['id']): can_view_other = True
        if can_view_other:
            target_employee_id = employee_id_override; page_title = f"Onboarding: {target_employee_data['full_name']}"
            is_own_view = (g.user['id'] == target_employee_id)
        else: flash("You are not authorized to view this employee's tasks.", "error"); return redirect(url_for('onboarding.my_onboarding_tasks'))
    else:
        target_employee_id = g.user['id']
        is_own_view = True
    
    trigger_pending_task_reminders(db, target_employee_id)

    query = """
        SELECT eos.id as status_id, eos.status, 
               strftime('%Y-%m-%d %H:%M:%S', eos.completed_date) as completed_date_utc_str, 
               eos.notes, 
               strftime('%Y-%m-%d %H:%M:%S', eos.last_reminder_sent_at) as last_reminder_sent_at_utc_str,
               ot.id as task_id, ot.task_name, ot.description, ot.responsible_role,
               ot.due_days_after_start, ot.depends_on_task_id, pt.task_name as prerequisite_task_name,
               oc.name as checklist_name, eos.employee_user_id,
               u_emp.full_name as employee_full_name, u_emp.manager_id as employee_manager_id, u_emp.hire_date as employee_hire_date
        FROM employee_onboarding_status eos
        JOIN onboarding_tasks ot ON eos.task_id = ot.id
        JOIN onboarding_checklists oc ON ot.checklist_id = oc.id
        JOIN users u_emp ON eos.employee_user_id = u_emp.id
        LEFT JOIN onboarding_tasks pt ON ot.depends_on_task_id = pt.id
        WHERE eos.employee_user_id = ? ORDER BY oc.name, ot.display_order, ot.task_name """
    employee_tasks_raw = db.execute(query, (target_employee_id,)).fetchall()
    employee_task_status_map = get_employee_task_statuses(db, target_employee_id)
    tasks_for_display = []
    
    today_date_obj = date.today() 
    due_soon_date_obj = today_date_obj + timedelta(days=DUE_SOON_THRESHOLD_DAYS)

    if employee_tasks_raw:
        for task_row_raw in employee_tasks_raw:
            task = dict(task_row_raw); task['due_date_str'] = "N/A"
            # ... (due_date_str calculation logic remains the same) ...
            current_task_hire_date_str = task.get('employee_hire_date') 
            if current_task_hire_date_str and task.get('due_days_after_start') is not None:
                try:
                    hire_date_obj = datetime.strptime(current_task_hire_date_str, '%Y-%m-%d').date()
                    task_due_date_dt = hire_date_obj + timedelta(days=task['due_days_after_start']) 
                    task['due_date_str'] = task_due_date_dt.strftime('%Y-%m-%d')
                except ValueError:
                    task['due_date_str'] = "Invalid Hire Date"

            task['is_actionable'] = True; task['prerequisite_unmet_name'] = None
            # ... (is_actionable logic remains the same) ...
            if task['depends_on_task_id']:
                prerequisite_status = employee_task_status_map.get(task['depends_on_task_id'])
                if prerequisite_status != 'Completed':
                    task['is_actionable'] = False; task['prerequisite_unmet_name'] = task['prerequisite_task_name'] or f"Task ID {task['depends_on_task_id']}"


            task['template_attachments'] = get_task_template_attachments(db, task['task_id'])
            task['user_submissions'] = get_user_submitted_attachments(db, task['status_id'])
            
            # Fetch comment created_at as UTC string
            task_comments_raw = db.execute(
                """SELECT otc.id, otc.user_id, otc.comment_text, 
                          strftime('%Y-%m-%d %H:%M:%S', otc.created_at) as created_at_utc_str, 
                          u.full_name as commenter_name
                   FROM onboarding_task_comments otc
                   JOIN users u ON otc.user_id = u.id
                   WHERE otc.employee_onboarding_status_id = ?
                   ORDER BY otc.created_at ASC""",
                (task['status_id'],)
            ).fetchall()
            task['comments'] = [dict(comment) for comment in task_comments_raw]
            tasks_for_display.append(task)

    return render_template('onboarding/my_onboarding_tasks.html',
                           employee_tasks=tasks_for_display,
                           page_title=page_title,
                           is_own_view=is_own_view,
                           viewed_employee_id=target_employee_id,
                           today_date_str=today_date_obj.strftime('%Y-%m-%d'), 
                           due_soon_date_str=due_soon_date_obj.strftime('%Y-%m-%d'), 
                           ALLOWED_EXTENSIONS=ALLOWED_EXTENSIONS
                           )

# --- Manager/HR/IT View for Their Responsible Tasks ---
@bp.route('/responsible-tasks')
@login_required
def responsible_tasks_view():
    db = get_db(); current_user_role_db = g.user['role']; current_user_id = g.user['id']
    # ... (role mapping and initial query building logic remains the same) ...
    role_mapping = {'manager': 'Manager', 'hr': 'HR', 'it': 'IT'}
    responsible_role_to_query = role_mapping.get(current_user_role_db)
    if not responsible_role_to_query and current_user_role_db != 'admin':
        flash("This view is for users with specific onboarding responsibilities.", "info"); return redirect(url_for('main.dashboard'))

    # Define the fields to select, including formatted timestamps
    select_fields = """
        eos.id as status_id, eos.status, 
        strftime('%Y-%m-%d %H:%M:%S', eos.completed_date) as completed_date_utc_str, 
        eos.notes, 
        strftime('%Y-%m-%d %H:%M:%S', eos.last_reminder_sent_at) as last_reminder_sent_at_utc_str,
        ot.id as task_id, ot.task_name, ot.description, ot.responsible_role,
        ot.due_days_after_start, ot.depends_on_task_id, pt.task_name as prerequisite_task_name,
        oc.name as checklist_name, u.full_name as employee_name, u.id as employee_user_id,
        u.hire_date as employee_hire_date, u.manager_id as employee_manager_id
    """
    tasks_query = f"""
        SELECT {select_fields}
        FROM employee_onboarding_status eos
        JOIN onboarding_tasks ot ON eos.task_id = ot.id 
        JOIN onboarding_checklists oc ON ot.checklist_id = oc.id
        JOIN users u ON eos.employee_user_id = u.id 
        LEFT JOIN onboarding_tasks pt ON ot.depends_on_task_id = pt.id
        WHERE 1=1 
    """
    params = []
    if current_user_role_db == 'admin':
        admin_responsible_roles = ['Manager', 'HR', 'IT', 'Employee'] 
        placeholders = ','.join('?' * len(admin_responsible_roles))
        tasks_query += f" AND ot.responsible_role IN ({placeholders})"
        params.extend(admin_responsible_roles)
    else:
        tasks_query += " AND ot.responsible_role = ? "
        params.append(responsible_role_to_query)
        if current_user_role_db == 'manager': tasks_query += " AND u.manager_id = ? "; params.append(current_user_id)
    tasks_query += " ORDER BY u.full_name, eos.status DESC, oc.name, ot.display_order, ot.task_name"
    
    responsible_tasks_raw = db.execute(tasks_query, tuple(params)).fetchall()
    tasks_for_template = []
    
    employee_ids_for_reminders = set(task_row['employee_user_id'] for task_row in responsible_tasks_raw)
    for emp_id in employee_ids_for_reminders:
        trigger_pending_task_reminders(db, emp_id)
    
    # Re-fetch after reminders, in case status changed (though unlikely in this flow)
    responsible_tasks_raw = db.execute(tasks_query, tuple(params)).fetchall() 

    if responsible_tasks_raw: 
        all_employee_ids_involved = list(set(row['employee_user_id'] for row in responsible_tasks_raw))
        all_task_statuses_map = {emp_id: get_employee_task_statuses(db, emp_id) for emp_id in all_employee_ids_involved}
        
        today_date_obj = date.today()
        due_soon_date_obj = today_date_obj + timedelta(days=DUE_SOON_THRESHOLD_DAYS)

        for task_row_raw in responsible_tasks_raw:
            task = dict(task_row_raw); task['due_date_str'] = "N/A"
            # ... (due_date_str calculation remains the same) ...
            employee_hire_date_str = task.get('employee_hire_date')
            if employee_hire_date_str and task.get('due_days_after_start') is not None:
                try:
                    hire_date_obj = datetime.strptime(employee_hire_date_str, '%Y-%m-%d').date()
                    task_due_date_dt = hire_date_obj + timedelta(days=task['due_days_after_start']) 
                    task['due_date_str'] = task_due_date_dt.strftime('%Y-%m-%d')
                except ValueError: task['due_date_str'] = "Invalid Hire Date"

            task['is_actionable'] = True; task['prerequisite_unmet_name'] = None
            # ... (is_actionable logic remains the same) ...
            if task['depends_on_task_id']:
                employee_specific_statuses = all_task_statuses_map.get(task['employee_user_id'], {})
                prerequisite_status = employee_specific_statuses.get(task['depends_on_task_id'])
                if prerequisite_status != 'Completed':
                    task['is_actionable'] = False; task['prerequisite_unmet_name'] = task['prerequisite_task_name'] or f"Task ID {task['depends_on_task_id']}"


            task['template_attachments'] = get_task_template_attachments(db, task['task_id'])
            task['user_submissions'] = get_user_submitted_attachments(db, task['status_id'])
            
            # Fetch comment created_at as UTC string
            task_comments_raw = db.execute(
                """SELECT otc.id, otc.user_id, otc.comment_text, 
                          strftime('%Y-%m-%d %H:%M:%S', otc.created_at) as created_at_utc_str, 
                          u.full_name as commenter_name
                   FROM onboarding_task_comments otc
                   JOIN users u ON otc.user_id = u.id
                   WHERE otc.employee_onboarding_status_id = ?
                   ORDER BY otc.created_at ASC""",
                (task['status_id'],)
            ).fetchall()
            task['comments'] = [dict(comment) for comment in task_comments_raw]
            tasks_for_template.append(task)

    return render_template('onboarding/responsible_tasks.html',
                           responsible_tasks=tasks_for_template,
                           page_title="Tasks I Am Responsible For",
                           today_date_str=date.today().strftime('%Y-%m-%d'), 
                           due_soon_date_str=(date.today() + timedelta(days=DUE_SOON_THRESHOLD_DAYS)).strftime('%Y-%m-%d'), 
                           ALLOWED_EXTENSIONS=ALLOWED_EXTENSIONS
                           )

# --- Admin Onboarding Tracking View ---
# (admin_tracking_view does not display specific timestamps needing localization in its current table)
# ... (This route remains unchanged) ...
@bp.route('/admin/tracking')
@admin_required
def admin_tracking_view():
    db = get_db()
    employees = db.execute("SELECT id, full_name, hire_date FROM users WHERE role != 'admin' ORDER BY full_name").fetchall()
    tracking_data = []
    
    for emp_for_reminder in employees:
        trigger_pending_task_reminders(db, emp_for_reminder['id'])

    # Re-fetch employees in case any status changed due to reminders (though unlikely here)
    employees = db.execute("SELECT id, full_name, hire_date FROM users WHERE role != 'admin' ORDER BY full_name").fetchall()

    for emp in employees:
        total_tasks = db.execute("SELECT COUNT(id) FROM employee_onboarding_status WHERE employee_user_id = ?", (emp['id'],)).fetchone()[0]
        completed_tasks = db.execute("SELECT COUNT(id) FROM employee_onboarding_status WHERE employee_user_id = ? AND status = 'Completed'", (emp['id'],)).fetchone()[0]
        assigned_checklists_raw = db.execute( """SELECT DISTINCT oc.name as checklist_name FROM employee_onboarding_status eos JOIN onboarding_tasks ot ON eos.task_id = ot.id JOIN onboarding_checklists oc ON ot.checklist_id = oc.id WHERE eos.employee_user_id = ?""", (emp['id'],)).fetchall()
        checklist_names = ", ".join([c['checklist_name'] for c in assigned_checklists_raw]) if assigned_checklists_raw else "N/A"
        tracking_data.append({ 'employee_id': emp['id'], 'full_name': emp['full_name'], 'hire_date': emp['hire_date'], 'checklist_name': checklist_names, 'total_tasks': total_tasks, 'completed_tasks': completed_tasks, 'progress_percent': (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0 })
    return render_template('onboarding/admin_tracking.html', tracking_data=tracking_data)


# --- Main Onboarding Index (Entry Point) ---
# ... (This route remains unchanged) ...
@bp.route('/')
@login_required
def index():
    if g.user:
        user_role = g.user['role']
        if user_role == 'admin': return redirect(url_for('onboarding.admin_tracking_view'))
        elif user_role in ['manager', 'hr', 'it']: return redirect(url_for('onboarding.responsible_tasks_view'))
        elif user_role == 'employee': return redirect(url_for('onboarding.my_onboarding_tasks'))
        else: flash(f"Onboarding view for role '{user_role}' not yet defined. Showing your tasks if any.", "info"); return redirect(url_for('onboarding.my_onboarding_tasks'))
    flash("User data not found, cannot determine onboarding view.", "error"); return redirect(url_for('main.dashboard'))


# --- Route to Update Task Status ---
@bp.route('/task-status/<int:status_id>/update', methods=['POST'])
@login_required
def update_task_status(status_id):
    db = get_db(); new_status = request.form.get('new_status'); notes = request.form.get('notes', '').strip()
    # ... (Authorization logic remains the same) ...
    task_status_record = db.execute( """SELECT eos.*, ot.responsible_role, ot.depends_on_task_id, ot.task_name as task_name, ot.checklist_id as task_checklist_id, u_emp.id as employee_user_id_for_task, u_emp.full_name as employee_full_name, u_emp.manager_id as employee_manager_id, u_emp.hire_date as employee_hire_date FROM employee_onboarding_status eos JOIN onboarding_tasks ot ON eos.task_id = ot.id JOIN users u_emp ON eos.employee_user_id = u_emp.id WHERE eos.id = ?""", (status_id,)).fetchone()
    if not task_status_record: flash("Task status not found.", "error"); return redirect(request.referrer or url_for('main.dashboard'))

    if new_status in ['Completed', 'N/A'] and task_status_record['depends_on_task_id'] and g.user['role'] != 'admin':
        prereq_status_row = db.execute( """SELECT status FROM employee_onboarding_status WHERE employee_user_id = ? AND task_id = ?""", (task_status_record['employee_user_id_for_task'], task_status_record['depends_on_task_id'])).fetchone()
        if not prereq_status_row or prereq_status_row['status'] != 'Completed':
            flash("Cannot update task: its prerequisite task is not yet completed.", "error"); return redirect(request.referrer or url_for('main.dashboard'))

    can_update = False; logged_in_user_id = g.user['id']; logged_in_user_role = g.user['role']
    task_responsible_role = task_status_record['responsible_role']; employee_id_of_task = task_status_record['employee_user_id_for_task']
    manager_of_employee_of_task = task_status_record['employee_manager_id']
    if logged_in_user_role == 'admin': can_update = True
    elif task_responsible_role == 'Employee' and employee_id_of_task == logged_in_user_id: can_update = True
    elif task_responsible_role == 'Manager' and logged_in_user_role == 'manager' and manager_of_employee_of_task == logged_in_user_id: can_update = True
    elif task_responsible_role.lower() == logged_in_user_role and logged_in_user_role in ['hr', 'it']: can_update = True
    if not can_update: flash("You do not have permission to update this task status.", "error"); return redirect(request.referrer or url_for('main.dashboard'))

    if new_status not in ['Pending', 'Completed', 'N/A']: flash("Invalid status.", "error"); return redirect(request.referrer or url_for('main.dashboard'))


    try:
        db.execute('BEGIN')
        # Set completed_date as explicit UTC string
        completed_date_val_utc_str = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S') if new_status == 'Completed' else None
        if new_status == 'Pending' or new_status == 'N/A': completed_date_val_utc_str = None
        
        db.execute("UPDATE employee_onboarding_status SET status = ?, completed_date = ?, notes = ? WHERE id = ?", 
                   (new_status, completed_date_val_utc_str, notes, status_id))
        db.commit(); flash("Task status updated.", "success")

        # ... (Notification logic remains the same, it uses task_name, etc.) ...
        checklist_name_for_notif_row = db.execute("SELECT name FROM onboarding_checklists WHERE id = (SELECT checklist_id FROM onboarding_tasks WHERE id = ?)", (task_status_record['task_id'],)).fetchone()
        checklist_name_str = checklist_name_for_notif_row['name'] if checklist_name_for_notif_row else "N/A"

        if new_status == 'Completed':
            if manager_of_employee_of_task and manager_of_employee_of_task != logged_in_user_id :
                send_task_completed_notification(employee_id_of_task, task_status_record['task_name'], logged_in_user_id, checklist_name_str, notes, related_status_id=status_id)

            unlocked_tasks_query = """
                SELECT ot.id as unlocked_task_id, ot.task_name as unlocked_task_name,
                       ot.responsible_role as unlocked_responsible_role, ot.due_days_after_start,
                       eos_unlocked.id as unlocked_status_id, 
                       eos_unlocked.employee_user_id,
                       u_emp.hire_date as employee_hire_date,
                       u_emp.manager_id as emp_manager_id,
                       checklist.name as checklist_name,
                       pt.task_name as completed_prerequisite_name
                FROM onboarding_tasks ot
                JOIN employee_onboarding_status eos_unlocked ON ot.id = eos_unlocked.task_id
                JOIN users u_emp ON eos_unlocked.employee_user_id = u_emp.id
                JOIN onboarding_checklists checklist ON ot.checklist_id = checklist.id
                JOIN onboarding_tasks pt ON ot.depends_on_task_id = pt.id
                WHERE ot.depends_on_task_id = ? AND eos_unlocked.employee_user_id = ? AND eos_unlocked.status = 'Pending'
            """
            unlocked_tasks = db.execute(unlocked_tasks_query, (task_status_record['task_id'], employee_id_of_task) ).fetchall()

            for unlocked_task in unlocked_tasks:
                responsible_user_id_for_notification = None
                # ... (logic to determine responsible_user_id_for_notification for unlocked task) ...
                if unlocked_task['unlocked_responsible_role'] == 'Employee': responsible_user_id_for_notification = unlocked_task['employee_user_id']
                elif unlocked_task['unlocked_responsible_role'] == 'Manager':
                    if unlocked_task['emp_manager_id']: responsible_user_id_for_notification = unlocked_task['emp_manager_id']
                elif unlocked_task['unlocked_responsible_role'] == 'HR':
                    hr_user = db.execute("SELECT id FROM users WHERE role = 'hr' LIMIT 1").fetchone()
                    if hr_user: responsible_user_id_for_notification = hr_user['id']
                elif unlocked_task['unlocked_responsible_role'] == 'IT':
                    it_user = db.execute("SELECT id FROM users WHERE role = 'it' LIMIT 1").fetchone()
                    if it_user: responsible_user_id_for_notification = it_user['id']

                if responsible_user_id_for_notification:
                    due_date_str = None
                    if unlocked_task['employee_hire_date'] and unlocked_task['due_days_after_start'] is not None:
                        try:
                            hire_dt = datetime.strptime(unlocked_task['employee_hire_date'], '%Y-%m-%d').date()
                            due_dt = hire_dt + timedelta(days=unlocked_task['due_days_after_start'])
                            due_date_str = due_dt.strftime('%Y-%m-%d')
                        except ValueError: pass
                    send_prerequisite_met_notification(
                        unlocked_task['employee_user_id'],
                        unlocked_task['unlocked_task_name'],
                        responsible_user_id_for_notification,
                        unlocked_task['checklist_name'],
                        due_date_str,
                        unlocked_task['completed_prerequisite_name'],
                        related_status_id=unlocked_task['unlocked_status_id']
                    )


    except sqlite3.Error as e: db.rollback(); flash(f"DB error: {e}", "error")
    except Exception as e_gen: db.rollback(); flash(f"General error updating task: {e_gen}", "error"); current_app.logger.error(f"General error in update_task_status: {e_gen}")

    # ... (Redirect logic remains the same) ...
    if request.referrer:
        if f'/employee-tasks/{employee_id_of_task}' in request.referrer and (g.user['role'] == 'admin' or (g.user['role'] == 'manager' and manager_of_employee_of_task == g.user['id'] )):
             return redirect(url_for('onboarding.my_onboarding_tasks', employee_id_override=employee_id_of_task))
        if 'responsible-tasks' in request.referrer and g.user['role'] in ['admin', 'manager', 'hr', 'it']:
             return redirect(url_for('onboarding.responsible_tasks_view'))
        if 'my-tasks' in request.referrer and employee_id_of_task == g.user['id']:
             return redirect(url_for('onboarding.my_onboarding_tasks'))
        if 'admin/tracking' in request.referrer and g.user['role'] == 'admin':
             return redirect(url_for('onboarding.admin_tracking_view'))
    return redirect(url_for('main.dashboard'))


# --- Route to Download Task Attachment ---
# (download_task_attachment: uploaded_at for attachments is DEFAULT CURRENT_TIMESTAMP,
#  if displayed, it would come from get_task_template_attachments or get_user_submitted_attachments
#  which are now updated to fetch uploaded_at_utc_str)
# ... (This route remains largely unchanged regarding its own logic) ...
@bp.route('/task-attachment/<int:attachment_id>/download')
@login_required
def download_task_attachment(attachment_id):
    db = get_db()
    attachment = db.execute(
        "SELECT * FROM onboarding_task_attachments WHERE id = ?",
        (attachment_id,)
    ).fetchone()

    if not attachment:
        flash("Attachment not found.", "error")
        return redirect(request.referrer or url_for('onboarding.index'))

    # ... (Authorization logic for download remains the same) ...
    can_download = False
    if g.user['role'] == 'admin':
        can_download = True
    else:
        if attachment['onboarding_task_id']: 
            task_assignment = db.execute(
                "SELECT 1 FROM employee_onboarding_status WHERE employee_user_id = ? AND task_id = ?",
                (g.user['id'], attachment['onboarding_task_id'])
            ).fetchone()
            if task_assignment: can_download = True
            else: 
                task_info = db.execute("SELECT responsible_role FROM onboarding_tasks WHERE id = ?", (attachment['onboarding_task_id'],)).fetchone()
                if task_info and task_info['responsible_role'].lower() == g.user['role'] and g.user['role'] in ['hr', 'it']:
                    can_download = True
                else: 
                    assigned_employees = db.execute("SELECT employee_user_id FROM employee_onboarding_status WHERE task_id = ?", (attachment['onboarding_task_id'],)).fetchall()
                    for emp_assign in assigned_employees:
                        emp_details = db.execute("SELECT manager_id FROM users WHERE id = ?", (emp_assign['employee_user_id'],)).fetchone()
                        if emp_details and emp_details['manager_id'] == g.user['id']:
                            can_download = True; break
        elif attachment['employee_onboarding_status_id']: 
            status_record = db.execute(
                """SELECT eos.employee_user_id, ot.responsible_role, u.manager_id
                   FROM employee_onboarding_status eos
                   JOIN onboarding_tasks ot ON eos.task_id = ot.id
                   JOIN users u ON eos.employee_user_id = u.id
                   WHERE eos.id = ?""",
                (attachment['employee_onboarding_status_id'],)
            ).fetchone()
            if status_record:
                if status_record['employee_user_id'] == g.user['id']: can_download = True 
                elif g.user['role'] == 'manager' and status_record['manager_id'] == g.user['id']: can_download = True 
                elif status_record['responsible_role'].lower() == g.user['role'] and g.user['role'] in ['hr', 'it']: can_download = True 
                elif attachment['uploader_user_id'] == g.user['id']: can_download = True


    if not can_download:
        flash("You do not have permission to download this attachment.", "error")
        return redirect(request.referrer or url_for('onboarding.index'))

    if attachment['attachment_type'] in ['template_file', 'user_submission'] and attachment['stored_file_name']:
        try:
            return send_from_directory(
                current_app.config['UPLOAD_FOLDER'],
                attachment['stored_file_name'],
                as_attachment=True,
                download_name=attachment['file_name']
            )
        except Exception as e:
            current_app.logger.error(f"Error serving file {attachment['stored_file_name']}: {e}")
            flash("Error downloading file.", "error")
    elif attachment['attachment_type'] == 'template_link' and attachment['url']:
        return redirect(attachment['url'])
    else:
        flash("Attachment is not downloadable or is misconfigured.", "error")

    return redirect(request.referrer or url_for('onboarding.index'))


# --- Route to upload attachment for a specific task instance ---
# (upload_task_instance_attachment: uploaded_at for attachments is DEFAULT CURRENT_TIMESTAMP)
# ... (This route remains unchanged regarding timestamp logic) ...
@bp.route('/task-instance/<int:status_id>/upload-attachment', methods=['POST'])
@login_required
def upload_task_instance_attachment(status_id):
    db = get_db()
    task_status_info = db.execute(
        """SELECT eos.id, eos.employee_user_id, eos.task_id, ot.responsible_role, u.manager_id as employee_manager_id
           FROM employee_onboarding_status eos
           JOIN onboarding_tasks ot ON eos.task_id = ot.id
           JOIN users u ON eos.employee_user_id = u.id
           WHERE eos.id = ?""", (status_id,)
    ).fetchone()

    if not task_status_info:
        flash("Task instance not found.", "error")
        return redirect(request.referrer or url_for('onboarding.index'))

    can_upload = False
    # ... (authorization logic remains the same) ...
    if g.user['role'] == 'admin': can_upload = True
    elif task_status_info['employee_user_id'] == g.user['id']: can_upload = True
    elif g.user['role'] == 'manager' and task_status_info['employee_manager_id'] == g.user['id']: can_upload = True
    elif task_status_info['responsible_role'].lower() == g.user['role'] and g.user['role'] in ['hr', 'it']: can_upload = True


    if not can_upload:
        flash("You do not have permission to upload attachments for this task instance.", "error")
        redirect_url = request.referrer or url_for('onboarding.index')
        return redirect(redirect_url)

    if 'user_task_attachment' not in request.files:
        flash('No file part in request.', 'warning')
        return redirect(request.referrer or url_for('onboarding.index'))

    file = request.files['user_task_attachment']
    if file.filename == '':
        flash('No selected file.', 'warning')
        return redirect(request.referrer or url_for('onboarding.index'))

    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        stored_filename = f"user_{g.user['id']}_{datetime.now(pytz.utc).strftime('%Y%m%d%H%M%S%f')}_{original_filename}"
        try:
            db.execute('BEGIN')
            if not os.path.exists(current_app.config['UPLOAD_FOLDER']):
                os.makedirs(current_app.config['UPLOAD_FOLDER'])
            file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename))

            # uploaded_at is DEFAULT CURRENT_TIMESTAMP
            db.execute(
                """INSERT INTO onboarding_task_attachments
                   (employee_onboarding_status_id, uploader_user_id, file_name, stored_file_name, attachment_type)
                   VALUES (?, ?, ?, ?, ?)""",
                (status_id, g.user['id'], original_filename, stored_filename, 'user_submission')
            )
            db.commit()
            flash(f"File '{original_filename}' uploaded successfully for the task.", "success")
        except Exception as e:
            db.rollback()
            current_app.logger.error(f"Error saving user-submitted file or DB record: {e}")
            flash(f"An error occurred while uploading the file: {e}", "error")
            if os.path.exists(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename)):
                try: os.remove(os.path.join(current_app.config['UPLOAD_FOLDER'], stored_filename))
                except OSError: pass # Log this error in a real app
    else:
        flash("File type not allowed.", "error")

    redirect_url = request.referrer or url_for('onboarding.index')
    return redirect(redirect_url)


# --- Route to delete user-submitted attachment ---
# (delete_user_task_attachment does not display timestamps)
# ... (This route remains unchanged regarding timestamp logic) ...
@bp.route('/user-attachment/<int:attachment_id>/delete', methods=['POST'])
@login_required
def delete_user_task_attachment(attachment_id):
    db = get_db()
    attachment = db.execute(
        "SELECT * FROM onboarding_task_attachments WHERE id = ? AND attachment_type = 'user_submission'",
        (attachment_id,)
    ).fetchone()

    if not attachment:
        flash("User-submitted attachment not found or is not a user submission.", "error")
        return redirect(request.referrer or url_for('onboarding.index'))

    can_delete = False
    # ... (authorization logic remains the same) ...
    if g.user['role'] == 'admin':
        can_delete = True
    elif attachment['uploader_user_id'] == g.user['id']:
        can_delete = True
    elif g.user['role'] == 'manager' and attachment['employee_onboarding_status_id']:
        status_record = db.execute("SELECT employee_user_id FROM employee_onboarding_status WHERE id = ?",
                                   (attachment['employee_onboarding_status_id'],)).fetchone()
        if status_record:
            employee_of_task = db.execute("SELECT manager_id FROM users WHERE id = ?",
                                          (status_record['employee_user_id'],)).fetchone()
            if employee_of_task and employee_of_task['manager_id'] == g.user['id']:
                can_delete = True


    if not can_delete:
        flash("You do not have permission to delete this attachment.", "error")
        return redirect(request.referrer or url_for('onboarding.index'))

    try:
        db.execute('BEGIN')
        if attachment['stored_file_name']:
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], attachment['stored_file_name'])
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except OSError as e: current_app.logger.error(f"Error deleting file {file_path}: {e}")
        db.execute("DELETE FROM onboarding_task_attachments WHERE id = ?", (attachment_id,))
        db.commit()
        flash("Attachment deleted successfully.", "success")
    except sqlite3.Error as e:
        db.rollback()
        flash(f"Database error deleting attachment: {e}", "error")
    except Exception as e_gen:
        db.rollback()
        flash(f"An unexpected error occurred while deleting attachment: {e_gen}", "error")
        current_app.logger.error(f"Unexpected error in delete_user_task_attachment: {e_gen}")

    redirect_url = request.referrer
    # ... (redirect logic remains the same) ...
    if not redirect_url and attachment['employee_onboarding_status_id']:
        status_info = db.execute("SELECT employee_user_id FROM employee_onboarding_status WHERE id = ?", (attachment['employee_onboarding_status_id'],)).fetchone()
        if status_info:
            if status_info['employee_user_id'] != g.user['id'] and (g.user['role'] == 'admin' or g.user['role'] == 'manager'):
                 redirect_url = url_for('onboarding.my_onboarding_tasks', employee_id_override=status_info['employee_user_id'])
            else:
                 redirect_url = url_for('onboarding.my_onboarding_tasks')


    return redirect(redirect_url or url_for('onboarding.index'))


# --- Route to add a comment to a task instance ---
# (add_task_comment: created_at for comments is DEFAULT CURRENT_TIMESTAMP)
# ... (This route remains unchanged regarding timestamp logic) ...
@bp.route('/task-instance/<int:status_id>/add_comment', methods=['POST'])
@login_required
def add_task_comment(status_id):
    db = get_db()
    comment_text = request.form.get('comment_text', '').strip()

    task_instance = db.execute(
        """SELECT eos.id, eos.employee_user_id, ot.task_name, ot.responsible_role,
                  u_emp.full_name as employee_name, u_emp.manager_id as employee_manager_id
           FROM employee_onboarding_status eos
           JOIN onboarding_tasks ot ON eos.task_id = ot.id
           JOIN users u_emp ON eos.employee_user_id = u_emp.id
           WHERE eos.id = ?""", (status_id,)
    ).fetchone()

    if not task_instance:
        flash("Task instance not found.", "error")
        return redirect(request.referrer or url_for('onboarding.index'))

    if not comment_text:
        flash("Comment cannot be empty.", "error")
        redirect_url = request.referrer or url_for('onboarding.index')
        return redirect(redirect_url)

    can_view = False 
    # ... (authorization logic remains the same) ...
    if g.user['role'] == 'admin': can_view = True
    elif task_instance['employee_user_id'] == g.user['id']: can_view = True 
    elif g.user['role'] == 'manager' and task_instance['employee_manager_id'] == g.user['id']: can_view = True 
    elif task_instance['responsible_role'].lower() == g.user['role'] and g.user['role'] in ['hr', 'it']: can_view = True


    if not can_view: # Should be can_comment or similar, but logic is for viewing implies commenting here
        flash("You are not authorized to comment on this task.", "error")
        return redirect(request.referrer or url_for('onboarding.index'))

    try:
        db.execute('BEGIN')
        # created_at for comments is DEFAULT CURRENT_TIMESTAMP
        db.execute(
            "INSERT INTO onboarding_task_comments (employee_onboarding_status_id, user_id, comment_text) VALUES (?, ?, ?)",
            (status_id, g.user['id'], comment_text)
        )
        db.commit()
        flash("Comment added successfully.", "success")

        send_new_comment_notification(
            g.user['id'],
            status_id,
            comment_text,
            task_instance['task_name'],
            task_instance['employee_user_id'],
            task_instance['employee_name']
        )

    except sqlite3.Error as e:
        db.rollback()
        flash(f"Database error adding comment: {e}", "error")
        current_app.logger.error(f"DB error adding comment for status_id {status_id}: {e}")
    except Exception as e_gen:
        db.rollback()
        flash(f"An unexpected error occurred while adding comment: {e_gen}", "error")
        current_app.logger.error(f"Unexpected error in add_task_comment: {e_gen}")

    redirect_url = request.referrer or url_for('onboarding.index')
    return redirect(redirect_url)
