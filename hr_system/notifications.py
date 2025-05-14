# hr_system/notifications.py

from flask import current_app, url_for, g
from flask_mail import Message # For real emails
from hr_system import mail # Import the mail instance
from hr_system.db import get_db
from datetime import datetime, date, timedelta
import sqlite3 # For specific exception handling if needed

# Helper function to get user details
def get_user_details(user_id):
    """Fetches user's full name, email, and manager_id by ID. Returns None if not found."""
    if not user_id: return None
    db = get_db()
    # Ensure manager_id is selected
    user = db.execute("SELECT id, full_name, email, manager_id, role FROM users WHERE id = ?", (user_id,)).fetchone()
    return user

# Helper function to send emails (using Flask-Mail)
def _send_email_notification(to_email, subject, body_text, body_html=None):
    """Sends an email notification using Flask-Mail."""
    if not to_email:
        current_app.logger.warning(f"Attempted to send email with subject '{subject}' but no recipient email was provided.")
        return
    if not mail.default_sender:
        current_app.logger.error("MAIL_DEFAULT_SENDER is not configured. Cannot send email.")
        return

    msg = Message(subject, recipients=[to_email], sender=mail.default_sender)
    msg.body = body_text
    if body_html:
        msg.html = body_html
    try:
        # mail.send(msg) # Uncomment when ready to send real emails
        # For now, we'll keep printing to console to avoid errors if email isn't configured
        print("\n--- SENDING MOCK EMAIL (Flask-Mail structure) ---")
        print(f"To: {to_email}")
        print(f"Subject: {subject}")
        print(f"Body:\n{body_text}")
        if body_html:
            print(f"HTML Body:\n{body_html}")
        print("-------------------------------------------------\n")
        current_app.logger.info(f"Flask-Mail: (Mock) Email Sent to {to_email} with subject: {subject}")

    except Exception as e:
        current_app.logger.error(f"Failed to send email to {to_email} with subject '{subject}': {e}")

