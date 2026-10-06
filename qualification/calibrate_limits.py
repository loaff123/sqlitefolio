"""Original benign resource qualification; no performance superiority claim."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

CASES = {
    'row_boundary': (10000, 1, 30, 'completed'),
    'row_overflow': (10001, 1, 30, 'resource_limit'),
    'value_boundary': (1, 65536, 30, 'completed'),
    'value_overflow': (1, 65537, 30, 'resource_limit'),
    'evidence_near_bound': (40, 65536, 30, 'completed'),
    'evidence_overflow': (200, 65536, 30, 'resource_limit'),
    'worker_wall': (1, 1, 1, 'resource_limit'),
}


def one(name):
    from sqlitefolio.runner import run_candidate, runtime_profile
    count,size,wall,expected=CASES[name]
    seed=f'WITH RECURSIVE c(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM c WHERE n<{count}) INSERT INTO t SELECT zeroblob({size}) FROM c;'
    invariants=[]
    if name=='worker_wall':
        invariants=[{'name':str(i),'kind':'query_preserved','order':'ordered','sql':'WITH RECURSIVE c(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM c WHERE n<50000) SELECT sum(n) FROM c'} for i in range(64)]
    request={'format':'sqlitefolio.worker.v1','source':{'schema_sql':'CREATE TABLE t(x);','seed_sql':seed},'candidate':{'name':'calibration','sql':'SELECT 1;'},'profile':{'foreign_keys':True,'transaction_mode':'autocommit'},'invariants':invariants,'limits':{'rows_per_query':10000,'vm_steps':2000000,'wall_seconds':wall,'evidence_bytes':16777216}}
    start=time.monotonic();result=run_candidate(request);elapsed=time.monotonic()-start
    status=result['execution']['status']
    return {'case':name,'status':status,'expected':expected,'matched':status==expected,
            'snapshot_complete':{p:None if result[p] is None else result[p]['complete'] for p in ('before','observed','reopened')},
            'elapsed_seconds':round(elapsed,6),'worker_peak_rss_kib':resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
            'error':result['execution']['error'],'runtime':runtime_profile()}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--case',choices=CASES);parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.case:
        print(json.dumps(one(args.case),sort_keys=True));return 0
    results=[]
    for name in CASES:
        run=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--case',name],capture_output=True,text=True,timeout=40)
        if run.returncode:raise RuntimeError(run.stderr)
        results.append(json.loads(run.stdout))
    document={'purpose':'Bound calibration on original benign samples; not a throughput benchmark','results':results,'all_expected':all(r['matched'] for r in results)}
    text=json.dumps(document,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    else:print(text)
    return 0 if document['all_expected'] else 1


if __name__=='__main__':raise SystemExit(main())
