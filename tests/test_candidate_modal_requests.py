"""Execute the real modal handlers with controlled, out-of-order fetches."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is needed to execute frontend handlers")
@pytest.mark.parametrize("scenario", ["switch", "reopen", "out_of_order", "stale_error", "current"])
def test_candidate_search_is_scoped_to_modal_and_latest_request(scenario):
    script = r"""
const fs = require('fs');
const assert = require('assert/strict');
const html = fs.readFileSync('applemusic/web/static/index.html', 'utf8');
const start = html.indexOf('        let modalSearchRequestId =');
const end = html.indexOf('        const openSingleDiagnosticsModal =', start);
assert(start >= 0 && end > start);
const section = html.slice(start, end);
const ref = value => ({value});
const candidateModalItem = ref(null), modalCandidates = ref([]), modalSearchQuery = ref('');
const config = ref({storefront: 'cn'}), pending = [], alerts = [];
const fetch = () => new Promise((resolve, reject) => pending.push({resolve, reject}));
const api = new Function('candidateModalItem', 'modalCandidates', 'modalSearchQuery',
    'config', 'fetch', 'alert', section + '\nreturn {openCandidateModal, searchAlternative, selectCandidateForModal};')(
    candidateModalItem, modalCandidates, modalSearchQuery, config, fetch, message => alerts.push(message));
const aCandidate = {track: {id: 'a', title: 'Song A'}};
const bCandidate = {track: {id: 'b', title: 'Song B'}};
const a = {source_track: {title: 'Song A', artists: ['Artist A']}, candidates: [aCandidate]};
const b = {source_track: {title: 'Song B', artists: ['Artist B']}, candidates: [bCandidate]};
const response = results => ({json: async () => ({results})});
(async () => {
  api.openCandidateModal(a);
  const first = api.searchAlternative();
  switch (process.argv[1]) {
    case 'switch':
      candidateModalItem.value = null;
      api.openCandidateModal(b);
      pending[0].resolve(response([aCandidate]));
      await first;
      assert.equal(modalCandidates.value[0].track.id, 'b');
      api.selectCandidateForModal(modalCandidates.value[0]);
      assert.equal(b.selected_candidate.track.id, 'b');
      break;
    case 'reopen':
      candidateModalItem.value = null;
      api.openCandidateModal(a);
      pending[0].resolve(response([bCandidate]));
      await first;
      assert.equal(modalCandidates.value[0].track.id, 'a');
      break;
    case 'out_of_order': {
      modalSearchQuery.value = 'new query';
      const second = api.searchAlternative();
      pending[1].resolve(response([bCandidate]));
      await second;
      pending[0].resolve(response([aCandidate]));
      await first;
      assert.equal(modalCandidates.value[0].track.id, 'b');
      break;
    }
    case 'stale_error':
      api.openCandidateModal(b);
      pending[0].reject(new Error('outdated failure'));
      await first;
      assert.deepEqual(alerts, []);
      assert.equal(modalCandidates.value[0].track.id, 'b');
      break;
    case 'current': {
      pending[0].resolve(response([bCandidate]));
      await first;
      assert.equal(modalCandidates.value[0].track.id, 'b');
      const second = api.searchAlternative();
      pending[1].reject(new Error('current failure'));
      await second;
      assert.equal(alerts.length, 1);
      break;
    }
    default: throw new Error('Unknown scenario');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [NODE, "-e", script, scenario], cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(NODE is None, reason="Node.js is needed to execute frontend handlers")
def test_recommendation_badge_requires_strong_unconflicted_identity():
    script = r"""
const fs = require('fs');
const assert = require('assert/strict');
const html = fs.readFileSync('applemusic/web/static/index.html', 'utf8');
const match = html.match(/<span v-if="([^"]+)"[^>]*>推荐匹配/);
assert(match);
const recommended = new Function('cand', 'return Boolean(' + match[1] + ');');
assert(!recommended({score: .87, evidence: {verification_level: 'medium', conflicts: []}}));
assert(!recommended({score: .99, evidence: {verification_level: 'medium', conflicts: []}}));
assert(!recommended({score: .99, evidence: {verification_level: 'strong', conflicts: ['version']}}));
assert(!recommended({score: .99}));
assert(recommended({score: .99, evidence: {verification_level: 'strong', conflicts: []}}));
"""
    result = subprocess.run(
        [NODE, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
