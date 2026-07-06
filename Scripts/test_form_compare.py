#!/usr/bin/env python3
"""Compare old FormParser vs new regex form extraction."""
import sys, pathlib, urllib.request, re, html
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sync_static_pgn import load_form as old_load_form

URL = "https://chess-results.com/PartieSuche.aspx?lan=1"

# Old parser
old_form = old_load_form(URL)
print("=== OLD parser ===")
print(f"action_url: {old_form['action_url']}")
print(f"base_url: {old_form['base_url']}")
print(f"fields count: {len(old_form['fields'])}")
for k in sorted(old_form['fields']):
    v = old_form['fields'][k]
    print(f"  {k} = {v[:80] if len(v) > 80 else v}")

# New regex parser
print("\n=== NEW regex parser ===")
req = urllib.request.Request(URL, headers={"User-Agent": "test"})
with urllib.request.urlopen(req, timeout=30) as resp:
    final = resp.geturl()
    html_text = resp.read().decode("utf-8", errors="replace")

m_action = re.search(r'<form[^>]*action="([^"]*)"', html_text, re.IGNORECASE)
raw_action = m_action.group(1) if m_action else ""
action_url = urllib.parse.urljoin(final, html.unescape(raw_action)) if raw_action else final
print(f"action_url: {action_url}")
print(f"base_url: {final}")

fields = {}
for m in re.finditer(r'<input[^>]*name="([^"]*)"[^>]*>', html_text, re.IGNORECASE):
    name = m.group(1)
    val_m = re.search(r'value="([^"]*)"', m.group(0), re.IGNORECASE)
    fields[name] = val_m.group(1) if val_m else ""

print(f"fields count: {len(fields)}")
for k in sorted(fields):
    v = fields[k]
    print(f"  {k} = {v[:80] if len(v) > 80 else v}")

# Compare
print("\n=== DIFF ===")
if old_form['action_url'] != action_url:
    print(f"ACTION URL DIFFERS!")
    print(f"  old: {old_form['action_url']}")
    print(f"  new: {action_url}")
else:
    print("Action URL: SAME")

old_keys = set(old_form['fields'].keys())
new_keys = set(fields.keys())
if old_keys != new_keys:
    print(f"FIELD KEYS DIFFER!")
    print(f"  only in old: {old_keys - new_keys}")
    print(f"  only in new: {new_keys - old_keys}")
else:
    print("Field keys: SAME")
    for k in old_keys:
        ov = old_form['fields'][k]
        nv = fields[k]
        if ov != nv:
            print(f"  VALUE DIFF for {k}:")
            print(f"    old: {ov[:80]}")
            print(f"    new: {nv[:80]}")
