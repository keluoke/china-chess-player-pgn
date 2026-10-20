"""Explicit authorization of the frozen, position-only machine publication rule."""
import hashlib
import json
from pathlib import Path

import brilliancy_publication_gate as gate
import brilliancy_quality_gate as classifier

PATH = Path('data/manual/brilliancies/auto-policy.json')
POLICY_ID = 'chessdb-brilliancy-auto-2026-10-v1'


def load(root: Path) -> tuple[dict, str]:
    path = root / PATH
    if path.is_symlink() or not path.is_file():
        raise ValueError('BRILLIANCY_AUTO_PUBLICATION_CLOSED')
    raw = path.read_bytes()
    policy = json.loads(raw)
    if (policy.get('schemaVersion') != 1 or policy.get('enabled') is not True
            or policy.get('policyId') != POLICY_ID
            or policy.get('ruleVersion') != gate.RULE_VERSION
            or policy.get('allowedTiers') != ['S', 'A']
            or policy.get('calibrationId') != 'history-59318-controls-v1'):
        raise ValueError('BRILLIANCY_AUTO_POLICY_INVALID')
    for field, module in (('gateCodeSha256', gate), ('classifierCodeSha256', classifier)):
        if policy.get(field) != hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest():
            raise ValueError('BRILLIANCY_AUTO_POLICY_CODE_CHANGED')
    return policy, hashlib.sha256(raw).hexdigest()
