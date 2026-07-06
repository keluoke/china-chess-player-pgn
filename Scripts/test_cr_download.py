#!/usr/bin/env python3
"""Quick test: compare old vs new download path for Chess-Results."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sync_static_pgn import download_chess_results_pgn as old_download

# Test 1: old function with empty FIDE ID for known working tournament
print("=== Test 1: old_download('', '1213354') ===")
try:
    pgn = old_download("", "1213354")
    games = pgn.count("[Event ")
    print(f"  Success! {games} games, first 100 chars: {pgn[:100]!r}")
except Exception as e:
    print(f"  Error: {e}")

# Test 2: old function with empty FIDE ID for known non-working tournament  
print("\n=== Test 2: old_download('', '1226584') ===")
try:
    pgn = old_download("", "1226584")
    games = pgn.count("[Event ")
    print(f"  Success! {games} games, first 100 chars: {pgn[:100]!r}")
except Exception as e:
    print(f"  Error: {e}")

# Test 3: old function with a real FIDE ID
print("\n=== Test 3: old_download('8602980', '1213354') ===")
try:
    pgn = old_download("8602980", "1213354")
    games = pgn.count("[Event ")
    print(f"  Success! {games} games, first 100 chars: {pgn[:100]!r}")
except Exception as e:
    print(f"  Error: {e}")
