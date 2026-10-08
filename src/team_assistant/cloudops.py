"""Read-only snapshot adapter; oracle fields never enter its model-facing interface."""
import contextlib
import hashlib
import importlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import types

from .run_gate import authorize_case

ALLOWED = {'GetResources', 'DescribeResource', 'GetAppYAML', 'GetRecentLogs',
           'GetErrorLogs', 'GetServiceDependencies', 'CheckServiceConnectivity',
           'GetClusterConfiguration', 'GetAlerts', 'CheckNodeServiceStatus'}


def load_vendor(vendor):
    path = Path(vendor).resolve() / 'agents/cloudops_agent'
    name = '_cloudops_' + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = [str(path)]
        sys.modules[name] = package
    return name


class Snapshot:
    def __init__(self, vendor, case, registry, code_commit, freeze=None, artifact_root=None):
        self.split = authorize_case(case, registry, code_commit, freeze, artifact_root)
        if not re.fullmatch(r'boutique/(runtime|service|performance)/[1-9][0-9]*', case):
            raise ValueError('Unsupported case path')
        self.vendor = Path(vendor).resolve()
        revision = subprocess.check_output(['git', '-C', str(self.vendor), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != registry['dataset_commit']:
            raise ValueError('Dataset revision mismatch')
        self.path = self.vendor / 'benchmark' / case
        self.files = [self.path / 'tool_cache.json', self.path / 'raw_data/logs.json']
        for path in self.files + [self.path / 'metadata.json']:
            if path.exists() and (path.is_symlink() or not path.resolve().is_relative_to(self.path.resolve())):
                raise ValueError('Snapshot path escape')
        dirty = subprocess.check_output(['git', '-C', str(self.vendor), 'diff', 'HEAD', '--name-only', '--',
                                         'agents/cloudops_agent', str(self.path)], text=True)
        if dirty:
            raise ValueError('Vendor code or selected snapshot has uncommitted changes')
        metadata = json.loads((self.path / 'metadata.json').read_text())
        self.question = metadata['query']
        if not isinstance(self.question, str):
            raise ValueError('Question must be text')
        self.cache_keys = set(json.loads((self.path / "tool_cache.json").read_text()))
        self.source_version = self.fingerprint()
        module = importlib.import_module(load_vendor(self.vendor) + '.tools.cloudops')
        with contextlib.redirect_stdout(io.StringIO()):
            self.tools = {t.name: t for t in module.create_k8s_tools(str(self.path), system='boutique') if t.name in ALLOWED}
        self.observations = []

    def fingerprint(self):
        return hashlib.sha256(b'\0'.join(p.read_bytes() if p.exists() else b'MISSING' for p in self.files)).hexdigest()

    def specifications(self):
        return {name: {'description': tool.description,
                       'parameters': tool.args_schema.model_json_schema() if tool.args_schema else {'type': 'object', 'properties': {}}}
                for name, tool in self.tools.items()}

    def query(self, name, arguments):
        if self.fingerprint() != self.source_version:
            raise ValueError('Snapshot changed; reopen adapter before issuing queries')
        if name not in self.tools or not isinstance(arguments, dict):
            raise ValueError('Tool outside read-only allowlist or invalid arguments')
        tool = self.tools[name]
        fields = tool.args_schema.model_fields if tool.args_schema else {}
        if set(arguments) - set(fields):
            raise ValueError('Unknown tool arguments')
        args = tool.args_schema.model_validate(arguments, strict=True).model_dump() if tool.args_schema else {}
        if args.get('namespace') not in (None, 'boutique', 'kube-system'):
            raise ValueError('Namespace outside benchmark scope')
        for key, value in args.items():
            if isinstance(value, str) and ('/' in value or '..' in value or '\\' in value):
                raise ValueError('Path-like tool argument')
        if args.get('show_labels') and args.get('output_wide'):
            raise ValueError('Mutually exclusive output options')
        if 'port' in args and not 1 <= args['port'] <= 65535:
            raise ValueError('Port outside TCP range')
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            raw = tool._run(**args)
        missing = name == 'CheckServiceConnectivity' and not any(
            line in self.cache_keys for line in capture.getvalue().splitlines())
        if missing:
            raw = 'Snapshot unavailable: this connectivity query has no recorded measurement.' 
        text = raw if isinstance(raw, str) else json.dumps(raw)
        record = {'id': f'observation-{len(self.observations) + 1}', 'tool': name,
                  'arguments': args, 'availability': 'missing' if missing else ('error' if text.lstrip().lower().startswith(('error:', 'an unexpected error')) else 'returned'), 'text': text[:10000], 'raw_text': text,
                  'truncated': len(text) > 10000, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                  'source_version': self.source_version}
        self.observations.append(record)
        return self.visible(record)

    @staticmethod
    def visible(record):
        return {k: v for k, v in record.items() if k not in {'raw_text', 'source_version'}}
