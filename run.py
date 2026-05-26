"""FreelanceCash - Entry Point"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from freelancecash.app import create_app

app = create_app()

# Close all SQLAlchemy sessions after each request to prevent memory leaks
@app.teardown_appcontext
def shutdown_session(exception=None):
    from freelancecash.models import db
    db.session.remove()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3030))
    from werkzeug.serving import run_simple
    print(f"FreelanceCash starting on port {port}...")
    run_simple('0.0.0.0', port, app, use_reloader=False, use_debugger=False, threaded=True)
