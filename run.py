"""GigLedger - Entry Point"""
import os

from gigledger.app import create_app
from gigledger.models import db

app = create_app()


# Close all SQLAlchemy sessions after each request to prevent memory leaks
@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3030))
    from werkzeug.serving import run_simple
    print(f"GigLedger starting on port {port}...")
    run_simple('0.0.0.0', port, app, use_reloader=False, use_debugger=False, threaded=True)
