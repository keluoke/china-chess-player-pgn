# PGN Data Audit

Generated: 2026-07-18T12:27:52+00:00

## Headline

- By-player games: 65572
- Registry players: 11646
- Players with PGN: 2659 (22.83%)
- Missing Chess-Results pairs: 0
- Targetable missing Chess-Results pairs: 0

## Source Mix

- Lichess Broadcasts: 35266 games (53.78%)
- Static PGN: 30306 games (46.22%)

## Files

- `source-coverage.json`: source, stage, and player source mix.
- `missing-pgn-events.json`: known static events without usable PGN.
- `player-coverage.json`: registry coverage and priority uncovered players.
- `chess-results-targets.json`: known Chess-Results player/tournament pairs.
- `chess-results-discovery.json`: written by `--discover-chess-results` runs.
- `candidate-pgn-packages.json`: candidate Chess-Results PGN/event pages from discovery.
