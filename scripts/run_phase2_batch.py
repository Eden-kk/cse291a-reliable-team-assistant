"""Fixed development repetitions under one physical cap including the smoke trial."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def physical_usage(directory, reservation):
    charged, unknown = 0, 0
    for request in directory.glob('**/calls/*.request.json'):
        response = request.with_name(request.name.replace('.request.json', '.json'))
        tokens = json.loads(response.read_text()).get('tokens') if response.exists() else None
        known = isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0
        charged += tokens if known else reservation
        unknown += not known
    return charged, unknown


def run(output, smoke, vendor, protocol_path):
    if output.exists():
        raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip():
        raise ValueError('Commit experiment before batch')
    protocol = json.loads(protocol_path.read_text())
    smoke_manifest = json.loads((smoke/'manifest.json').read_text())
    if smoke_manifest.get('fault_scope', 'legacy-substring-v1') != protocol.get('fault_scope', 'legacy-substring-v1'):
        raise ValueError('Smoke and batch must use the same fault scope; do not reuse legacy smoke for v2')
    smoke_summary = json.loads((smoke/'summary.json').read_text())
    if smoke_summary['physical_halted'] or any(a['state'] != 'completed' for a in smoke_summary['arms'].values()):
        raise ValueError('Smoke must complete before repeating')
    spent = smoke_summary['physical_tokens']
    cap = protocol['physical_charged_token_cap_per_batch']
    output.mkdir(parents=True)
    windows = [{'case': case['case'], 'repeat': repeat+1,
                'order': protocol['recovery_orders'][repeat % len(protocol['recovery_orders'])]}
               for case in protocol['cases'] for repeat in range(protocol['live_repetitions_after_smoke'])]
    manifest = {'code_commit': subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip(),
                'protocol_sha256': hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
                'physical_cap_including_smoke': cap, 'smoke': str(smoke), 'smoke_tokens': spent,
                'smoke_not_pooled_as_formal_repeat': True, 'planned_windows': windows}
    (output/'manifest.json').write_text(json.dumps(manifest, indent=2))
    rows, halted = [], False
    for index, window in enumerate(windows):
        directory = output / ('window-' + str(index+1))
        remaining = cap-spent
        row = {**window, 'path': str(directory)}
        if halted or remaining < protocol['call_reservation']:
            row.update(state='not_run', reason='shared physical budget halted or cannot reserve')
        else:
            command = [sys.executable, str(ROOT/'scripts/run_phase2_revision.py'),
                       '--output', str(directory), '--vendor', str(vendor), '--case', window['case'],
                       '--fault-scope', protocol.get('fault_scope', 'legacy-substring-v1'),
                       '--repeat', str(window['repeat']), '--order', *window['order'],
                       '--tokens', str(protocol['logical_total_token_cap']),
                       '--tools', str(protocol['logical_total_tool_attempt_cap']),
                       '--physical-tokens', str(remaining)]
            code = subprocess.run(command, cwd=ROOT).returncode
            row['exit_code'] = code
            actual_charge, unknown = physical_usage(directory, protocol['call_reservation'])
            spent += actual_charge
            row.update(physical_tokens=actual_charge, unknown_usage_calls=unknown)
            summary_path = directory/'summary.json'
            if summary_path.exists():
                summary = json.loads(summary_path.read_text())
                row['arms'] = summary['arms']
                ledger_matches = actual_charge == summary['physical_tokens']
                row['ledger_matches'] = ledger_matches
                halted = bool(code or unknown or not ledger_matches or summary['physical_halted'] or spent > cap)
                row['state'] = 'completed' if not code and all(a['state']=='completed' for a in summary['arms'].values()) else 'partial_or_failed'
            else:
                row.update(state='failed', reason='No settled summary; journal reservations charged; batch stopped')
                halted = True
        rows.append(row)
        report = {'windows': rows, 'spent_including_smoke': spent, 'physical_cap': cap,
                  'halted': halted, 'all_windows_accounted': len(rows)==len(windows)}
        (output/'summary.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({'window': index+1, 'state': row['state'], 'spent_including_smoke': spent, 'halted': halted}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=ROOT/'configs/phase2/protocol-v2.json')
    parser.add_argument('--vendor', type=Path, required=True)
    args = parser.parse_args()
    run(args.output, args.smoke, args.vendor, args.protocol)
