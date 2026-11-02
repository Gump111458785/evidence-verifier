"""Protocol fixtures only: validates real kernel, not model quality or speed."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import CASES, live


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'Requires existing jsonschema environment')
class LiveContractTests(unittest.TestCase):
    def setUp(self):
        self.cache = {}
        self.requests = []
        self.events = []
        self.row = copy.deepcopy(CASES['employment-positive.clarified'])
        self.fact = {'subject': 'Lena', 'predicate': 'employed_at',
                     'object': {'kind': 'literal', 'text': 'Neri University'},
                     'quote_ids': ['Q1'], 'polarity': 'positive', 'qualifier': 'none'}
        self.override = None

    def transport(self, request, timeout):
        body = json.loads(request.data)
        self.requests.append(body)
        stage = body['response_format']['json_schema']['name']
        if self.override is not None:
            output = self.override(stage)
        else:
            output = {'facts': [self.fact]} if stage == 'extract' else {'verdict': 'entailed', 'rationale': 'Protocol fixture'}
            if stage == 'direct':
                output.update(facts=[self.fact])
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(output)}}],
                                    'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}).encode())

    def run_live(self, row=None, mode='split'):
        with patch.dict(os.environ, {'EV_MODEL_URL': 'http://fixture.invalid/v1', 'EV_MODEL_NAME': 'fixture-model', 'EV_MODEL_REVISION': 'fixture-v1'}), \
             patch('urllib.request.urlopen', self.transport):
            return live(row or self.row, mode, self.cache.get, self.cache.__setitem__,
                        lambda *args: self.events.append(args))

    def test_full_kernel_extract_verify_and_binding(self):
        result = self.run_live()
        self.assertEqual(result['output']['prediction']['verdict'], 'entailed')
        self.assertEqual(result['usage']['model_calls'], 2)
        self.assertEqual(set(json.loads(self.requests[0]['messages'][1]['content'])),
                         {'predicate_definitions', 'evidence'})

    def test_same_evidence_different_target_reuses_facts_without_inheriting_verdict(self):
        self.run_live()
        second = copy.deepcopy(self.row)
        second['relation']['predicate'] = 'educated_at'
        result = self.run_live(second)
        self.assertEqual(result['output']['prediction']['verdict'], 'missing')
        self.assertEqual(result['usage']['model_calls'], 0)
        self.assertEqual(result['usage']['cache_hits'], 2)

    def test_changed_uncited_evidence_reextracts_but_may_reuse_exact_citation_check(self):
        self.run_live()
        second = copy.deepcopy(self.row)
        second['evidence'][0]['text'] += ' This is extra context.'
        result = self.run_live(second)
        self.assertEqual(result['usage']['model_calls'], 1)
        self.assertEqual(result['usage']['cache_hits'], 1)
        self.assertEqual(self.requests[-1]['response_format']['json_schema']['name'], 'extract')

    def test_changed_cited_sentence_invalidates_its_support_cache(self):
        self.run_live()
        second = copy.deepcopy(self.row)
        second['evidence'][0]['text'] = 'Lena worked as an instrument maker at Neri University, starting in 1998.'
        result = self.run_live(second)
        self.assertEqual(result['usage']['cache_hits'], 0)
        self.assertEqual(result['usage']['model_calls'], 2)

    def test_unsupported_quote_is_invalid_not_missing(self):
        self.fact['object']['text'] = 'Invented institution'
        result = self.run_live()
        self.assertEqual(result['output']['status'], 'invalid_output')
        self.assertNotIn('prediction', result['output'])
        self.assertEqual(len(self.requests), 1)

    def test_direct_positive_requires_grounded_matching_fact(self):
        self.override = lambda stage: {'verdict': 'entailed', 'facts': [], 'rationale': 'Fixture with no support'}
        result = self.run_live(mode='direct')
        self.assertEqual(result['output']['status'], 'invalid_output')

    def test_verifier_rejection_blocks_raw_extracted_fact(self):
        self.override = lambda stage: {'facts': [self.fact]} if stage == 'extract' else {'verdict': 'missing', 'rationale': 'Reject fixture'}
        result = self.run_live()
        self.assertEqual(result['output']['prediction']['verdict'], 'missing')

    def test_malformed_output_is_not_cached(self):
        self.override = lambda stage: {'unexpected': 'bad format'}
        with self.assertRaises(Exception):
            self.run_live()
        self.assertEqual(self.cache, {})

    def test_changed_model_revision_does_not_reuse_prior_results(self):
        self.run_live()
        with patch.dict(os.environ, {'EV_MODEL_URL': 'http://fixture.invalid/v1', 'EV_MODEL_NAME': 'fixture-model', 'EV_MODEL_REVISION': 'fixture-v2'}), \
             patch('urllib.request.urlopen', self.transport):
            result = live(self.row, 'split', self.cache.get, self.cache.__setitem__, lambda *a: None)
        self.assertEqual(result['usage']['cache_hits'], 0)

    def test_unversioned_service_disables_cross_run_cache_reuse(self):
        with patch.dict(os.environ, {'EV_MODEL_URL': 'http://fixture.invalid/v1', 'EV_MODEL_NAME': 'fixture-model', 'EV_MODEL_REVISION': ''}), \
             patch('urllib.request.urlopen', self.transport):
            for _ in range(2):
                result = live(self.row, 'split', self.cache.get, self.cache.__setitem__, lambda *a: None)
                self.assertEqual(result['usage']['cache_hits'], 0)
                self.assertEqual(result['cache_scope'], 'execution_only')


if __name__ == '__main__':
    unittest.main()
