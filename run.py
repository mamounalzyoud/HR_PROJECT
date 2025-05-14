# run.py
# This script serves as the entry point to run the Flask application.

from hr_system import create_app # NO space before 'from'

# Create an instance of the Flask application using the factory function
app = create_app()

if __name__ == '__main__':
    # Run the Flask development server
    # debug=True enables auto-reloading and debugger (use False in production)
    # host='0.0.0.0' makes the server accessible on your network (use '127.0.0.1' for local only)
    # port=5001 uses a specific port
    app.run(debug=True, host='0.0.0.0', port=5001) # Indented correctly inside 'if'