# Helper function to create in-app notifications
def create_app_notification(user_id, message, link_url=None, related_entity_type=None, related_entity_id=None):
    """Creates an in-app notification for a user."""
    if not user_id or not message:
        current_app.logger.warning("Attempted to create in-app notification with missing user_id or message.")
        return
    db = get_db()
    try:
        db.execute(
            """INSERT INTO app_notifications (user_id, message, link_url, related_entity_type, related_entity_id)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, message, link_url, related_entity_type, related_entity_id)
        )
        db.commit()
        current_app.logger.info(f"In-app notification created for user {user_id}: '{message}' (Link: {link_url}, Entity: {related_entity_type}/{related_entity_id})")
    except sqlite3.Error as e:
        db.rollback()
        current_app.logger.error(f"Database error creating in-app notification for user {user_id}: {e}")
    except Exception as e_gen:
        db.rollback()
        current_app.logger.error(f"Unexpected error creating in-app notification for user {user_id}: {e_gen}")


# --- Onboarding Specific Notifications ---

def send_task_assignment_notification(employee_user_id, task_name, responsible_user_id_for_task, checklist_name, due_date_str=None, related_status_id=None):
    employee = get_user_details(employee_user_id)
    recipient_user = get_user_details(responsible_user_id_for_task)

    if not employee or not recipient_user:
        current_app.logger.error(f"Task Assignment Notification: User details not found for employee {employee_user_id} or recipient {responsible_user_id_for_task}")
        return

    subject = f"HR System: New Onboarding Task - {task_name}"
    body_text = f"Hello {recipient_user['full_name']},\n\n"
    link_url_path = None # For in-app notification
    email_link_url = None  # For email notification

    with current_app.app_context():
        if recipient_user['id'] == employee['id']: # Task assigned to the employee themselves
            body_text += f"A new onboarding task has been assigned to you:\n"
            link_url_path = url_for('onboarding.my_onboarding_tasks', _external=False)
            email_link_url = url_for('onboarding.my_onboarding_tasks', _external=True)
        else: # Task assigned to Manager, HR, IT regarding an employee
            body_text += f"An onboarding task requiring your action has been assigned for employee {employee['full_name']}:\n"
            link_url_path = url_for('onboarding.responsible_tasks_view', _external=False)
            email_link_url = url_for('onboarding.responsible_tasks_view', _external=True)


    body_text += f"- Task: {task_name}\n"
    body_text += f"- Checklist: {checklist_name}\n"
    if recipient_user['id'] != employee['id']:
        body_text += f"- Regarding Employee: {employee['full_name']}\n"
    if due_date_str and due_date_str != "N/A" and due_date_str != "Invalid Hire Date":
        body_text += f"- Due Date: {due_date_str}\n"

    body_text += f"\nPlease visit your onboarding dashboard: {email_link_url}\n\nThank you,\nHR System"
    _send_email_notification(recipient_user['email'], subject, body_text)

    in_app_message = f"New task '{task_name}'"
    if recipient_user['id'] != employee['id']:
        in_app_message += f" for {employee['full_name']}"
    if due_date_str and due_date_str != "N/A" and due_date_str != "Invalid Hire Date":
        in_app_message += f" (Due: {due_date_str})"
    in_app_message += "."
    create_app_notification(responsible_user_id_for_task, in_app_message, link_url_path, "onboarding_task_assignment", related_status_id)


def send_prerequisite_met_notification(employee_user_id, unlocked_task_name, responsible_user_id_for_task, checklist_name, due_date_str=None, completed_prerequisite_name=None, related_status_id=None):
    employee = get_user_details(employee_user_id)
    recipient_user = get_user_details(responsible_user_id_for_task)

    if not employee or not recipient_user:
        current_app.logger.error(f"Prerequisite Met Notification: User details not found for task '{unlocked_task_name}'.")
        return

    subject = f"HR System: Onboarding Task Unlocked - {unlocked_task_name}"
    body_text = f"Hello {recipient_user['full_name']},\n\n"
    link_url_path = None
    email_link_url = None

    if completed_prerequisite_name:
        body_text += f"The prerequisite task '{completed_prerequisite_name}' has been completed. "
    else:
        body_text += f"A prerequisite task has been completed. "

    body_text += f"The following onboarding task is now available for your action"
    with current_app.app_context():
        if recipient_user['id'] != employee['id']:
            body_text += f" for employee {employee['full_name']}"
            link_url_path = url_for('onboarding.responsible_tasks_view', _external=False)
            email_link_url = url_for('onboarding.responsible_tasks_view', _external=True)
        else:
            link_url_path = url_for('onboarding.my_onboarding_tasks', _external=False)
            email_link_url = url_for('onboarding.my_onboarding_tasks', _external=True)
    body_text += ":\n"

    body_text += f"- Task: {unlocked_task_name}\n"
    body_text += f"- Checklist: {checklist_name}\n"
    if recipient_user['id'] != employee['id']:
         body_text += f"- Regarding Employee: {employee['full_name']}\n"
    if due_date_str and due_date_str != "N/A" and due_date_str != "Invalid Hire Date":
        body_text += f"- Due Date: {due_date_str}\n"

    body_text += f"\nPlease visit your onboarding dashboard: {email_link_url}\n\nThank you,\nHR System"
    _send_email_notification(recipient_user['email'], subject, body_text)

    in_app_message = f"Task '{unlocked_task_name}' is now unlocked"
    if completed_prerequisite_name:
        in_app_message += f" (prereq: '{completed_prerequisite_name}' done)"
    if recipient_user['id'] != employee['id']:
        in_app_message += f" for {employee['full_name']}"
    in_app_message += "."
    create_app_notification(responsible_user_id_for_task, in_app_message, link_url_path, "onboarding_task_unlocked", related_status_id)


def send_task_completed_notification(employee_user_id, completed_task_name, completed_by_user_id, checklist_name, notes=None, related_status_id=None):
    employee = get_user_details(employee_user_id)
    completer = get_user_details(completed_by_user_id)

    if not employee or not completer:
        current_app.logger.error(f"Task Completion Notification: User details not found for task '{completed_task_name}'.")
        return

    manager_to_notify = None
    if employee and 'manager_id' in employee.keys() and employee['manager_id'] is not None:
        if not (completed_by_user_id == employee['manager_id']):
             manager_to_notify = get_user_details(employee['manager_id'])

    if manager_to_notify:
        subject = f"HR System: Onboarding Task Completed for {employee['full_name']}"
        body_text = f"Hello {manager_to_notify['full_name']},\n\n"
        body_text += f"This is to inform you that an onboarding task has been completed for your direct report, {employee['full_name']}:\n"
        body_text += f"- Task: {completed_task_name}\n"
        body_text += f"- Checklist: {checklist_name}\n"
        body_text += f"- Completed by: {completer['full_name']}\n"
        if notes and notes.strip():
            body_text += f"- Notes: {notes}\n"

        email_link_url = None
        link_url_path = None
        with current_app.app_context():
            # Link to the specific employee's task view
            email_link_url = url_for('onboarding.my_onboarding_tasks', employee_id_override=employee_user_id, _external=True)
            link_url_path = url_for('onboarding.my_onboarding_tasks', employee_id_override=employee_user_id, _external=False)
        body_text += f"\nYou can view their progress here: {email_link_url}\n\nThank you,\nHR System"

        _send_email_notification(manager_to_notify['email'], subject, body_text)

        in_app_message = f"Task '{completed_task_name}' for {employee['full_name']} completed by {completer['full_name']}."
        if notes and notes.strip():
            in_app_message += f" Notes: {notes[:30]}{'...' if len(notes)>30 else ''}"
        create_app_notification(manager_to_notify['id'], in_app_message, link_url_path, "onboarding_task_completed", related_status_id)
    else:
        current_app.logger.info(f"No manager to notify or manager completed the task: {completed_task_name} for employee {employee['full_name']}")


def send_task_due_soon_reminder_notification(employee_user_id, task_name, responsible_user_id_for_task, checklist_name, due_date_str, related_status_id=None): # ADDED related_status_id
    employee = get_user_details(employee_user_id)
    recipient_user = get_user_details(responsible_user_id_for_task)

    if not employee or not recipient_user:
        current_app.logger.error(f"Task Due Soon Reminder: User details missing for task '{task_name}'.")
        return

    subject = f"HR System REMINDER: Onboarding Task Due Soon - {task_name}"
    body_text = f"Hello {recipient_user['full_name']},\n\n"
    body_text += f"This is a friendly reminder that the following onboarding task is due soon"
    link_url_path = None
    email_link_url = None
    with current_app.app_context():
        if recipient_user['id'] != employee['id']:
            body_text += f" for employee {employee['full_name']}"
            link_url_path = url_for('onboarding.responsible_tasks_view', _external=False)
            email_link_url = url_for('onboarding.responsible_tasks_view', _external=True)
        else:
            link_url_path = url_for('onboarding.my_onboarding_tasks', _external=False)
            email_link_url = url_for('onboarding.my_onboarding_tasks', _external=True)
    body_text += ":\n"
    body_text += f"- Task: {task_name}\n"
    body_text += f"- Checklist: {checklist_name}\n"
    body_text += f"- Due Date: {due_date_str}\n"
    body_text += f"\nPlease visit your onboarding dashboard to complete it: {email_link_url}\n\nThank you,\nHR System"

    _send_email_notification(recipient_user['email'], subject, body_text)

    in_app_message = f"REMINDER: Task '{task_name}' is due soon ({due_date_str})"
    if recipient_user['id'] != employee['id']:
        in_app_message += f" for {employee['full_name']}"
    in_app_message += "."
    create_app_notification(responsible_user_id_for_task, in_app_message, link_url_path, "onboarding_task_due_soon", related_status_id)


def send_task_overdue_alert_notification(employee_user_id, task_name, responsible_user_id_for_task, checklist_name, due_date_str, days_overdue, related_status_id=None): # ADDED related_status_id
    employee = get_user_details(employee_user_id)
    recipient_user = get_user_details(responsible_user_id_for_task)

    if not employee or not recipient_user:
        current_app.logger.error(f"Task Overdue Alert: User details missing for task '{task_name}'.")
        return

    subject = f"HR System ALERT: Onboarding Task OVERDUE - {task_name}"
    body_text = f"Hello {recipient_user['full_name']},\n\n"
    link_url_path = None
    email_link_url = None
    body_text += f"This is an alert that the following onboarding task is now overdue by {days_overdue} day(s)"
    with current_app.app_context():
        if recipient_user['id'] != employee['id']:
            body_text += f" for employee {employee['full_name']}"
            link_url_path = url_for('onboarding.responsible_tasks_view', _external=False)
            email_link_url = url_for('onboarding.responsible_tasks_view', _external=True)
        else:
            link_url_path = url_for('onboarding.my_onboarding_tasks', _external=False)
            email_link_url = url_for('onboarding.my_onboarding_tasks', _external=True)
    body_text += ":\n"
    body_text += f"- Task: {task_name}\n"
    body_text += f"- Checklist: {checklist_name}\n"
    body_text += f"- Original Due Date: {due_date_str}\n"
    body_text += f"\nPlease visit your onboarding dashboard immediately to address this: {email_link_url}\n\nThank you,\nHR System"

    _send_email_notification(recipient_user['email'], subject, body_text)

    in_app_message = f"ALERT: Task '{task_name}' is OVERDUE by {days_overdue} day(s)"
    if recipient_user['id'] != employee['id']:
        in_app_message += f" for {employee['full_name']}"
    in_app_message += "."
    create_app_notification(responsible_user_id_for_task, in_app_message, link_url_path, "onboarding_task_overdue", related_status_id)

    # Notify manager if the employee is responsible and task is overdue
    # Use dictionary-style access for sqlite3.Row and check for key existence / None
    if recipient_user['id'] == employee['id'] and \
       employee['manager_id'] is not None and \
       employee['manager_id'] != recipient_user['id']: # Ensure manager is not the employee themselves
        manager = get_user_details(employee['manager_id'])
        if manager:
            manager_subject = f"HR System ALERT: Employee Task OVERDUE - {employee['full_name']} - {task_name}"
            manager_body = f"Hello {manager['full_name']},\n\n"
            manager_body += f"This is to inform you that an onboarding task for your direct report, {employee['full_name']}, is overdue:\n"
            manager_body += f"- Task: {task_name}\n"
            manager_body += f"- Checklist: {checklist_name}\n"
            manager_body += f"- Original Due Date: {due_date_str}\n"
            manager_body += f"- Days Overdue: {days_overdue}\n"
            manager_body += f"Please follow up with {employee['full_name']}.\n"

            emp_task_link_email = None
            emp_task_link_path = None
            with current_app.app_context():
                emp_task_link_email = url_for('onboarding.my_onboarding_tasks', employee_id_override=employee['id'], _external=True)
                emp_task_link_path = url_for('onboarding.my_onboarding_tasks', employee_id_override=employee['id'], _external=False)
            manager_body += f"View employee's tasks: {emp_task_link_email}\n\nThank you,\nHR System"
            _send_email_notification(manager['email'], manager_subject, manager_body)

            manager_in_app_message = f"ALERT: Task '{task_name}' for your report {employee['full_name']} is OVERDUE by {days_overdue} day(s)."
            create_app_notification(manager['id'], manager_in_app_message, emp_task_link_path, "onboarding_task_overdue_report", related_status_id)


def send_new_comment_notification(commenter_id, task_status_id, comment_text, task_name, commented_on_employee_id, commented_on_employee_name):
    db = get_db()
    commenter = get_user_details(commenter_id)
    if not commenter:
        current_app.logger.error(f"New Comment Notification: Commenter details not found for ID {commenter_id}")
        return

    # Fetch details about the task instance and the employee it belongs to
    task_instance_info = db.execute(
        """SELECT eos.employee_user_id, ot.responsible_role, u.manager_id as employee_manager_id
           FROM employee_onboarding_status eos
           JOIN onboarding_tasks ot ON eos.task_id = ot.id
           JOIN users u ON eos.employee_user_id = u.id
           WHERE eos.id = ?""",
        (task_status_id,)
    ).fetchone()

    if not task_instance_info:
        current_app.logger.error(f"New Comment Notification: Task instance info not found for status_id {task_status_id}")
        return

    # Determine who to notify
    recipients_ids = set()

    # 1. The employee whose task it is (if not the commenter)
    if commenter_id != task_instance_info['employee_user_id']:
        recipients_ids.add(task_instance_info['employee_user_id'])

    # 2. The manager of that employee (if exists and not the commenter)
    if task_instance_info['employee_manager_id'] and commenter_id != task_instance_info['employee_manager_id']:
        recipients_ids.add(task_instance_info['employee_manager_id'])

    # 3. The person/role responsible for the task (if not the commenter and not already covered)
    task_responsible_role = task_instance_info['responsible_role']
    responsible_party_id = None
    if task_responsible_role == 'Employee':
        responsible_party_id = task_instance_info['employee_user_id']
    elif task_responsible_role == 'Manager':
        responsible_party_id = task_instance_info['employee_manager_id']
    elif task_responsible_role == 'HR':
        hr_user = db.execute("SELECT id FROM users WHERE role = 'hr' LIMIT 1").fetchone()
        if hr_user: responsible_party_id = hr_user['id']
    elif task_responsible_role == 'IT':
        it_user = db.execute("SELECT id FROM users WHERE role = 'it' LIMIT 1").fetchone()
        if it_user: responsible_party_id = it_user['id']
    
    if responsible_party_id and commenter_id != responsible_party_id:
        recipients_ids.add(responsible_party_id)


    for recipient_id in recipients_ids:
        if recipient_id == commenter_id: continue # Should be redundant due to checks above, but safe

        recipient_user = get_user_details(recipient_id)
        if not recipient_user:
            current_app.logger.warning(f"New Comment Notification: Recipient user details not found for ID {recipient_id}")
            continue

        subject = f"HR System: New Comment on Onboarding Task - {task_name}"
        body_text = f"Hello {recipient_user['full_name']},\n\n"
        body_text += f"{commenter['full_name']} has added a comment to the onboarding task '{task_name}'"
        if recipient_id != commented_on_employee_id: # If notifying someone other than the employee the task is FOR
             body_text += f" for employee {commented_on_employee_name}"
        body_text += ".\n\n"
        body_text += f"Comment: {comment_text}\n\n"

        link_url_path = None
        email_link_url = None
        with current_app.app_context():
            # Link to the specific task instance view for the employee the task is FOR
            link_url_path = url_for('onboarding.my_onboarding_tasks', employee_id_override=commented_on_employee_id, _anchor=f"task_instance_{task_status_id}", _external=False)
            email_link_url = url_for('onboarding.my_onboarding_tasks', employee_id_override=commented_on_employee_id, _anchor=f"task_instance_{task_status_id}", _external=True)
        
        body_text += f"You can view the task and comments here: {email_link_url}\n\nThank you,\nHR System"
        _send_email_notification(recipient_user['email'], subject, body_text)

        in_app_message = f"{commenter['full_name']} commented on task '{task_name}'"
        if recipient_id != commented_on_employee_id:
            in_app_message += f" for {commented_on_employee_name}"
        in_app_message += f": \"{comment_text[:30]}{'...' if len(comment_text)>30 else ''}\""
        create_app_notification(recipient_id, in_app_message, link_url_path, "onboarding_task_comment", task_status_id)
