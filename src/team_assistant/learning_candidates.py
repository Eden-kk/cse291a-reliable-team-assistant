"""Build an audited learning corpus and validate evidence-bound candidate proposals."""
import json
from pathlib import Path
from .memory import digest


def read(path):
    return json.loads(Path(path).read_text())


def corpus(paths, registry):
    sources, excluded, bindings = [], [], {}
    for path in paths:
        path=Path(path)
        manifest=read(path/'manifest.json')
        if manifest.get('split')!='learning' or manifest['case'] not in registry['splits']['learning']:
            raise ValueError('Candidate sources must belong to learning split')
        if manifest['dataset_commit']!=registry['dataset_commit']:
            raise ValueError('Dataset mismatch')
        result=read(path/'result.json') if (path/'result.json').exists() else {}
        score=read(path/'scores.json') if (path/'scores.json').exists() else {}
        audit=read(path/'semantic-audit.json') if (path/'semantic-audit.json').exists() else {}
        binding=digest({'manifest':manifest,'result':result,'scores':score})
        metrics=score.get('scores',{})
        if (metrics.get('JRA')!=1 or metrics.get('ECR')!=1
                or metrics.get('contract_valid') is not True or metrics.get('budget_valid') is not True
                or audit.get('supported') is not True or audit.get('evidence_sha256')!=binding
                or not audit.get('reviewer') or not audit.get('rationale')):
            excluded.append({'path':str(path),'reason':'Not a successful bound-audited learning episode'})
            continue
        ids={e['observation_id'] for e in result['answer']['evidence']}
        observations=[o for o in result['observations'] if o['id'] in ids]
        source_id=f'source-{len(sources)+1}'
        # No score/oracle or case paths enter the generation payload.
        sources.append({'source':source_id,'observations':observations})
        bindings[source_id]={'path':str(path),'evidence_sha256':binding}
    if not sources:
        raise ValueError('No eligible audited sources')
    if len(json.dumps(sources))>100000:
        raise ValueError('Corpus exceeds generation input cap; do not silently truncate')
    return {'sources':sources,'excluded':excluded,'bindings':bindings}


def validate_candidates(value, sources):
    proposals=value.get('candidates')
    if not isinstance(proposals,list) or not 1<=len(proposals)<=3:
        raise ValueError('Expected one to three supported proposals')
    observations={(s['source'],o['id']):o['text'] for s in sources for o in s['observations']}
    seen=set()
    for candidate in proposals:
        name=candidate.get('id')
        if not isinstance(name,str) or not name or name in seen:
            raise ValueError('Unique candidate identifier required')
        seen.add(name)
        rule=candidate.get('rule')
        if not isinstance(rule,str) or not 20<=len(rule)<=1000:
            raise ValueError('Bounded self-contained procedural rule required')
        supports=candidate.get('support')
        if not isinstance(supports,list) or not supports:
            raise ValueError('Candidate requires observed support')
        for evidence in supports:
            quote=evidence.get('quote')
            text=observations.get((evidence.get('source'),evidence.get('observation_id')))
            if not isinstance(quote,str) or not quote.strip() or text is None or quote not in text:
                raise ValueError('Support must quote an eligible source exactly')
    return {'rules':[p['rule'] for p in proposals]}
