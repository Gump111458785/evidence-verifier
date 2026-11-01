import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from relations import evaluate, validate_plan, PlanError


def fixture(subject, predicate, obj, eid, verdict='entailed', polarity='positive'):
    text = subject + ' ' + predicate + ' ' + obj + '.'
    def span(value):
        start = text.index(value)
        return {'evidence_id': eid, 'text': value, 'start': start, 'end': start+len(value)}
    q = {'evidence_id': eid, 'text': text, 'start': 0, 'end': len(text)}
    f = {'subject': subject, 'predicate': predicate, 'object': obj, 'polarity': polarity,
         'exclusive': False, 'time_scope': 'unspecified', 'quotes': [q],
         'subject_spans': [span(subject)], 'object_spans': [span(obj)]}
    return {'fact': f, 'verification': {'status': 'ok', 'parsed': {'verdict': verdict}}}, {'id': eid, 'text': text}


class RelationPlanTests(unittest.TestCase):
    plan = [{'subject': 'A', 'predicate': 'has_child', 'object': '?bridge'},
            {'subject': '?bridge', 'predicate': 'has_child', 'object': 'D'}]

    def run_plan(self, facts, plan=None):
        rows, evidence = zip(*facts) if facts else ([], [])
        return evaluate(plan or self.plan, list(rows), list(evidence))

    def test_consistent_chain_binds_same_intermediate(self):
        result = self.run_plan([fixture('A', 'has_child', 'B', 'E1'), fixture('B', 'has_child', 'D', 'E2')])
        self.assertEqual(result['verdict'], 'entailed')
        self.assertEqual(result['bindings'], {'?bridge': 'B'})
        self.assertEqual(result['complete_question_coverage'], 'not_established')

    def test_individually_supported_but_disconnected_chain_is_missing(self):
        result = self.run_plan([fixture('A', 'has_child', 'B', 'E1'), fixture('C', 'has_child', 'D', 'E2')])
        self.assertEqual(result['verdict'], 'missing')
        self.assertTrue(result['binding_gap'])

    def test_later_relation_disambiguates_multiple_initial_bindings(self):
        result = self.run_plan([fixture('A', 'has_child', 'B', 'E1'), fixture('A', 'has_child', 'C', 'E2'), fixture('B', 'has_child', 'D', 'E3')])
        self.assertEqual(result['verdict'], 'entailed')
        self.assertEqual(result['bindings']['?bridge'], 'B')

    def test_two_complete_bindings_remain_ambiguous(self):
        result = self.run_plan([fixture('A', 'has_child', 'B', 'E1'), fixture('A', 'has_child', 'C', 'E2'), fixture('B', 'has_child', 'D', 'E3'), fixture('C', 'has_child', 'D', 'E4')])
        self.assertEqual(result['verdict'], 'ambiguous')

    def test_roles_cannot_be_reversed(self):
        result = self.run_plan([fixture('B', 'has_child', 'A', 'E1'), fixture('B', 'has_child', 'D', 'E2')])
        self.assertEqual(result['verdict'], 'missing')

    def test_employment_does_not_fill_education(self):
        result = self.run_plan([fixture('A', 'employed_at', 'University', 'E1')],
                               [{'subject': 'A', 'predicate': 'educated_at', 'object': 'University'}])
        self.assertEqual(result['verdict'], 'missing')

    def test_unsupported_types_are_not_missing(self):
        with self.assertRaises(PlanError):
            validate_plan([{'subject': 'A', 'predicate': 'born_at', 'object': 'B'}])

    def test_variable_type_conflict_is_rejected(self):
        with self.assertRaisesRegex(PlanError, 'incompatible'):
            validate_plan([{'subject': 'A', 'predicate': 'has_child', 'object': '?x'},
                           {'subject': '?x', 'predicate': 'opened_in_year', 'object': '1990'}])

    def test_unimplemented_qualifier_is_not_silently_ignored(self):
        with self.assertRaisesRegex(PlanError, 'Unsupported relation fields'):
            validate_plan([dict(self.plan[0], only_child=True)])

    def test_wrong_span_is_invalid_not_supported(self):
        f = fixture('A', 'has_child', 'B', 'E1')
        f[0]['fact']['subject_spans'][0]['text'] = 'Wrong'
        result = self.run_plan([f])
        self.assertEqual(result['status'], 'invalid_output')
        self.assertNotIn('verdict', result)

    def test_rejected_fact_cannot_complete_a_chain(self):
        result = self.run_plan([fixture('A', 'has_child', 'B', 'E1'), fixture('B', 'has_child', 'D', 'E2', verdict='missing')])
        self.assertEqual(result['verdict'], 'missing')

    def test_fact_must_have_independent_verification(self):
        f = fixture('A', 'has_child', 'B', 'E1'); f[0].pop('verification')
        self.assertEqual(self.run_plan([f])['status'], 'invalid_output')

    def test_source_conflict_is_not_hidden_by_positive_path(self):
        result = self.run_plan([fixture('A', 'has_child', 'B', 'E1'), fixture('B', 'has_child', 'D', 'E2'), fixture('B', 'has_child', 'D', 'E3', polarity='negative')])
        self.assertEqual(result['verdict'], 'ambiguous')


if __name__ == '__main__':
    unittest.main()
