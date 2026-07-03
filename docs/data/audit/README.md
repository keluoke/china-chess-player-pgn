# PGN Data Audit

Generated: 2026-07-02T12:39:13+00:00

## Headline

- By-player games: 22183
- Registry players: 11456
- Players with PGN: 1289 (11.25%)
- Missing Chess-Results pairs: 166
- Targetable missing Chess-Results pairs: 166

## Source Mix

- Lichess Broadcasts: 21073 games (95.0%)
- Chess-Results: 1110 games (5.0%)

## Files

- `source-coverage.json`: source, stage, and player source mix.
- `missing-pgn-events.json`: known static events without usable PGN.
- `player-coverage.json`: registry coverage and priority uncovered players.
- `chess-results-targets.json`: known Chess-Results player/tournament pairs.
- `chess-results-discovery.json`: written by `--discover-chess-results` runs.
- `candidate-pgn-packages.json`: candidate Chess-Results PGN/event pages from discovery.
