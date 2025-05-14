# hr_system/db.py

import sqlite3
import click
from flask import current_app, g
from werkzeug.security import generate_password_hash
from datetime import datetime
import os # Added for UPLOAD_FOLDER path

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def add_column_if_not_exists(db, table_name, column_name, column_type):
    """Helper function to add a column to a table if it doesn't already exist."""
    cursor = db.execute(f"PRAGMA table_info({table_name})")
    columns = [row['name'] for row in cursor.fetchall()]
    if column_name not in columns:
        try:
            # Simplified default clause handling for this example
            # More complex default logic might be needed based on column_type
            default_clause = ""
            if "DEFAULT" not in column_type.upper() and "PRIMARY KEY" not in column_type.upper():
                if "TEXT" in column_type.upper() and "NOT NULL" in column_type.upper():
                     default_clause = " DEFAULT ''" # Default for NOT NULL TEXT
                elif "INTEGER" in column_type.upper() and "NOT NULL" in column_type.upper():
                     default_clause = " DEFAULT 0" # Default for NOT NULL INTEGER
                elif "REAL" in column_type.upper() and "NOT NULL" in column_type.upper():
                     default_clause = " DEFAULT 0.0" # Default for NOT NULL REAL
                # Add more specific defaults if needed, or make columns nullable

            db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}{default_clause}")
            current_app.logger.info(f"Added column {column_name} to {table_name}")
        except sqlite3.OperationalError as e:
            current_app.logger.warning(f"Warning: Could not add column {column_name} to {table_name}: {e}")


