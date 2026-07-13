# PGN Data Audit

Generated: 2026-07-13T07:12:57+00:00

## Headline

- By-player games: 72610
- Registry players: 11646
- Players with PGN: 2589 (22.23%)
- Missing Chess-Results pairs: 141
- Targetable missing Chess-Results pairs: 141

## Source Mix

- Lichess Broadcasts: 46037 games (63.4%)
- Chess-Results: 26573 games (36.6%)

## Files

- `source-coverage.json`: source, stage, and player source mix.
- `missing-pgn-events.json`: known static events without usable PGN.
- `player-coverage.json`: registry coverage and priority uncovered players.
- `chess-results-targets.json`: known Chess-Results player/tournament pairs.
- `chess-results-discovery.json`: written by `--discover-chess-results` runs.
- `candidate-pgn-packages.json`: candidate Chess-Results PGN/event pages from discovery.
