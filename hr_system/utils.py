# hr_system/utils.py

from datetime import datetime
import pytz # Make sure you have installed pytz: pip install pytz
from flask import current_app, g 

def format_datetime_user_timezone(utc_dt_str, target_timezone_str=None, format_str='%b %d, %Y, %I:%M %p'):
    """
    Converts a UTC datetime string to a user-specified timezone and formats it.
    """
    current_app.logger.debug(f"[format_datetime_user_timezone] Called with: utc_dt_str='{utc_dt_str}', target_timezone_str='{target_timezone_str}'")

    if not utc_dt_str:
        current_app.logger.debug("[format_datetime_user_timezone] utc_dt_str is empty or None. Returning 'N/A'.")
        return "N/A"

    # Determine the target timezone
    resolved_target_timezone_str = target_timezone_str
    if resolved_target_timezone_str is None:
        if hasattr(g, 'user_timezone') and g.user_timezone:
            resolved_target_timezone_str = g.user_timezone
            current_app.logger.debug(f"[format_datetime_user_timezone] Using g.user_timezone: '{resolved_target_timezone_str}'")
        else:
            resolved_target_timezone_str = current_app.config.get('USER_DEFAULT_TIMEZONE', 'UTC')
            current_app.logger.debug(f"[format_datetime_user_timezone] Using app.config USER_DEFAULT_TIMEZONE or fallback 'UTC': '{resolved_target_timezone_str}'")
    else:
        current_app.logger.debug(f"[format_datetime_user_timezone] Using explicitly passed target_timezone_str: '{resolved_target_timezone_str}'")


    try:
        target_tz = pytz.timezone(resolved_target_timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        current_app.logger.error(f"[format_datetime_user_timezone] UnknownTimeZoneError: The timezone '{resolved_target_timezone_str}' is not recognized. Falling back to UTC.")
        target_tz = pytz.utc # Fallback to UTC
        resolved_target_timezone_str = 'UTC' # Update for logging consistency

    # Ensure utc_dt is a datetime object
    naive_dt = None
    if isinstance(utc_dt_str, str):
        try:
            naive_dt = datetime.strptime(utc_dt_str, '%Y-%m-%d %H:%M:%S')
            current_app.logger.debug(f"[format_datetime_user_timezone] Parsed string '{utc_dt_str}' using '%Y-%m-%d %H:%M:%S' to naive_dt: {naive_dt}")
        except ValueError:
            try:
                if utc_dt_str.endswith('Z'):
                    naive_dt = datetime.fromisoformat(utc_dt_str[:-1] + '+00:00')
                else:
                    naive_dt = datetime.fromisoformat(utc_dt_str)
                current_app.logger.debug(f"[format_datetime_user_timezone] Parsed string '{utc_dt_str}' using fromisoformat to naive_dt: {naive_dt}")
            except ValueError as e_parse:
                current_app.logger.error(f"[format_datetime_user_timezone] Could not parse UTC datetime string '{utc_dt_str}'. Error: {e_parse}. Returning original.")
                return utc_dt_str
    elif isinstance(utc_dt_str, datetime):
        naive_dt = utc_dt_str
        current_app.logger.debug(f"[format_datetime_user_timezone] Input utc_dt_str is already datetime object: {naive_dt}")
    else:
        current_app.logger.error(f"[format_datetime_user_timezone] Invalid type for utc_dt_str: {type(utc_dt_str)}. Returning string representation.")
        return str(utc_dt_str)

    # Localize or convert to UTC
    utc_dt = None
    if naive_dt.tzinfo is None or naive_dt.tzinfo.utcoffset(naive_dt) is None:
        utc_dt = pytz.utc.localize(naive_dt)
        current_app.logger.debug(f"[format_datetime_user_timezone] Localized naive_dt to UTC: {utc_dt}")
    else:
        utc_dt = naive_dt.astimezone(pytz.utc)
        current_app.logger.debug(f"[format_datetime_user_timezone] Converted timezone-aware naive_dt to UTC: {utc_dt}")

    # Convert to the target timezone and format
    try:
        local_dt = utc_dt.astimezone(target_tz)
        formatted_time = local_dt.strftime(format_str)
        current_app.logger.debug(f"[format_datetime_user_timezone] Converted to target_tz '{resolved_target_timezone_str}': {local_dt}. Formatted: '{formatted_time}'")
        return formatted_time
    except Exception as e_convert:
        current_app.logger.error(f"[format_datetime_user_timezone] Error converting/formatting datetime: {e_convert} (Input UTC: {utc_dt}, Target TZ: {resolved_target_timezone_str})")
        return utc_dt.strftime('%Y-%m-%d %H:%M:%S %Z') # Fallback to UTC display


def register_custom_filters(app):
    """Registers custom Jinja2 filters."""

    @app.template_filter('localdatetime')
    def localdatetime_filter(utc_dt_str, target_timezone_str=None, format_str='%b %d, %Y, %I:%M %p'):
        current_app.logger.debug(f"[localdatetime_filter] Called with utc_dt_str='{utc_dt_str}', target_timezone_str='{target_timezone_str}'")
        return format_datetime_user_timezone(utc_dt_str, target_timezone_str, format_str)

    app.logger.info("Registered custom Jinja filter: localdatetime")
