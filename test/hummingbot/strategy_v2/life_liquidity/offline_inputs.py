"""Versioned regression inputs; never fresh acceptance evidence or permission."""
from pathlib import Path

DIRECTORY = Path(__file__).with_name('fixtures') / 'offline_inputs'
SPOT_REPLAY = DIRECTORY / 'spot_replay.json'
SPOT_ACCEPTANCE = DIRECTORY / 'spot_acceptance.json'
COMBINED_REPLAY = DIRECTORY / 'combined_replay.json'
FULL_ACCEPTANCE = DIRECTORY / 'full_acceptance.json'
REVIEW_ACCEPTANCE = DIRECTORY / 'review_acceptance.json'
BUDGETS = DIRECTORY / 'budgets.json'
