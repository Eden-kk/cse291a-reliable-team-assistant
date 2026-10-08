"""Recorded, tool-disabled Codex CLI inference using the existing subscription."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import time

from .dispatch import Outcome


def decode(events, returncode):
    usage = next((e.get('usage') for e in reversed(events) if e.get('type') == 'turn.completed'), None)
    actual = None
    if usage and all(isinstance(usage.get(k), int) and not isinstance(usage[k], bool) and usage[k] >= 0
                     for k in ('input_tokens', 'output_tokens')):
        actual = usage['input_tokens'] + usage['output_tokens']
    forbidden = any(e.get('type', '').startswith('item.') and
                    e.get('item', {}).get('type') not in ('agent_message', 'reasoning') for e in events)
    messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                and e.get('item', {}).get('type') == 'agent_message']
    try:
        if returncode or forbidden or not messages:
            raise ValueError('CLI failed, performed forbidden activity, or returned no message')
        text = messages[-1].strip()
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0]
        if len(text) > 10000:
            raise ValueError('Response exceeds output character limit')
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError('Expected JSON object')
        return Outcome(value, actual), usage
    except (ValueError, IndexError) as exc:
        return Outcome({}, actual, str(exc)), usage


class CodexProvider:
    def __init__(self, output, model='gpt-6-astra', timeout=180):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.timeout = timeout
        self.records = []
        self.next_id = 0

    async def complete(self, instruction, request):
        self.next_id += 1
        call_id = self.next_id
        started = time.monotonic()
        record = {'call': call_id, 'model': self.model, 'instruction': instruction, 'request': request}
        prefix = self.output / f'{call_id:03d}'
        prefix.with_suffix('.request.json').write_text(json.dumps(record, indent=2))
        outcome = Outcome({}, None, 'Provider did not complete')
        try:
            with tempfile.TemporaryDirectory(prefix='team-assistant-inference-') as directory:
                command = ['codex', 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral',
                           '--skip-git-repo-check', '--sandbox', 'read-only', '-C', directory,
                           '-m', self.model, '-c', 'features.shell_tool=false', '-c', 'features.unified_exec=false',
                           '-c', 'features.multi_agent=false', '-c', 'web_search="disabled"',
                           '-c', 'apps._default.enabled=false', '-c', 'forced_login_method="chatgpt"',
                           '-c', 'model_context_window=131072', '-c', 'model_reasoning_effort="low"', '--json', '-']
                environment = {k: v for k, v in os.environ.items() if k not in ('OPENAI_API_KEY', 'CODEX_API_KEY')}
                process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
                                                               stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                               env=environment)
                prompt = 'No tools or files. Use supplied evidence only. Return one JSON object, under 2000 tokens.\n' + instruction + '\n' + json.dumps(request)
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(prompt.encode()), self.timeout)
                except (TimeoutError, asyncio.CancelledError):
                    process.kill()
                    await process.wait()
                    raise
                prefix.with_suffix('.events.jsonl').write_bytes(stdout)
                events = [json.loads(line) for line in stdout.decode().splitlines() if line.startswith('{')]
                outcome, usage = decode(events, process.returncode)
                record.update(usage=usage, stderr=stderr.decode()[-2000:], returncode=process.returncode)
        except Exception as exc:
            outcome = Outcome({}, None, type(exc).__name__ + ': ' + str(exc))
        finally:
            record.update(seconds=time.monotonic() - started, tokens=outcome.tokens,
                          response=outcome.value, error=outcome.error)
            prefix.with_suffix('.json').write_text(json.dumps(record, indent=2))
            self.records.append(record)
            print(json.dumps({'call': call_id, 'seconds': round(record['seconds'], 2),
                              'tokens': outcome.tokens, 'error': outcome.error}), flush=True)
        return outcome
