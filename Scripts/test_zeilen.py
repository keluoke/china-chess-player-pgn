#!/usr/bin/env python3
"""Test if combo_anzahl_zeilen value causes 500."""
import sys, pathlib, urllib.request, urllib.parse, html.parser
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sync_static_pgn import load_form

FORM_URL = "https://chess-results.com/PartieSuche.aspx?lan=1"
USER_AGENT = "test"

def try_download(tid, fide_id, zeilen):
    form = load_form(FORM_URL)
    fields = dict(form["fields"])
    fields["ctl00$P1$Txt_FideID"] = fide_id
    fields["ctl00$P1$txt_dbkey"] = tid
    fields["ctl00$P1$combo_anzahl_zeilen"] = zeilen
    fields["ctl00$P1$cb_DownLoadPGN"] = "Download as PGN-File"
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        form["action_url"],
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
            "Referer": form["base_url"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        games = data.count("[Event ")
        return f"OK, {games} games"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except Exception as e:
        return f"Error: {e}"

print(f"Testing tnr1213354 with empty FIDE ID:")
for z in ["5", "10", "25", "50", "100", "200", "500", "999", "9999"]:
    result = try_download("1213354", "", z)
    print(f"  zeilen={z}: {result}")

print(f"\nTesting tnr1213354 with FIDE ID 8602980:")
for z in ["5", "999"]:
    result = try_download("1213354", "8602980", z)
    print(f"  zeilen={z}: {result}")
