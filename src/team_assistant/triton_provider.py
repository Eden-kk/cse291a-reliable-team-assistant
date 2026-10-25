"""Bounded, recorded UCSD TritonAI inference; no retries or tool execution."""
import asyncio
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from .dispatch import Outcome

BASE_URL = 'https://tritonai-api.ucsd.edu/v1'
MODEL = 'api-glm-5.3'
MAX_TOKENS = 8192
INPUT_PRICE = Decimal('1.2') / 1000000
OUTPUT_PRICE = Decimal('3.25') / 1000000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(body, key, timeout):
    request = urllib.request.Request(BASE_URL + '/chat/completions', data=body,
                                     headers={'Authorization': 'Bearer ' + key,
                                              'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.code, dict(response.headers), response.read()


def _money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError('Invalid monetary cap') from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError('Invalid monetary cap')
    return amount


class TritonProvider:
    def __init__(self, output, *, max_calls, maximum_usd, input_byte_cap=57952, timeout=180, json_object=False):
        if os.environ.get('OPENAI_BASE_URL') != BASE_URL:
            raise ValueError('Expected the exact UCSD TritonAI endpoint')
        self._key = os.environ.get('OPENAI_API_KEY', '')
        if not self._key or self._key.isspace():
            raise ValueError('OPENAI_API_KEY is required')
        if type(max_calls) is not int or max_calls <= 0:
            raise ValueError('max_calls must be a positive integer')
        if type(input_byte_cap) is not int or not 512 < input_byte_cap <= 57952:
            raise ValueError('input_byte_cap must be an integer between 513 and 57952')
        if type(timeout) not in (int, float) or not 0 < timeout <= 180:
            raise ValueError('timeout must be positive and at most 180 seconds')
        if type(json_object) is not bool:
            raise ValueError('json_object must be boolean')
        self.json_object = json_object
        self.maximum_usd = _money(maximum_usd)
        self.max_calls = max_calls
        self.input_byte_cap = input_byte_cap
        self.reservation_tokens = input_byte_cap + MAX_TOKENS
        self.reservation_usd = input_byte_cap * INPUT_PRICE + MAX_TOKENS * OUTPUT_PRICE
        self.spent_usd = Decimal(0)
        self.timeout = timeout
        self.model = MODEL
        self.records = []
        self.next_id = 0
        self.halted = False
        self._lock = asyncio.Lock()
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)

    def _write(self, path, value):
        # Server bodies and supplied evidence can echo credentials; redact at rest.
        raw = value if isinstance(value, bytes) else json.dumps(value, indent=2).encode()
        raw = raw.replace(self._key.encode(), b'[REDACTED]')
        raw = raw.replace(json.dumps(self._key)[1:-1].encode(), b'[REDACTED]')
        path.write_bytes(raw)

    async def complete(self, instruction, request):
        async with self._lock:
            return await self._complete(instruction, request)

    async def _complete(self, instruction, request):
        if self.halted:
            return Outcome({}, None, 'Provider halted')
        if self.next_id >= self.max_calls:
            return Outcome({}, 0, 'Call cap exceeded')
        if self.spent_usd + self.reservation_usd > self.maximum_usd:
            return Outcome({}, 0, 'Monetary reservation exceeds cap')
        try:
            if not isinstance(instruction, str):
                raise ValueError()
            body = {'model': MODEL, 'messages': [
                {'role': 'system', 'content': 'No tools or files. Use supplied evidence only. Return one JSON object.\n' + instruction},
                {'role': 'user', 'content': json.dumps(request, allow_nan=False)}],
                'max_tokens': MAX_TOKENS, 'temperature': 0, 'reasoning_effort': 'low', 'stream': False}
            if self.json_object:
                body['response_format'] = {'type': 'json_object'}
            encoded = json.dumps(body, separators=(',', ':'), allow_nan=False).encode()
        except (ValueError, TypeError):
            return Outcome({}, 0, 'Invalid request')
        # UTF-8 bytes bound text tokens; reserve additional chat framing overhead.
        if len(encoded) + 512 > self.input_byte_cap:
            return Outcome({}, 0, 'Input byte cap exceeded')
        self.next_id += 1
        prefix = self.output / f'{self.next_id:03d}'
        record = {'call': self.next_id, 'model': MODEL, 'instruction': instruction,
                  'request': request, 'body': body, 'input_bytes': len(encoded),
                  'reserved_tokens': self.reservation_tokens, 'reserved_usd': str(self.reservation_usd)}
        self._write(prefix.with_suffix('.request.json'), record)
        self.spent_usd += self.reservation_usd
        started = time.monotonic()
        outcome = Outcome({}, None, 'Provider did not complete')
        try:
            status, headers, raw = await asyncio.wait_for(
                asyncio.to_thread(_post, encoded, self._key, self.timeout), self.timeout)
            self._write(prefix.with_suffix('.response.json'), raw)
            record['status'] = status
            record['headers'] = {k.lower(): v for k, v in headers.items()
                                 if any(word in k.lower() for word in ('usage', 'cache', 'cost'))}
            if status != 200:
                raise ValueError('HTTP response rejected')
            data = json.loads(raw)
            usage = data.get('usage')
            record['usage'] = usage
            choices = data.get('choices')
            if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
                record['finish_reason'] = choices[0].get('finish_reason')
            if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0
                                                 for k in ('prompt_tokens', 'completion_tokens')):
                raise ValueError('Unknown usage')
            pt, ct = usage['prompt_tokens'], usage['completion_tokens']
            outcome.tokens = pt + ct
            modeled = pt * INPUT_PRICE + ct * OUTPUT_PRICE
            record['modeled_usd'] = str(modeled)
            charge = modeled
            header_cost = record['headers'].get('x-litellm-response-cost')
            if header_cost is not None:
                cost = Decimal(header_cost)
                if not cost.is_finite() or cost < 0:
                    raise ValueError('Invalid cost header')
                charge = max(charge, cost)
            self.spent_usd += charge - self.reservation_usd
            record['charged_usd'] = str(charge)
            if pt > self.input_byte_cap or ct > MAX_TOKENS or charge > self.reservation_usd:
                raise ValueError('Response exceeds reservation')
            if not isinstance(choices, list) or len(choices) != 1:
                raise ValueError('Expected one choice')
            choice = choices[0]
            record['finish_reason'] = choice.get('finish_reason')
            message = choice.get('message', {})
            if choice.get('finish_reason') != 'stop' or message.get('tool_calls') or message.get('function_call'):
                raise ValueError('Truncated response or forbidden tool call')
            content = message.get('content')
            if not isinstance(content, str) or len(content) > 10000:
                raise ValueError('Invalid output content')
            value = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, dict):
                raise ValueError('Expected JSON object')
            outcome.value = value
            outcome.error = None
        except asyncio.CancelledError:
            self.halted = True
            outcome.error = 'CancelledError'
            raise
        except Exception as exc:
            self.halted = True
            # Never persist exception strings, which can contain authentication context.
            outcome.error = type(exc).__name__
        finally:
            record.update(seconds=time.monotonic() - started, tokens=outcome.tokens,
                          response=outcome.value, error=outcome.error, halted=self.halted,
                          spent_usd=str(self.spent_usd))
            self.records.append(record)
            self._write(prefix.with_suffix('.json'), record)
        return outcome
