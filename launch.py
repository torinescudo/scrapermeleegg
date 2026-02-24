#!/usr/bin/env python3
"""Kill old server, regenerate site, relaunch server."""
import subprocess, os, sys, time, importlib

os.chdir('/workspaces/scrapermeleegg')
sys.path.insert(0, '/workspaces/scrapermeleegg')

# Kill any existing process on port 8080
subprocess.run('kill $(lsof -t -i:8080) 2>/dev/null', shell=True)
time.sleep(1)

# Regenerate the site
import generate_site
importlib.reload(generate_site)
import manage_tournaments as mt
importlib.reload(mt)

db = mt.TournamentDB()
dist_path = '/workspaces/scrapermeleegg/dist'
if db.tournaments:
    generate_site.generate_multi_tournament_site(db.data["tournaments"], dist_path)
    size = os.path.getsize(os.path.join(dist_path, 'index.html'))
    print(f"Site generated: {size} bytes")
else:
    print("No tournaments in database!")

# Check HTML
with open(os.path.join(dist_path, 'index.html'), 'r') as f:
    html = f.read()
for term in ['home-panel', 'add-box', 'Scrapear', 'btn-do-add']:
    print(f"  {term}: {html.count(term)}")

# Launch server
server = subprocess.Popen(
    [sys.executable, 'server.py', '--port', '8080'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    cwd='/workspaces/scrapermeleegg'
)
time.sleep(2)
print(f"\nServer PID: {server.pid}")

# Verify
r = subprocess.run('curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/', shell=True, capture_output=True, text=True)
print(f"Response: {r.stdout}")
print("Done! Server at http://localhost:8080")
