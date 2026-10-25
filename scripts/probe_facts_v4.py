"""Strict fixed-case probe extraction, canonical rendering, and one-field intervention.

This accepts the benchmark's block-YAML subset, not arbitrary YAML. Ambiguous or
unsupported forms fail closed instead of adding an unpinned parser dependency.
"""
import copy
import hashlib
import json
import re

FIELDS=('protocol','port','initialDelaySeconds','periodSeconds','failureThreshold')
PROBES=('liveness','readiness')
PROTOCOLS=('grpc','httpGet','tcpSocket')


class Ineligible(ValueError):
    """Observable schema cannot support this fixed intervention."""


def _indent(line): return len(line)-len(line.lstrip(' '))


def _field(lines,key,indent,required=False):
    quoted=re.compile(r'''["']'''+re.escape(key)+r'''["']\s*:''')
    if any(_indent(line)==indent and quoted.match(line.lstrip()) for line in lines):
        raise Ineligible('Unsupported quoted key: '+key)
    hits=[i for i,line in enumerate(lines) if _indent(line)==indent and re.match(re.escape(key)+r':(?:\s|$)',line.lstrip())]
    if len(hits)>1: raise Ineligible('Duplicate field: '+key)
    if not hits:
        if required: raise Ineligible('Missing field: '+key)
        return None,[]
    i=hits[0];value=lines[i].strip().split(':',1)[1].strip()
    end=i+1
    while end<len(lines) and (not lines[end].strip() or _indent(lines[end])>indent): end+=1
    return value,lines[i+1:end]


def _block(lines,key,indent):
    value,body=_field(lines,key,indent,True)
    if value and not value.startswith('#'): raise Ineligible('Expected block mapping: '+key)
    return body


def _scalar(value,kind):
    if value is None: return None
    value=value.split(' #',1)[0].strip()
    if value in ('null','~',''): return None
    if kind=='port' and re.fullmatch(r'''(['"])[A-Za-z0-9_-]+\1''',value): return value[1:-1]
    if value.lower() in ('true','false','yes','no','on','off'):
        raise Ineligible('Boolean scalar unsupported for '+kind)
    if re.fullmatch(r'\d+',value):
        result=int(value)
        if kind=='port' and not 1<=result<=65535: raise Ineligible('Port outside range')
        return result
    if kind=='port' and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*',value): return value
    raise Ineligible('Unsupported scalar for '+kind)


def _scalar_field(lines,key,indent,kind):
    value,body=_field(lines,key,indent)
    if any(line.strip() and not line.lstrip().startswith('#') for line in body):
        raise Ineligible('Expected scalar: '+key)
    return _scalar(value,kind)


def _container(template_spec):
    hits=[i for i,line in enumerate(template_spec) if _indent(line)==6 and line.strip()=='containers:']
    if len(hits)!=1: raise Ineligible('Missing/ambiguous containers block')
    after=template_spec[hits[0]+1:]
    nonempty=[line for line in after if line.strip() and not line.lstrip().startswith('#')]
    if not nonempty or not nonempty[0].lstrip().startswith('- name:'): raise Ineligible('Unsupported container list')
    indent=_indent(nonempty[0])
    if indent not in (6,8): raise Ineligible('Unsupported container indentation')
    body=[]
    for line in after:
        if line.strip() and (_indent(line)<6 or (_indent(line)==6 and not line.lstrip().startswith('- '))): break
        body.append(line)
    starts=[i for i,line in enumerate(body) if _indent(line)==indent and line.lstrip().startswith('- ')]
    if len(starts)!=1: raise Ineligible('Multiple/ambiguous containers')
    first=body[starts[0]].strip()
    if not re.fullmatch(r'- name:\s*[A-Za-z0-9_-]+',first): raise Ineligible('Unsupported container name')
    return first.split(':',1)[1].strip(),body[starts[0]+1:],indent+2


