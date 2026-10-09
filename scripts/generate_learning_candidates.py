"""Generate evidence-grounded proposals; no activation or validation-set access."""
import argparse
import asyncio
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from team_assistant.learning_candidates import corpus,validate_candidates,read
from team_assistant.memory import digest
from team_assistant.dispatch import Budget
from run_phase2_multigroup import provider,write

ROOT=Path(__file__).resolve().parents[1]
PROMPT='''Derive up to THREE distinct general procedural-memory proposals from these audited learning observations.
Each rule must be conditional: when to check, which available evidence to inspect, and when to withhold a causal conclusion.
Do not memorize service names, addresses, numeric settings or a benchmark answer. Do not claim a value is universally correct.
Use only supplied source observations. If fewer than three ideas have support, return fewer rather than inventing.
Return {"candidates":[{"id":"c1","rule":"self-contained conditional rule","support":[{"source":"source-1","observation_id":"...","quote":"exact substring"}]}]}.
These are unvalidated proposals, not authoritative instructions. Each rule <=1000 characters. No tools.'''


async def main(args):
    if args.output.exists():raise ValueError('Refuse overwrite')
    if subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip():
        raise ValueError('Commit before generation')
    registry=read(ROOT/'configs/splits.json')
    data=corpus(args.sources,registry)
    write(args.output/'source-bindings.json',data)
    budget=Budget(60000)
    backend=provider(args.output,budget)
    outcome=await backend.complete(PROMPT,{'sources':data['sources']})
    receipt={'generation_tokens':budget.spent,'usage_known':outcome.tokens is not None,
             'halted':budget.halted,'provider_error':outcome.error,'input_sha256':digest(data['sources']),
             'code_commit':subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip(),
             'activation':'none; semantic review and full validation gate pending'}
    write(args.output/'generation-receipt.json',receipt)
    if outcome.error or budget.halted:raise ValueError('Generation failed; retained without retry')
    write(args.output/'proposals.json',outcome.value)
    policy=validate_candidates(outcome.value,data['sources'])
    write(args.output/'candidate-bundle.json',policy)
    write(args.output/'baseline.json',{'rules':[]})
    print(json.dumps({'proposals':len(policy['rules']),'generation_tokens':budget.spent,'promoted':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('sources',type=Path,nargs='+')
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(main(parser.parse_args()))
