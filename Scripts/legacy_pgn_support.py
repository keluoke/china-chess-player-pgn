"""Migration library helpers. No collection or publication entrypoint."""
import html.parser
import urllib.parse
import urllib.request

class FormParser(html.parser.HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.action_url = base_url
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "form":
            action = values.get("action")
            if action:
                self.action_url = urllib.parse.urljoin(self.base_url, action)
        if tag.lower() == "input":
            name = values.get("name")
            if name:
                self.fields[name] = values.get("value", "")

def require_migration_library():
    raise SystemExit("MIGRATION_ONLY: retired CLI; use refresh.sh event-queue or the monthly Lichess workflow. Offline recovery may import library functions; it must publish through staging/manifest.")


def download_chess_results_pgn(fide_id: str, tournament_id: str, *, load_form, form_url, user_agent, open_url, decode_response) -> str:
    form = load_form(form_url)
    fields = dict(form["fields"])
    fields["ctl00$P1$Txt_FideID"] = fide_id
    fields["ctl00$P1$txt_dbkey"] = tournament_id
    fields["ctl00$P1$combo_anzahl_zeilen"] = "5"
    fields["ctl00$P1$cb_DownLoadPGN"] = "Download as PGN-File"
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        form["action_url"],
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": user_agent,
            "Referer": form["base_url"],
        },
        method="POST",
    )
    with open_url(request) as response:
        return decode_response(response.read())
