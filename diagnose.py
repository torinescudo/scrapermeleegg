#!/usr/bin/env python3
import subprocess, os

# 1. Check if dist/index.html exists and contains the new elements
dist_file = '/workspaces/scrapermeleegg/dist/index.html'
if os.path.exists(dist_file):
    with open(dist_file, 'r') as f:
        content = f.read()
    print(f"dist/index.html size: {len(content)} bytes")
    for term in ['home-panel', 'add-box', 'Scrapear', 'btn-do-add', 'active-bar', 'Torneos', 'switchPanel']:
        count = content.count(term)
        print(f"  '{term}': {count} occurrences")
else:
    print("dist/index.html DOES NOT EXIST!")

# 2. Check if server is running on port 8080
r = subprocess.run('lsof -i:8080 2>/dev/null || echo "NOTHING on port 8080"', shell=True, capture_output=True, text=True)
print(f"\nPort 8080 status:\n{r.stdout}")

# 3. Check server.py exists
print(f"server.py exists: {os.path.exists('/workspaces/scrapermeleegg/server.py')}")
