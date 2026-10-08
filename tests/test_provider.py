from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from team_assistant.provider import decode
from team_assistant.dispatch import Budget, Dispatcher


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_json_still_charges_known_usage(self):
        events = [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'not JSON'}},
                  {'type': 'turn.completed', 'usage': {'input_tokens': 20, 'output_tokens': 5}}]
        outcome, _ = decode(events, 0)
        self.assertIsNotNone(outcome.error)
        dispatcher = Dispatcher(Budget(100))
        async def invoke():
            return outcome
        with self.assertRaises(RuntimeError):
            await dispatcher.call('A', 'response', 40, invoke)
        self.assertEqual(dispatcher.budget.spent, 25)
        self.assertFalse(dispatcher.budget.halted)

    async def test_forbidden_activity_rejected(self):
        outcome, _ = decode([{'type': 'item.completed', 'item': {'type': 'command_execution'}}], 0)
        self.assertIsNotNone(outcome.error)
        self.assertIsNone(outcome.tokens)

    async def test_invalid_status_cannot_pass_contract(self):
        from team_assistant.contracts import check_diagnosis
        observations = [{'id': 'a', 'tool': 't1', 'arguments': {}, 'text': 'first evidence', 'sha256': '1'},
                        {'id': 'b', 'tool': 't2', 'arguments': {}, 'text': 'second evidence', 'sha256': '2'}]
        value = {'fault_object': 'app/a', 'root_cause': 'cause',
                 'evidence': [{'observation_id': 'a', 'quote': 'first evidence'},
                              {'observation_id': 'b', 'quote': 'second evidence'}]}
        self.assertIn('status must be diagnosed or uncertain',
                      check_diagnosis(value, observations, {'cause': 'app'}, {'app/a'}))

    async def test_malformed_diagnosis_fields_return_errors(self):
        from team_assistant.contracts import check_diagnosis
        for cause, identifier, quote in [([], 'a', 'evidence'), ('cause', [], 'evidence'), ('cause', 'a', [])]:
            errors = check_diagnosis({'status': 'diagnosed', 'root_cause': cause,
                                     'fault_object': 'app/a', 'evidence': [{'observation_id': identifier, 'quote': quote}]},
                                    [], {'cause': 'app'}, {'app/a'})
            self.assertTrue(errors)
