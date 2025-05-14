# hr_system/__init__.py

import os
from flask import Flask, g, session, current_app # Added current_app for consistency
from flask_mail import Mail

mail = Mail() # Initialize the Mail extension

def create_app(test_config=None):
    """Create and configure an instance of the Flask application."""
    app = Flask(__name__, instance_relative_config=True)

    default_upload_folder = os.path.join(app.instance_path, 'uploads', 'onboarding_attachments')

    app.config.from_mapping(
        SECRET_KEY=os.environ.get('SECRET_KEY', 'dev'),
        DATABASE=os.path.join(app.instance_path, 'hr_system.db'),
        UPLOAD_FOLDER=default_upload_folder,
        MAX_CONTENT_LENGTH=16 * 1024 * 1024, # 16MB

        # Flask-Mail Configuration
        MAIL_SERVER=os.environ.get('MAIL_SERVER', 'smtp.example.com'),
        MAIL_PORT=int(os.environ.get('MAIL_PORT', 587)),
        MAIL_USE_TLS=os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', '1', 't'],
        MAIL_USE_SSL=os.environ.get('MAIL_USE_SSL', 'false').lower() in ['true', '1', 't'],
        MAIL_USERNAME=os.environ.get('MAIL_USERNAME'),
        MAIL_PASSWORD=os.environ.get('MAIL_PASSWORD'),
        MAIL_DEFAULT_SENDER=os.environ.get('MAIL_DEFAULT_SENDER', '"HR System" <noreply@example.com>'),

        # Default timezone for users if not otherwise specified.
        # For Jordan, 'Asia/Amman' is appropriate.
        # This is used by auth.py if user has no preference and by utils.py as a fallback.
        USER_DEFAULT_TIMEZONE = os.environ.get('USER_DEFAULT_TIMEZONE', 'Asia/Amman')
    )

    if test_config is None:
        # Load the instance config, if it exists, when not testing
        app.config.from_pyfile('config.py', silent=True)
    else:
        # Load the test config if passed in
        app.config.update(test_config)

    # Initialize Flask extensions
    mail.init_app(app)

    # Ensure instance folder and upload folder exist
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        app.logger.info(f"Upload folder ensured at: {app.config['UPLOAD_FOLDER']}")
    except OSError as e:
        app.logger.error(f"Could not create instance or upload folder: {e}")
        # Depending on severity, you might want to raise the error or handle it
        pass

    # Initialize database
    from . import db
    db.init_app(app)

    # Import and register custom Jinja filters (like 'localdatetime')
    from . import utils # Assuming utils.py is in the same directory (hr_system)
    utils.register_custom_filters(app)

    # Register blueprints
    from . import auth
    app.register_blueprint(auth.bp)

    from . import main
    app.register_blueprint(main.bp)
    app.add_url_rule('/', endpoint='index') # main.index will be the default view for '/'

    from . import users
    app.register_blueprint(users.bp)

    from . import attendance
    app.register_blueprint(attendance.bp)

    from . import leaves
    app.register_blueprint(leaves.bp)

    from . import benefits
    app.register_blueprint(benefits.bp)

    from . import salaries
    app.register_blueprint(salaries.bp)

    from . import announcements
    app.register_blueprint(announcements.bp)

    from . import reports
    app.register_blueprint(reports.bp)

    from . import payroll
    app.register_blueprint(payroll.bp)

    from . import performance
    app.register_blueprint(performance.bp)

    from . import expenses
    app.register_blueprint(expenses.bp)

    from . import onboarding
    app.register_blueprint(onboarding.bp)
    
    from . import notifications_ui
    app.register_blueprint(notifications_ui.bp)

    app.logger.info("HR System application created and configured.")
    return app
