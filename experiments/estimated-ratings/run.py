#!/usr/bin/env python3
"""Run preserved local experiments against the current code workspace."""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = pathlib.Path(__file__).resolve().parent / 'Scripts'
choices = {'pilot': 'build_estimated_ratings_pilot.py', 'experiments': 'run_estimated_ratings_experiments.py'}
if len(sys.argv) < 2 or sys.argv[1] not in choices:
    raise SystemExit('Usage: run.py pilot|experiments [original CLI arguments]')
name = sys.argv.pop(1)
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('local_rating_experiment', SCRIPTS / choices[name])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.REPO_ROOT = ROOT
raise SystemExit(module.main())
