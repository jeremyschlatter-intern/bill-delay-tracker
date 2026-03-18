#!/usr/bin/env python3
"""
Bill Delay Tracker - Web Server

Serves the dashboard and provides the bill data as JSON.
"""

import json
import os
from pathlib import Path
from flask import Flask, send_file, jsonify, send_from_directory

app = Flask(__name__)
DATA_DIR = Path(__file__).parent / "data"
STATIC_DIR = Path(__file__).parent


@app.route("/")
def index():
    return send_file(STATIC_DIR / "dashboard.html")


@app.route("/data/<path:filename>")
def serve_data(filename):
    return send_from_directory(DATA_DIR, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8752))
    print(f"Bill Delay Tracker running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
