"""Unknown authorization is retryable, not a confirmed login or rejection."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is needed to execute frontend state")
@pytest.mark.parametrize("state", [
    {"auth_state": "valid", "is_authorized": True, "has_token": True},
    {"auth_state": "unknown", "is_authorized": False, "has_token": True},
    {"auth_state": "unknown", "is_authorized": True, "has_token": True},
    {"auth_state": "unknown", "is_authorized": False, "has_token": False},
    {"auth_state": "invalid", "is_authorized": False, "has_token": True},
    {"is_authorized": False},
])
def test_library_access_and_status_for_authorization_state(state):
    import json

    script = r"""
const fs = require('fs');
const assert = require('assert/strict');
const html = fs.readFileSync('applemusic/web/static/index.html', 'utf8');
const start = html.indexOf('        const authUnknown =');
const end = html.indexOf('        const showAuthModal =', start);
assert(start >= 0 && end > start);
const config = {value: JSON.parse(process.argv[1])};
const computed = getter => ({get value() {return getter();}});
const state = new Function('config', 'computed', html.slice(start, end) +
  '\nreturn {authUnknown, canAccessLibrary, authStatusText};')(config, computed);
const unknown = config.value.auth_state === 'unknown' && Boolean(config.value.has_token);
assert.equal(state.authUnknown.value, unknown);
assert.equal(state.canAccessLibrary.value, Boolean(config.value.is_authorized || unknown));
if (unknown) {
  assert(state.authStatusText.value.includes('暂时无法验证'));
  assert(!state.authStatusText.value.includes('已连接'));
}
assert(html.includes('v-if="!canAccessLibrary"'));
assert(html.includes('if (!canAccessLibrary.value)'));
assert(html.includes('if (canAccessLibrary.value)'));
"""
    result = subprocess.run(
        [NODE, "-e", script, json.dumps(state)], cwd=ROOT,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
