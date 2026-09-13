"""Indexed PGN lookup with explicit rejection of ambiguous matches."""
from collections import defaultdict


class GameLookup:
    def __init__(self, games, parser):
        self.parser = parser
        self.by_digest = {}
        self.exact = defaultdict(dict)
        self.by_event_date = defaultdict(dict)
        for index, game in enumerate(games):
            headers = parser.pgn_headers(game)
            digest = parser.stable_game_hash(game)
            record = (index, game, headers)
            self.by_digest.setdefault(digest, record)
            self.exact[parser.game_key_from_headers(headers)].setdefault(digest, record)
            key = (parser.normalize_key(headers.get("Event")),
                   parser.date_key(headers.get("EventDate") or headers.get("Date")))
            self.by_event_date[key].setdefault(digest, record)
        self.cache = {}

    def resolve(self, entry):
        p = self.parser
        digest = entry.get("gameSha256")
        if digest:
            record = self.by_digest.get(digest)
            if not record or p.game_key_from_headers(record[2]) != p.game_key_from_entry(entry):
                return None
            return record[:2]
        key = p.game_key_from_entry(entry)
        if key in self.cache:
            return self.cache[key]
        matches = self.exact.get(key, {})
        if not matches:
            group = self.by_event_date.get((p.normalize_key(entry.get("event")), p.date_key(entry.get("date"))), {})
            # Empty names are not evidence for a substring match.
            matches = {digest: record for digest, record in group.items()
                       if p.normalize_key(entry.get("white")) and p.normalize_key(entry.get("black"))
                       and p.loose_match(record[2], entry)}
        result = next(iter(matches.values()))[:2] if len(matches) == 1 else None
        self.cache[key] = result
        return result