def parse_probe_source(observation,source_version,case='boutique/service/2'):
    """Return immutable-source canonical facts; raises Ineligible before model inference."""
    if case!='boutique/service/2': raise Ineligible('Unallocated case')
    if observation.get('tool')!='GetAppYAML' or observation.get('arguments',{}).get('app_name')!='adservice':
        raise Ineligible('Expected fixed adservice YAML')
    text=observation.get('text'); source_id=observation.get('id')
    if not isinstance(text,str) or not isinstance(source_id,str) or not source_id or not isinstance(source_version,str) or not source_version:
        raise Ineligible('Missing source identity/version')
    if 'source_version' in observation and observation['source_version']!=source_version:
        raise Ineligible('Conflicting source version')
    if observation.get('truncated') or '\t' in text: raise Ineligible('Partial source or unsupported indentation')
    source_hash=hashlib.sha256(text.encode()).hexdigest()
    if observation.get('sha256')!=source_hash: raise Ineligible('Source hash mismatch')
    # Aliases/anchors and flow maps can hide values outside this limited parser's scope.
    if re.search(r'(?m)^\s*[^#\n]+:\s*[&*!{\[|>]',text): raise Ineligible('Unsupported YAML alias/tag/flow/block scalar')
    documents=re.split(r'(?m)^---\s*$',text)
    matches=[]
    for n,doc in enumerate(documents):
        lines=doc.splitlines();kind,_=_field(lines,'kind',0)
        if kind!='Deployment': continue
        metadata=_block(lines,'metadata',0);name,_=_field(metadata,'name',2,True)
        if name=='adservice': matches.append((n,lines))
    if len(matches)!=1: raise Ineligible('Missing/ambiguous adservice Deployment')
    n,lines=matches[0]
    spec=_block(lines,'spec',0);template=_block(spec,'template',2);template_spec=_block(template,'spec',4)
    container,body,indent=_container(template_spec)
    facts={}
    for probe in PROBES:
        key=probe+'Probe';probe_body=_block(body,key,indent)
        if _field(probe_body,'exec',indent+2)[0] is not None:
            raise Ineligible('Unsupported exec handler')
        found=[protocol for protocol in PROTOCOLS if _field(probe_body,protocol,indent+2)[0] is not None]
        if len(found)!=1: raise Ineligible('Missing/ambiguous '+key+' protocol')
        protocol=found[0];protocol_body=_block(probe_body,protocol,indent+2)
        prefix=f'documents[{n}].spec.template.spec.containers[0].{key}'
        slot={'protocol':protocol,'port':_scalar_field(protocol_body,'port',indent+4,'port')}
        for field in FIELDS[2:]: slot[field]=_scalar_field(probe_body,field,indent+2,field)
        if any(slot[f] is not None and slot[f]<1 for f in ('periodSeconds','failureThreshold')):
            raise Ineligible('Nonpositive period/threshold')
        if protocol=='grpc' and isinstance(slot['port'],str): raise Ineligible('Named gRPC port unsupported')
        slot.update(source_id=source_id,source_version=source_version,source_hash=source_hash,
                    source_paths={f:prefix+('.'+protocol if f=='protocol' else '.'+protocol+'.port' if f=='port' else '.'+f) for f in FIELDS},
                    missing_fields=[f for f in FIELDS if slot[f] is None],
                    units={'port':'port number or name','initialDelaySeconds':'seconds','periodSeconds':'seconds per check','failureThreshold':'consecutive failure count'},
                    temporal_scope='static configuration; no observed restart duration implied')
        facts[probe]=slot
    if facts['liveness']['protocol'] not in ('grpc','httpGet'): raise Ineligible('Unallocated liveness protocol')
    return {'schema':'independent-probe-facts-v4','case':case,'container':container,'facts':{'adservice':facts}}


def expected_extraction(canonical):
    return {'service':'adservice','probes':{p:{f:canonical['facts']['adservice'][p][f] for f in FIELDS} for p in PROBES}}


def validate_extraction(model_output,canonical):
    """Allow external prose for archiving, but never forward it as canonical facts."""
    expected=expected_extraction(canonical); errors=[]
    if not isinstance(model_output,dict): return ['expected object']
    if model_output.get('service')!='adservice': errors.append('service mismatch')
    probes=model_output.get('probes')
    if not isinstance(probes,dict) or set(probes)!=set(PROBES): return errors+['probe slots missing/extra']
    for p in PROBES:
        values=probes[p]
        if not isinstance(values,dict) or set(values)!=set(FIELDS):
            errors.append(p+': fields missing/extra');continue
        for field,value in expected['probes'][p].items():
            if values[field]!=value or type(values[field]) is not type(value): errors.append(p+'.'+field+': source mismatch')
    return errors


def canonical_from_extraction(model_output,canonical):
    errors=validate_extraction(model_output,canonical)
    if errors: raise Ineligible('; '.join(errors))
    return copy.deepcopy(canonical)


def render_probe_facts(canonical):
    """Separate deterministic slots; no joint sentence or unsourced model prose."""
    return '\n'.join('adservice.'+p+': '+json.dumps(canonical['facts']['adservice'][p],sort_keys=True,separators=(',',':')) for p in PROBES)


def inject_liveness_protocol(canonical):
    before=canonical['facts']['adservice']['liveness']['protocol']
    if before not in ('grpc','httpGet'): raise Ineligible('Unallocated protocol')
    wrong=copy.deepcopy(canonical);wrong['facts']['adservice']['liveness']['protocol']='httpGet' if before=='grpc' else 'grpc'
    restored=copy.deepcopy(wrong);restored['facts']['adservice']['liveness']['protocol']=before
    assert restored==canonical
    encode=lambda value:json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    return wrong,{'seeded':True,'changed_paths':['facts.adservice.liveness.protocol'],
         'before':before,'after':wrong['facts']['adservice']['liveness']['protocol'],
         'before_sha256':hashlib.sha256(encode(canonical)).hexdigest(),'after_sha256':hashlib.sha256(encode(wrong)).hexdigest(),
         'other_fields_identical':True,'raw_source_references_unchanged':True,
         'interpretation':'constructed extraction error; original source remains authoritative'}
