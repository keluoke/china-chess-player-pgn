#!/usr/bin/env python3
"""Diagnose Chess-Results form and empty-FIDE-ID download."""
import re, urllib.request, urllib.parse

FORM_URL = "https://chess-results.com/PartieSuche.aspx?lan=1"
USER_AGENT = "ChinaChessPlayerPGNEventFetch/2.0"

# 1. Load form
req = urllib.request.Request(FORM_URL, headers={"User-Agent": USER_AGENT})
resp = urllib.request.urlopen(req, timeout=30)
html = resp.read().decode("utf-8", errors="replace")
print(f"Form loaded: {len(html)} chars")
print(f"Final URL: {resp.geturl()}")

# Extract form action
m_action = re.search(r'<form[^>]*action="([^"]*)"', html, re.IGNORECASE)
action_url = urllib.parse.urljoin(resp.geturl(), m_action.group(1)) if m_action else resp.geturl()
print(f"Form action: {action_url}")

# Extract key hidden fields
for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
    m = re.search(rf'name="{re.escape(name)}"[^>]*value="([^"]*)"', html, re.IGNORECASE)
    if m:
        print(f"  {name}: {m.group(1)[:60]}...")
    else:
        print(f"  {name}: NOT FOUND")

# Extract ALL input fields
fields = {}
for m in re.finditer(r'<input[^>]*name="([^"]*)"[^>]*>', html, re.IGNORECASE):
    name = m.group(1)
    vm = re.search(r'value="([^"]*)"', m.group(0), re.IGNORECASE)
    fields[name] = vm.group(1) if vm else ""

print(f"\nTotal input fields: {len(fields)}")
print("Key fields:")
for k in sorted(fields):
    if any(x in k for x in ("Fide", "dbkey", "anzahl", "PGN", "VIEWSTATE", "EVENTVALIDATION")):
        print(f"  {k} = {fields[k][:80] if len(fields[k]) > 80 else fields[k]}")

# 2. Try downloading with empty FIDE ID for a known working tournament
test_tid = "1213354"  # 2025 Hangzhou - known to have PGN
print(f"\n--- Test download: tnr{test_tid} (empty FIDE ID) ---")

test_fields = dict(fields)
test_fields["ctl00$P1$Txt_FideID"] = ""
test_fields["ctl00$P1$txt_dbkey"] = test_tid
test_fields["ctl00$P1$combo_anzahl_zeilen"] = "999"
test_fields["ctl00$P1$cb_DownLoadPGN"] = "Download as PGN-File"

body = urllib.parse.urlencode(test_fields).encode("utf-8")
req2 = urllib.request.Request(
    action_url,
    data=body,
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
        "Referer": resp.geturl(),
    },
    method="POST",
)
try:
    resp2 = urllib.request.urlopen(req2, timeout=60)
    data = resp2.read()
    text = data.decode("utf-8", errors="replace")
    print(f"Status: {resp2.getcode()}")
    print(f"Response length: {len(text)}")
    print(f"First 200 chars: {text[:200]!r}")
    is_pgn = text.strip().startswith("[")
    print(f"Looks like PGN: {is_pgn}")
    if is_pgn:
        games = len(re.findall(r'^\[Event ', text, re.MULTILINE))
        print(f"Games found: {games}")
except urllib.error.HTTPError as e:
    print(f"HTTP ERROR: {e.code}")
    print(f"Response: {e.read()[:500]!r}")
except Exception as e:
    print(f"ERROR: {e}")

# 3. Try with a non-empty FIDE ID from a known player
print(f"\n--- Test download: tnr{test_tid} (with FIDE ID 8602980) ---")
test_fields2 = dict(fields)
test_fields2["ctl00$P1$Txt_FideID"] = "8602980"
test_fields2["ctl00$P1$txt_dbkey"] = test_tid
test_fields2["ctl00$P1$combo_anzahl_zeilen"] = "999"
test_fields2["ctl00$P1$cb_DownLoadPGN"] = "Download as PGN-File"

body2 = urllib.parse.urlencode(test_fields2).encode("utf-8")
req3 = urllib.request.Request(
    action_url,
    data=body2,
    headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
        "Referer": resp.geturl(),
    },
    method="POST",
)
try:
    resp3 = urllib.request.urlopen(req3, timeout=60)
    data3 = resp3.read()
    text3 = data3.decode("utf-8", errors="replace")
    print(f"Status: {resp3.getcode()}")
    print(f"Response length: {len(text3)}")
    print(f"First 200 chars: {text3[:200]!r}")
    is_pgn3 = text3.strip().startswith("[")
    print(f"Looks like PGN: {is_pgn3}")
    if is_pgn3:
        games3 = len(re.findall(r'^\[Event ', text3, re.MULTILINE))
        print(f"Games found: {games3}")
except urllib.error.HTTPError as e:
    print(f"HTTP ERROR: {e.code}")
    print(f"Response: {e.read()[:500]!r}")
except Exception as e:
    print(f"ERROR: {e}")
