"""Question attribution must replay exactly, including Unicode boundaries."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from planning import PlanningError, validate_proposal


class PlanningTests(unittest.TestCase):
    def test_wrong_offset_is_rejected_without_repair(self):
        question = 'Who designed X?'
        value = {'relations': [{'subject': 'X', 'predicate': 'designed_by',
                                'object': 'Nora Hale', 'time_scope': 'any'}],
                 'clauses': [{'text': question, 'start': 0, 'end': len(question),
                              'kind': 'relation', 'relation_indices': [0], 'reason': 'Designer requested'}],
                 'coverage': 'complete', 'rationale': 'Candidate is the proposed designer'}
        self.assertTrue(validate_proposal(value, question, 'Nora Hale')['coverage']['source_spans_valid'])
        bad = copy.deepcopy(value)
        bad['clauses'][0]['end'] -= 1
        with self.assertRaisesRegex(PlanningError, 'position replay'):
            validate_proposal(bad, question, 'Nora Hale')

    def test_planner_gets_program_computed_unicode_span_without_evidence(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'DB', Path(folder)/'plans.sqlite'), \
             patch.dict(os.environ, {'EV_PLANNER_URL': 'http://127.0.0.1:12345/v1', 'EV_PLANNER_NAME': 'fixture'}):
            server.init()
            question = '谁设计了 X🚀？'
            proposal = server.planner().create(question, 'Nora Hale', 'sidecar', 'fixture')
            payload = json.loads(proposal['config']['messages'][1]['content'])
            self.assertEqual(set(payload), {'question', 'candidate', 'question_source', 'predicate_definitions'})
            span = payload['question_source']
            self.assertEqual(span, {'text': question, 'start': 0, 'end': len(question)})
            self.assertEqual(question[span['start']:span['end']], span['text'])