def init_db_command_logic():
    """Initialize the database schema and add sample data."""
    db = get_db()
    current_app.logger.info("Initializing database (preserving UI-created onboarding checklists/tasks)...")

    current_app.logger.info("Dropping tables for a clean reset of assignments, attachments, comments, and notifications...")
    db.execute("DROP TABLE IF EXISTS onboarding_task_comments;")
    db.execute("DROP TABLE IF EXISTS app_notifications;")
    db.execute("DROP TABLE IF EXISTS onboarding_task_attachments;")
    db.execute("DROP TABLE IF EXISTS employee_onboarding_status;")
    db.execute("DROP TABLE IF EXISTS salary_components;")
    current_app.logger.info("Finished dropping resettable tables.")


    # --- Users Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        full_name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('employee', 'manager', 'admin', 'hr', 'it')),
        department TEXT,
        hire_date TEXT,
        phone_number TEXT,
        address TEXT,
        emergency_contact_name TEXT,
        emergency_contact_phone TEXT,
        manager_id INTEGER,
        annual_leave_entitlement REAL DEFAULT 20.0,
        timezone TEXT, -- NEW COLUMN for user's preferred timezone
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (manager_id) REFERENCES users (id) ON DELETE SET NULL
    )''')
    add_column_if_not_exists(db, 'users', 'manager_id', 'INTEGER REFERENCES users(id) ON DELETE SET NULL')
    add_column_if_not_exists(db, 'users', 'annual_leave_entitlement', 'REAL DEFAULT 20.0')
    add_column_if_not_exists(db, 'users', 'timezone', 'TEXT') # Ensure timezone column exists
    current_app.logger.info("Users table ensured with timezone column.")

    # ... (rest of your table creation logic remains the same) ...
    # --- Time Clock Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS time_clock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        clock_in TIMESTAMP NOT NULL,
        clock_out TIMESTAMP,
        status TEXT DEFAULT 'active' CHECK(status IN ('active', 'completed')),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("Time clock table ensured.")

    # --- Attendance Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        date TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('present', 'absent', 'leave')),
        hours_worked REAL,
        UNIQUE(user_id, date),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("Attendance table ensured.")

    # --- Leaves Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS leaves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        leave_type TEXT NOT NULL,
        start_date TEXT NOT NULL,
        end_date TEXT NOT NULL,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'cancelled')),
        reason TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        actioned_by_user_id INTEGER,
        actioned_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (actioned_by_user_id) REFERENCES users(id) ON DELETE SET NULL
    )''')
    current_app.logger.info("Leaves table ensured.")

    # --- Benefits Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS benefits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        benefit_type TEXT NOT NULL,
        details TEXT,
        start_date TEXT,
        end_date TEXT,
        status TEXT DEFAULT 'active' CHECK(status IN ('active', 'inactive')),
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("Benefits table ensured.")

    # --- Announcements Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("Announcements table ensured.")

    # --- Employee Salaries Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS employee_salaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        basic_salary REAL NOT NULL,
        pay_frequency TEXT NOT NULL DEFAULT 'Monthly' CHECK(pay_frequency IN ('Monthly', 'Annually', 'Weekly', 'Bi-Weekly', 'Hourly')),
        effective_date TEXT NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("Employee salaries table ensured.")

    # --- Salary Components Table (Create IF NOT EXISTS, as it was dropped) ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS salary_components (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        component_type TEXT NOT NULL CHECK(component_type IN ('allowance', 'deduction')),
        component_name TEXT NOT NULL,
        calculation_type TEXT NOT NULL DEFAULT 'fixed' CHECK(calculation_type IN ('fixed', 'percentage')),
        amount REAL DEFAULT NULL,
        percentage_rate REAL DEFAULT NULL,
        calculation_basis TEXT DEFAULT NULL CHECK(calculation_basis IN ('basic_salary', 'gross_pay')),
        upper_limit REAL DEFAULT NULL,
        frequency TEXT NOT NULL DEFAULT 'Monthly' CHECK(frequency IN ('Monthly', 'Annually', 'One-Time')),
        notes TEXT,
        employer_contribution_percent REAL DEFAULT NULL,
        employer_contribution_fixed REAL DEFAULT NULL,
        is_statutory INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("Salary components table created/recreated.")

    # --- Payroll Runs Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS payroll_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pay_period_year INTEGER NOT NULL,
        pay_period_month INTEGER NOT NULL,
        run_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_by_user_id INTEGER,
        status TEXT DEFAULT 'Completed' CHECK(status IN ('Processing', 'Completed', 'Completed with warnings', 'Failed')),
        notes TEXT,
        UNIQUE(pay_period_year, pay_period_month),
        FOREIGN KEY (processed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
    )''')
    current_app.logger.info("Payroll runs table ensured.")

    # --- Payslips Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS payslips (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payroll_run_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        basic_salary_used REAL NOT NULL,
        pay_frequency_used TEXT NOT NULL,
        gross_pay REAL NOT NULL,
        total_allowances REAL NOT NULL DEFAULT 0,
        total_deductions REAL NOT NULL DEFAULT 0,
        net_pay REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (payroll_run_id) REFERENCES payroll_runs (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        UNIQUE(payroll_run_id, user_id)
    )''')
    current_app.logger.info("Payslips table ensured.")

    # --- Payslip Components Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS payslip_components (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        payslip_id INTEGER NOT NULL,
        component_type TEXT NOT NULL CHECK(component_type IN ('allowance', 'deduction')),
        component_name TEXT NOT NULL,
        amount REAL NOT NULL,
        FOREIGN KEY (payslip_id) REFERENCES payslips (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("Payslip components table ensured.")

    # --- Performance Reviews Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS performance_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_user_id INTEGER NOT NULL,
        manager_user_id INTEGER NOT NULL,
        review_period_start TEXT NOT NULL,
        review_period_end TEXT NOT NULL,
        review_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        overall_rating INTEGER CHECK(overall_rating BETWEEN 1 AND 5),
        manager_comments TEXT,
        employee_comments TEXT,
        status TEXT DEFAULT 'Completed' CHECK(status IN ('Draft', 'Completed', 'Acknowledged')),
        FOREIGN KEY (employee_user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (manager_user_id) REFERENCES users (id) ON DELETE SET NULL
    )''')
    current_app.logger.info("Performance reviews table ensured.")

    # --- Expenses Table ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS expenses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        expense_date TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        description TEXT,
        receipt_filename TEXT,
        status TEXT DEFAULT 'Pending' CHECK(status IN ('Pending', 'Approved', 'Rejected')),
        submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        approved_by_user_id INTEGER,
        approved_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
    )''')
    current_app.logger.info("Expenses table ensured.")

    # --- Onboarding Checklists Table (Create IF NOT EXISTS - Preserves UI data) ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS onboarding_checklists (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        description TEXT,
        is_default INTEGER DEFAULT 0
    )''')
    current_app.logger.info("Onboarding checklists table ensured (UI data preserved).")

    # --- Onboarding Tasks Table (Create IF NOT EXISTS - Preserves UI data) ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS onboarding_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        checklist_id INTEGER NOT NULL,
        task_name TEXT NOT NULL,
        description TEXT,
        responsible_role TEXT CHECK(responsible_role IN ('Employee', 'Manager', 'HR', 'IT')),
        due_days_after_start INTEGER,
        display_order INTEGER DEFAULT 0,
        depends_on_task_id INTEGER DEFAULT NULL,
        FOREIGN KEY (checklist_id) REFERENCES onboarding_checklists (id) ON DELETE CASCADE,
        FOREIGN KEY (depends_on_task_id) REFERENCES onboarding_tasks (id) ON DELETE SET NULL
    )''')
    current_app.logger.info("Onboarding tasks table ensured (UI data preserved).")

    # --- Employee Onboarding Status Table (Recreated for clean assignments for sample user) ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS employee_onboarding_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_user_id INTEGER NOT NULL,
        task_id INTEGER NOT NULL,
        status TEXT DEFAULT 'Pending' CHECK(status IN ('Pending', 'Completed', 'N/A')),
        completed_date TEXT,
        notes TEXT,
        last_reminder_sent_at TIMESTAMP DEFAULT NULL, -- NEW COLUMN for debouncing
        UNIQUE(employee_user_id, task_id),
        FOREIGN KEY (employee_user_id) REFERENCES users (id) ON DELETE CASCADE,
        FOREIGN KEY (task_id) REFERENCES onboarding_tasks (id) ON DELETE CASCADE
    )''')
    add_column_if_not_exists(db, 'employee_onboarding_status', 'last_reminder_sent_at', 'TIMESTAMP DEFAULT NULL')
    current_app.logger.info("Employee onboarding status table created/recreated with notes and last_reminder_sent_at.")

    # --- Onboarding Task Attachments Table (Recreated for clean state) ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS onboarding_task_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        onboarding_task_id INTEGER,
        employee_onboarding_status_id INTEGER,
        uploader_user_id INTEGER,
        file_name TEXT NOT NULL,
        stored_file_name TEXT,
        attachment_type TEXT NOT NULL CHECK(attachment_type IN ('template_file', 'template_link', 'user_submission')),
        url TEXT,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (onboarding_task_id) REFERENCES onboarding_tasks (id) ON DELETE CASCADE,
        FOREIGN KEY (employee_onboarding_status_id) REFERENCES employee_onboarding_status (id) ON DELETE CASCADE,
        FOREIGN KEY (uploader_user_id) REFERENCES users (id) ON DELETE SET NULL,
        CONSTRAINT chk_attachment_context CHECK (
            (attachment_type IN ('template_file', 'template_link') AND onboarding_task_id IS NOT NULL AND employee_onboarding_status_id IS NULL) OR
            (attachment_type = 'user_submission' AND employee_onboarding_status_id IS NOT NULL AND onboarding_task_id IS NULL)
        ),
        CONSTRAINT chk_attachment_storage CHECK (
            (attachment_type IN ('template_file', 'user_submission') AND stored_file_name IS NOT NULL) OR
            (attachment_type = 'template_link' AND url IS NOT NULL)
        )
    )''')
    current_app.logger.info("Onboarding task attachments table created/recreated.")

    # --- App Notifications Table (Recreated for clean state) ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS app_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        link_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_read INTEGER DEFAULT 0,
        related_entity_type TEXT,
        related_entity_id INTEGER,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("App notifications table created/recreated.")

    # --- Onboarding Task Comments Table (Recreated for clean state) ---
    db.execute('''
    CREATE TABLE IF NOT EXISTS onboarding_task_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        employee_onboarding_status_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        comment_text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (employee_onboarding_status_id) REFERENCES employee_onboarding_status (id) ON DELETE CASCADE,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )''')
    current_app.logger.info("Onboarding task comments table created/recreated.")


    # --- Add Default/Sample Data ---
    current_app.logger.info("Adding/Updating default/sample data...")
    default_user_timezone = current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC') # Get default from app config

    admin_exists = db.execute('SELECT id FROM users WHERE username = ?', ('admin',)).fetchone()
    if not admin_exists:
        db.execute(
            'INSERT INTO users (username, password, full_name, email, role, annual_leave_entitlement, timezone) VALUES (?, ?, ?, ?, ?, ?, ?)',
            ('admin', generate_password_hash('admin123'), 'Admin User', 'admin@example.com', 'admin', 25.0, default_user_timezone)
        )
        current_app.logger.info('Created default admin user.')

    manager_username = 'mjones'
    manager_exists = db.execute('SELECT id, manager_id FROM users WHERE username = ?', (manager_username,)).fetchone()
    manager_id = None
    if not manager_exists:
        cursor = db.execute(
            'INSERT INTO users (username, password, full_name, email, role, department, hire_date, annual_leave_entitlement, timezone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (manager_username, generate_password_hash('password123'), 'Mary Jones', 'mary@example.com', 'manager', 'Marketing', '2022-06-10', 22.0, default_user_timezone)
        )
        manager_id = cursor.lastrowid
        db.execute(
            'INSERT INTO employee_salaries (user_id, basic_salary, pay_frequency, effective_date) VALUES (?, ?, ?, ?)',
            (manager_id, 75000, 'Annually', '2022-06-10')
        )
        current_app.logger.info(f'Created sample manager: {manager_username}')
    else:
        manager_id = manager_exists['id']

    dlee_user_id = None
    jsmith_user_id = None

    sample_employees_data = [
        ('jsmith', 'password123', 'John Smith', 'john@example.com', 'employee', 'Engineering', '2023-01-15', 20.0, 60000, manager_id, 'America/New_York'),
        ('dlee', 'password123', 'David Lee', 'david@example.com', 'employee', 'Finance', '2023-03-22', 20.0, 55000, manager_id, 'Asia/Amman'), # Example for David Lee
        ('schan', 'password123', 'Sarah Chan', 'sarah@example.com', 'employee', 'Marketing', '2023-05-01', 21.0, 62000, manager_id, default_user_timezone),
        ('hruser', 'password123', 'Holly Resource', 'holly@example.com', 'hr', 'HR', '2022-01-01', 20.0, 65000, None, default_user_timezone),
        ('itguy', 'password123', 'Ivan Tech', 'ivan@example.com', 'it', 'IT', '2022-02-01', 20.0, 68000, None, default_user_timezone),
    ]

    for emp_data in sample_employees_data:
        emp_exists = db.execute('SELECT id, manager_id FROM users WHERE username = ?', (emp_data[0],)).fetchone()
        emp_timezone = emp_data[10] if len(emp_data) > 10 and emp_data[10] else default_user_timezone

        if not emp_exists:
            emp_manager_id_to_assign = emp_data[9] if len(emp_data) > 9 and emp_data[9] is not None else None
            if emp_data[4] == 'employee' and emp_manager_id_to_assign is None:
                 emp_manager_id_to_assign = manager_id

            cursor = db.execute(
                'INSERT INTO users (username, password, full_name, email, role, department, hire_date, annual_leave_entitlement, manager_id, timezone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (emp_data[0], generate_password_hash(emp_data[1]), emp_data[2], emp_data[3], emp_data[4], emp_data[5], emp_data[6], emp_data[7], emp_manager_id_to_assign, emp_timezone)
            )
            emp_id = cursor.lastrowid
            if emp_data[0] == 'dlee': dlee_user_id = emp_id
            if emp_data[0] == 'jsmith': jsmith_user_id = emp_id
            db.execute(
                'INSERT INTO employee_salaries (user_id, basic_salary, pay_frequency, effective_date) VALUES (?, ?, ?, ?)',
                (emp_id, emp_data[8], 'Annually', emp_data[6])
            )
            current_app.logger.info(f'Created sample employee: {emp_data[0]} with timezone {emp_timezone}')
        else:
            if emp_data[0] == 'dlee':
                dlee_user_id = emp_exists['id']
                if dlee_user_id and manager_id and emp_exists['manager_id'] != manager_id:
                    db.execute("UPDATE users SET manager_id = ? WHERE id = ?", (manager_id, dlee_user_id))
                    current_app.logger.info(f"Ensured dlee (ID: {dlee_user_id}) reports to manager mjones (ID: {manager_id}).")
            elif emp_data[0] == 'jsmith':
                jsmith_user_id = emp_exists['id']


    # ... (rest of your init_db_command_logic for onboarding checklists and tasks) ...
    # --- Ensure "Standard Employee Onboarding" checklist exists or create it ---
    checklist_name_standard = 'Standard Employee Onboarding'
    default_checklist_row = db.execute("SELECT id, is_default FROM onboarding_checklists WHERE name = ?", (checklist_name_standard,)).fetchone()
    checklist_id_for_sample_tasks = None

    if not default_checklist_row:
        current_app.logger.info(f"'{checklist_name_standard}' not found, creating it as default...")
        db.execute("UPDATE onboarding_checklists SET is_default = 0 WHERE is_default = 1") # Unset other defaults
        default_checklist_cursor = db.execute("INSERT INTO onboarding_checklists (name, description, is_default) VALUES (?, ?, ?)",
                            (checklist_name_standard, 'Default checklist for all new standard employees.', 1))
        checklist_id_for_sample_tasks = default_checklist_cursor.lastrowid
        current_app.logger.info(f"Created '{checklist_name_standard}' with ID: {checklist_id_for_sample_tasks} and set as default.")

        tasks_data_with_deps = [
            ('Welcome Email Sent', 'HR sends official welcome email with first day info.', 'HR', -5, 10, None),
            ('HR Paperwork', 'Fill out W4, I9, and direct deposit forms.', 'Employee', 1, 20, 'Welcome Email Sent'),
            ('Company Policy Review', 'Read and acknowledge employee handbook.', 'Employee', 2, 30, 'HR Paperwork'),
            ('Setup Workstation', 'Ensure desk, computer, and phone are set up.', 'IT', 0, 40, None),
            ('System Access Granted', 'IT provides access to necessary systems.', 'IT', 1, 50, 'Setup Workstation'),
            ('Welcome Meeting with Manager', 'Initial meeting to discuss role and expectations.', 'Manager', 1, 60, 'HR Paperwork'),
            ('Benefits Enrollment Overview', 'Meet with HR to discuss benefit options.', 'HR', 5, 70, 'Company Policy Review'),
            ('Team Introduction', 'Manager introduces new hire to the team.', 'Manager', 2, 80, 'Welcome Meeting with Manager')
        ]
        task_name_to_id_map = {}
        for task_d_name, task_d_desc, task_d_role, task_d_due, task_d_order, _ in tasks_data_with_deps:
            cursor = db.execute(
                """INSERT INTO onboarding_tasks
                   (checklist_id, task_name, description, responsible_role, due_days_after_start, display_order, depends_on_task_id)
                   VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                (checklist_id_for_sample_tasks, task_d_name, task_d_desc, task_d_role, task_d_due, task_d_order)
            )
            task_id = cursor.lastrowid
            task_name_to_id_map[task_d_name] = task_id

        for task_d_name, _desc, _role, _due, _order, depends_on_name_val in tasks_data_with_deps:
            if depends_on_name_val:
                current_task_id = task_name_to_id_map.get(task_d_name)
                prerequisite_task_id = task_name_to_id_map.get(depends_on_name_val)
                if current_task_id and prerequisite_task_id:
                    db.execute("UPDATE onboarding_tasks SET depends_on_task_id = ? WHERE id = ?",
                               (prerequisite_task_id, current_task_id))
        current_app.logger.info(f"Added sample tasks with dependencies to newly created '{checklist_name_standard}'.")
    else:
        checklist_id_for_sample_tasks = default_checklist_row['id']
        current_app.logger.info(f"'{checklist_name_standard}' (ID: {checklist_id_for_sample_tasks}) already exists. Tasks will not be re-added to preserve admin changes.")
        if not default_checklist_row['is_default']:
            any_other_default = db.execute("SELECT id FROM onboarding_checklists WHERE is_default = 1 AND id != ?", (checklist_id_for_sample_tasks,)).fetchone()
            if not any_other_default:
                db.execute("UPDATE onboarding_checklists SET is_default = 1 WHERE id = ?", (checklist_id_for_sample_tasks,))
                current_app.logger.info(f"Set existing '{checklist_name_standard}' as default as no other default was found.")

    if dlee_user_id and checklist_id_for_sample_tasks:
        current_app.logger.info(f"Preparing to assign/re-assign tasks from checklist ID {checklist_id_for_sample_tasks} to David Lee (ID: {dlee_user_id}).")
        tasks_in_default_checklist_rows = db.execute(
            "SELECT id FROM onboarding_tasks WHERE checklist_id = ?",
            (checklist_id_for_sample_tasks,)
        ).fetchall()
        if tasks_in_default_checklist_rows:
            task_ids_from_default_checklist = [row['id'] for row in tasks_in_default_checklist_rows]
            placeholders = ','.join('?' for _ in task_ids_from_default_checklist)
            db.execute(
                f"DELETE FROM employee_onboarding_status WHERE employee_user_id = ? AND task_id IN ({placeholders})",
                [dlee_user_id] + task_ids_from_default_checklist
            )
            current_app.logger.info(f"Cleared prior task statuses for David Lee from checklist ID {checklist_id_for_sample_tasks}.")
            for task_id_to_assign in task_ids_from_default_checklist:
                try:
                    db.execute(
                        "INSERT INTO employee_onboarding_status (employee_user_id, task_id, status) VALUES (?, ?, ?)",
                        (dlee_user_id, task_id_to_assign, 'Pending')
                    )
                except sqlite3.IntegrityError:
                    current_app.logger.warning(f"Task {task_id_to_assign} somehow still marked as assigned to user {dlee_user_id} despite clear (IntegrityError). Skipping.")
                except Exception as e_assign:
                    current_app.logger.error(f"Error assigning task {task_id_to_assign} to user {dlee_user_id}: {e_assign}")
            current_app.logger.info(f"Finished assigning/re-assigning {len(task_ids_from_default_checklist)} tasks from checklist ID {checklist_id_for_sample_tasks} to David Lee (ID: {dlee_user_id}).")
        else:
            current_app.logger.warning(f"Checklist ID {checklist_id_for_sample_tasks} ('{checklist_name_standard}') has no tasks defined. No tasks assigned to David Lee from it.")
    else:
        current_app.logger.warning(f"Could not assign default checklist to David Lee. dlee_user_id: {dlee_user_id}, checklist_id_for_sample_tasks: {checklist_id_for_sample_tasks}.")


    db.commit()
    current_app.logger.info("Database initialization complete (UI-created checklists/tasks preserved, sample data reset/seeded).")

@click.command('init-db')
def init_db_command():
    """Ensure schema exists and seed/reset sample data, preserving UI-created checklists/tasks."""
    init_db_command_logic()
    click.echo('Initialized the database (UI-created checklists/tasks preserved, sample data reset/seeded).')

def init_app(app):
    """Register database functions with the Flask app."""
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
