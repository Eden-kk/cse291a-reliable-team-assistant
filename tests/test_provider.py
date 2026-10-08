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
