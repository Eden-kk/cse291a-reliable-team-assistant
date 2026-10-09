"""Narrow, predeclared query-record faults; never modify unrelated numeric fields."""
import copy
import hashlib
import re

SCOPES = ('legacy-substring-v1', 'query-fields-v2')


def _section(lines, header, start=0):
    matches = [i for i in range(start, len(lines)) if lines[i].strip() == header]
    if len(matches) != 1:
        raise ValueError('Expected one unambiguous section: ' + header)
    first = matches[0]
    indent = len(lines[first]) - len(lines[first].lstrip())
    last = first + 1
    while last < len(lines):
        line = lines[last]
        if line.strip() and not line.lstrip().startswith('#'):
            if len(line) - len(line.lstrip()) <= indent:
                break
        last += 1
    return first + 1, last


def _server_cpu(text, yaml, old, replacement):
    lines = text.splitlines(keepends=True)
    if yaml:
        first, last = _section(lines, '- name: server')
    else:
        first, last = _section(lines, 'Containers:')
        # kubectl child sections are indented; isolate only the main server.
        subsection = lines[first:last]
        a, b = _section(subsection, 'server:')
        first, last = first + a, first + b
    server = lines[first:last]
    a, b = _section(server, 'limits:' if yaml else 'Limits:')
    matches = [i for i in range(a, b) if re.fullmatch(r'[ \t]*cpu:[ \t]*' + re.escape(old) + r'[ \t]*\n?', server[i])]
    if len(matches) != 1:
        return text
    index = first + matches[0]
    lines[index] = re.sub(r'(?<=:)([ \t]*)' + re.escape(old) + r'(?=[ \t]*\n?$)',
                         lambda m: m.group(1) + replacement, lines[index])
    return ''.join(lines)


def historical_record(observation, transform, scope):
    if scope not in SCOPES:
        raise ValueError('Unknown fault scope')
    old, replacement = transform
    result = copy.deepcopy(observation)
    text = result['text']
    if scope == 'legacy-substring-v1':
        changed = text.replace(old, replacement)
    else:
        args = result['arguments']
        yaml = result['tool'] == 'GetAppYAML' and args.get('app_name') == 'adservice'
        pod = (result['tool'] == 'DescribeResource' and args.get('resource_type') == 'pod'
               and args.get('namespace') == 'boutique' and args.get('name', '').startswith('adservice-'))
        if not (yaml or pod):
            return result
        changed = (_server_cpu(text, yaml, old, replacement) if old == '200m'
                   else re.sub(r'(?<!\d)' + re.escape(old) + r'(?!\d)', replacement, text))
    if changed != text:
        if result.get('truncated'):
            raise ValueError('Cannot transform a truncated historical record')
        result['text'] = changed
        result['sha256'] = hashlib.sha256(changed.encode()).hexdigest()
    return result
