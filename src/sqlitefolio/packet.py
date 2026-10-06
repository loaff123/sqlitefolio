"""Coordinator: preserved inputs, sequential workers, and review packets."""
from pathlib import Path
import tempfile
from .contract import InputError, canonical, read_json
from .paths import bounded_read, load_inputs, sha
from .publish import publish
from .report import render
from .runner import run_candidate, runtime_profile, runtime_supported
from .summary import LIMITATIONS, build_summary

MAX_PACKET = 32*1024*1024


class CapacityError(ValueError):
    """Coordinator refused a bounded result before publication."""


def build_packet(bundle, include_sample=False):
    if not runtime_supported():raise InputError('runtime outside qualified Linux x86_64 CPython 3.12 / SQLite 3.53.1 profile')
    if include_sample and bundle.source_kind != 'database':
        raise InputError('--include-sample requires an explicit database source in v1')
    results=[]
    with tempfile.TemporaryDirectory(prefix='sqlitefolio-source-') as temp:
        if bundle.database is not None:
            database=Path(temp)/'source.db';database.write_bytes(bundle.database)
            source={'database':str(database)}
        else:
            source={'schema_sql':bundle.files['inputs/schema.sql'].decode('utf-8'),
                    'seed_sql':bundle.files['inputs/seed.sql'].decode('utf-8')}
        for candidate in bundle.manifest['candidates']:
            request={'format':'sqlitefolio.worker.v1','source':source,
                     'candidate':{'name':candidate['name'],'sql':bundle.files[candidate['sql']].decode('utf-8')},
                     'profile':bundle.manifest['profile'],'invariants':bundle.manifest['invariants'],
                     'limits':bundle.manifest['limits']}
            results.append(run_candidate(request))
            if len(canonical(results))>MAX_PACKET:
                raise CapacityError('accumulated observations exceed packet ceiling; no packet published')
    if not bundle.unchanged():
        for result in results:result['source_preserved']=False
    files=dict(bundle.files)
    if include_sample:files['sample.db']=bundle.database
    packet={'format':'sqlitefolio.packet.v1','input':bundle.manifest,
            'input_sha256':sha(canonical(bundle.manifest)),
            'files':{name:sha(data) for name,data in sorted(files.items())},
            'source':{'kind':bundle.source_kind,'sha256':bundle.source_sha256,
                      'snapshot_sha256':sha(canonical(results[0]['before'])),'included':bool(include_sample)},
            'candidates':results,'summary':build_summary(bundle.manifest,results),'limitations':LIMITATIONS}
    if len(canonical(packet))>MAX_PACKET:raise CapacityError('packet JSON exceeds ceiling; no packet published')
    return packet,files


def rehearse(manifest_path, output, *, trust_input=False, include_sample=False):
    if not trust_input:raise InputError('explicit --trust-input required for original sample SQL')
    bundle=load_inputs(Path(manifest_path),Path(output))
    packet,files=build_packet(bundle,include_sample)
    # Independent semantic reconstruction occurs before publication.
    from .verify import reconstruct, verify_packet
    if reconstruct(packet['input'],packet['candidates']) != packet['summary']:
        raise RuntimeError('independent summary reconstruction disagreed; no packet published')
    files['packet.json']=canonical(packet)
    files['manifest.json']=canonical(packet['input'])
    try:files['report.html']=render(packet).encode('utf-8')
    except ValueError as exc:raise CapacityError(str(exc)) from exc
    publish(files,Path(output))
    verification=verify_packet(Path(output))
    return {'published':True,'verification':verification,'summary':packet['summary']}


def read_packet(path):
    data,_=bounded_read(Path(path)/'packet.json',MAX_PACKET)
    if len(data)>MAX_PACKET:raise InputError('packet exceeds byte ceiling')
    return read_json(data)


def replay(path, manifest_path, *, trust_input=False):
    if not trust_input:raise InputError('explicit --trust-input required for source-bound replay')
    from .verify import verify_packet
    verified=verify_packet(Path(path))
    if not verified['valid']:return {'matched':False,'exit_code':6,'message':verified['message']}
    raw,_=bounded_read(Path(path)/'packet.json',MAX_PACKET)
    if sha(raw)!=verified['packet_sha256']:
        return {'matched':False,'exit_code':6,'message':'packet changed after structural verification; no SQL replayed'}
    old=read_json(raw)
    bundle=load_inputs(Path(manifest_path))
    expected_files={k:v for k,v in old['files'].items() if k not in ('inputs/original-manifest.json','sample.db')}
    supplied_files={k:sha(v) for k,v in bundle.files.items() if k!='inputs/original-manifest.json'}
    if (old['input']!=bundle.manifest or old['source']['sha256']!=bundle.source_sha256 or expected_files!=supplied_files
            or any(c['runtime']!=runtime_profile() for c in old['candidates'])):
        return {'matched':False,'exit_code':6,'message':'source, contract, exact SQL, or runtime binding mismatch; no SQL replayed'}
    fresh,_=build_packet(bundle)
    matched=fresh['candidates']==old['candidates'] and fresh['summary']==old['summary']
    return {'matched':matched,'exit_code':verified['exit_code'] if matched else 6,
            'message':'source-bound replay matched raw facts on the same engine' if matched else 'source-bound replay raw facts differ'}
