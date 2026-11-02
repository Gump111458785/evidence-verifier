"""Source-revision contracts; no remote models and no changes to the frozen oracle."""
import copy
from contextlib import nullcontext
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import live
from grounding import apply_revision, source_payload, expand_local_context


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'Requires jsonschema')
class GroundingTests(unittest.TestCase):
    def test_abbreviated_work_title_stays_verbatim_in_one_source_span(self):
        from evpilot import relation_kernel_v7 as kernel
        text = 'Mara Chen designed the Copper Engine No. 2. It was never built.'
        _, cat = source_payload([{'id': 'E', 'text': text}], kernel)
        fact = {'subject': 'Copper Engine No. 2', 'predicate': 'designed_by',
                'object': {'kind': 'literal', 'text': 'Mara Chen'}, 'quote_ids': ['Q1'],
                'polarity': 'positive', 'qualifier': 'none'}
        result = kernel.materialize_fact(fact, cat)
        self.assertEqual(result['source_subject'], 'Copper Engine No. 2')
        for span in cat.values():self.assertEqual(text[span['start']:span['end']], span['text'])
        self.assertEqual(len(cat), 2)

    def setUp(self):
        self.row = {'id': 'grounding-fixture', 'relation': {'subject': 'Mara Chen', 'predicate': 'educated_at',
                    'object': 'Cedar College', 'subject_type': 'person', 'object_type': 'organization'},
                    'evidence': [{'id': 'E1', 'text': 'Mara Chen was born in Harbor City. Chen studied at Cedar College. The Copper Engine was designed by Lena Li.'},
                                 {'id': 'E2', 'text': 'Another unrelated source mentions Mara Chen.'}]}
        self.bad = {'subject': 'Mara Chen', 'predicate': 'educated_at', 'object': {'kind': 'literal', 'text': 'Cedar College'},
                    'quote_ids': ['Q2'], 'polarity': 'positive', 'qualifier': 'none'}
        self.valid = {'subject': 'Copper Engine', 'predicate': 'designed_by', 'object': {'kind': 'literal', 'text': 'Lena Li'},
                      'quote_ids': ['Q3'], 'polarity': 'positive', 'qualifier': 'none'}
        self.original = [self.bad, self.valid]
        self.revised = copy.deepcopy(self.original);self.revised[0]['quote_ids'] = ['Q1', 'Q2']
        self.requests = [];self.events = [];self.verdict = 'entailed'

    def transport(self, request, timeout):
        body = json.loads(request.data);self.requests.append(body)
        stage = body['response_format']['json_schema']['name']
        value = ({'facts': self.original} if stage == 'extract' else {'facts': self.revised}
                 if stage == 'grounding_repair' else {'verdict': self.verdict, 'rationale': 'fixture'})
        return io.BytesIO(json.dumps({'choices': [{'message': {'content': json.dumps(value)}}],
                                     'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}).encode())

    def run_live(self, enabled='1', local_context=False):
        with patch.dict(os.environ, {'EV_MODEL_URL': 'http://fixture.invalid/v1', 'EV_MODEL_NAME': 'fixture',
                    'EV_MODEL_REVISION': 'fixture-v1', 'EV_GROUNDING_REPAIR_ATTEMPTS': enabled}), \
             patch('urllib.request.urlopen', self.transport), \
             (nullcontext() if local_context else patch('engine.expand_local_context',
                    side_effect=lambda facts, *args: (copy.deepcopy(facts), []))):
            return live(self.row, 'split', lambda key: None, lambda key, value: None,
                        lambda *args: self.events.append(args))

    def test_adjacent_antecedent_is_added_without_a_paid_model_revision(self):
        result = self.run_live(local_context=True)
        self.assertEqual(result['output']['status'], 'ok')
        self.assertFalse(result['output']['grounding']['repair_attempted'])
        self.assertTrue(result['output']['grounding']['local_context_expansions'][0]['applied'])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(result['output']['facts'][0]['raw']['quote_ids'], ['Q1','Q2'])

    def test_context_expansion_keeps_intervening_disambiguating_text(self):
        from evpilot import relation_kernel_v7 as kernel
        evidence = [{'id':'E','text':'Mara Chen visited a laboratory. That researcher was someone else. She studied at Cedar College.'}]
        payload, cat = source_payload(evidence, kernel)
        raw = copy.deepcopy(self.bad);raw['quote_ids'] = ['Q3']
        revised, audit = expand_local_context([raw], [{'index':0}], payload, cat)
        self.assertEqual(revised[0]['quote_ids'], ['Q1','Q2','Q3'])
        self.assertEqual(audit[0]['semantic_support'], 'not_established')

    def test_oversized_adjacent_context_is_rejected_without_trimming(self):
        from evpilot import relation_kernel_v7 as kernel
        evidence = [{'id':'E','text':'Mara Chen '+('x'*700)+'. She studied at Cedar College.'}]
        payload, cat = source_payload(evidence, kernel)
        raw = copy.deepcopy(self.bad)
        revised, audit = expand_local_context([raw], [{'index':0}], payload, cat)
        self.assertEqual(revised[0], raw)
        self.assertFalse(audit[0]['applied'])

    def test_one_revision_keeps_original_errors_and_verifies_only_cited_sentences(self):
        result = self.run_live();out = result['output']
        self.assertEqual(out['status'], 'ok');self.assertEqual(out['prediction']['verdict'], 'entailed')
        self.assertEqual(out['grounding']['repaired_fact_indices'], [0])
        self.assertEqual(out['grounding']['initial_errors'][0]['index'], 0)
        self.assertEqual(out['grounding']['final_errors'], [])
        self.assertEqual([r['response_format']['json_schema']['name'] for r in self.requests],
                         ['extract', 'grounding_repair', 'verify', 'verify'])
        for body in self.requests[:2]:
            value = json.loads(body['messages'][1]['content'])
            self.assertFalse({'question','candidate','target_relation'} & set(value))
        value = json.loads(self.requests[2]['messages'][1]['content'])
        self.assertEqual([s['quote_id'] for d in value['evidence'] for s in d['segments']], ['Q1','Q2'])
        self.assertNotIn('Copper Engine', json.dumps(value['evidence']))
        self.assertEqual(out['facts'][1]['raw'], self.valid)

    def test_disabled_revision_fails_before_semantic_calls(self):
        out = self.run_live('0')['output']
        self.assertEqual(out['status'], 'invalid_output');self.assertNotIn('prediction', out)
        self.assertEqual(len(self.requests), 1)

    def test_unrepaired_error_cannot_loop_or_be_counted_as_missing(self):
        self.revised = copy.deepcopy(self.original)
        out = self.run_live()['output']
        self.assertEqual(out['status'], 'invalid_output');self.assertNotIn('prediction', out)
        self.assertEqual(len(self.requests), 2)

    def test_repair_cannot_replace_relationship_to_make_a_target_pass(self):
        self.revised[0]['predicate'] = 'employed_at'
        out = self.run_live()['output']
        self.assertEqual(out['status'], 'invalid_output');self.assertEqual(len(self.requests), 2)
        self.assertIn('semantic field', out['errors'][0]['error'])

    def test_repair_cannot_borrow_entity_identity_from_another_document(self):
        self.revised[0]['quote_ids'] = ['Q2','Q4']
        out = self.run_live()['output']
        self.assertEqual(out['status'], 'invalid_output')
        self.assertIn('different source', out['errors'][0]['error'])

    def test_repair_cannot_change_already_grounded_facts(self):
        self.revised[1]['object']['text'] = 'Someone Else'
        out = self.run_live()['output']
        self.assertEqual(out['status'], 'invalid_output')
        self.assertIn('already grounded', out['errors'][0]['error'])

    def test_repair_cannot_drop_invalid_hypotheses(self):
        with self.assertRaisesRegex(ValueError, 'add or remove'):
            apply_revision(self.original, [self.valid], [{'index': 0}], {})

    def test_locatable_strings_do_not_override_semantic_rejection(self):
        self.verdict = 'missing'
        out = self.run_live()['output']
        self.assertEqual(out['status'], 'ok');self.assertEqual(out['prediction']['verdict'], 'missing')


if __name__ == '__main__':unittest.main()
