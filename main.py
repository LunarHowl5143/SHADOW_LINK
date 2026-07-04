"""
main.py
-------
Entry point for the crime_api_handler Advanced I/O function.
Catalyst's Advanced I/O runtime looks for a WSGI-compatible `app` object
in this file (the exact expected filename/variable can vary slightly by
CLI-generated boilerplate - check your project's existing entry file
before overwriting it; merge these registrations into it instead if one
already exists).

If your project already has a main.py / app.py with other routers
(e.g. from Operators 1-3), do NOT replace it wholesale — just add these
two import + register_blueprint lines to the existing file.
"""

from flask import Flask, jsonify

from routers.agentic_routes import agentic_bp
from routers.trend_routes import trend_bp

app = Flask(__name__)

app.register_blueprint(agentic_bp)
app.register_blueprint(trend_bp)


@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "crime_api_handler",
        "status": "running",
        "routers": ["agentic_routes", "trend_routes"],
    })


if __name__ == "__main__":
    # Local testing only - Catalyst's own runtime invokes `app` directly
    # when deployed, it does not use this __main__ block.
    app.run(host="0.0.0.0", port=9000, debug=True)
