#!/usr/bin/env python3
"""
Build a self-contained dashboard HTML file with embedded data.
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
TEMPLATE = Path(__file__).parent / "dashboard.html"
OUTPUT = Path(__file__).parent / "dist"


def build():
    OUTPUT.mkdir(exist_ok=True)

    # Load bill data
    data_file = DATA_DIR / "bills_119.json"
    if not data_file.exists():
        print("Error: Run collect_data.py first to generate data/bills_119.json")
        return

    with open(data_file) as f:
        data = json.load(f)

    print(f"Embedding {len(data['bills'])} bills into dashboard...")

    # Read template
    with open(TEMPLATE) as f:
        html = f.read()

    # Embed data as a JS variable before the main script
    data_script = f"<script>const EMBEDDED_DATA = {json.dumps(data)};</script>"
    html = html.replace("</head>", f"{data_script}\n</head>")

    # Write output
    output_file = OUTPUT / "bill-delay-tracker.html"
    with open(output_file, 'w') as f:
        f.write(html)

    size_kb = output_file.stat().st_size / 1024
    print(f"Built self-contained dashboard: {output_file} ({size_kb:.0f} KB)")


if __name__ == "__main__":
    build()
