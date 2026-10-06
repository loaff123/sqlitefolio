"""Production summaries; independently reconstructed by verify.py."""
from collections import Counter
from .contract import canonical

LIMITATIONS = [
 'Trusted sample input only; guardrails are not a hostile-input security sandbox.',
 'Sample checks do not prove production safety or correctness for other data.',
 'Reopened state follows controlled close; no crash durability or rollback guarantee.',
 'Row deltas are common-column multiset projections, not inferred row identity.',
 'Structural verification checks consistency, not authorship or SQLite engine correctness.',
 'Source-bound replay is separate and uses the same qualified SQLite engine.',
 'Existing migration tools and competent SQLite recipes can obtain these same facts.',
]
EXIT = {'pass':0,'failed':1,'unsupported':2,'execution_error':3,'incomplete':4,'internal_error':5}


def comparison(before, after):
    if not before or not after or not before['complete'] or not after['complete']:
        return {'complete':False,'objects_added':None,'objects_removed':None,'objects_changed':None,'tables':{}}
    a = {f"{o['type']}:{o['name']}":o for o in before['objects']}
    b = {f"{o['type']}:{o['name']}":o for o in after['objects']}
    result = {'complete':True,'objects_added':sorted(b.keys()-a.keys()),
              'objects_removed':sorted(a.keys()-b.keys()),
              'objects_changed':sorted(k for k in a.keys() & b.keys() if a[k]!=b[k]),'tables':{}}
    for name in sorted(before['tables'].keys() | after['tables'].keys()):
        left=before['tables'].get(name);right=after['tables'].get(name)
        old=left['columns'] if left else [];new=right['columns'] if right else []
        common=[c for c in old if c in new]
        added=removed=None
        if left and right and common:
            def bag(table):
                indexes=[table['columns'].index(c) for c in common]
                return Counter(canonical([row[i] for i in indexes]) for row in table['rows'])
            first,last=bag(left),bag(right)
            removed=sum((first-last).values());added=sum((last-first).values())
        result['tables'][name]={'kind':'projection' if left and right else 'added' if right else 'removed',
            'common_columns':common,'columns_added':[c for c in new if c not in old],
            'columns_removed':[c for c in old if c not in new],
            'rows_before':len(left['rows']) if left else 0,'rows_after':len(right['rows']) if right else 0,
            'projected_added':added,'projected_removed':removed,
            'metadata_changed':not left or not right or any(left[k]!=right[k] for k in ('column_info','table_info','foreign_keys','indexes'))}
    return result


def _query_status(fact):
    if fact is None: return 'not_run'
    if fact['error']:
        kind=fact['error']['kind']
        return 'not_run' if kind=='not_run' else 'incomplete' if kind=='resource_limit' else 'error'
    return None if fact['complete'] else 'incomplete'


def _equal(left,right,order):
    if order=='ordered':return left==right
    return Counter(canonical(r) for r in left)==Counter(canonical(r) for r in right)


def check(invariant, raw, phase):
    kind=invariant['kind'];before=raw['before'];after=raw[phase]
    if kind.startswith('query_'):
        facts=raw['queries'].get(invariant['name'],{})
        observed=facts.get(phase);state=_query_status(observed)
        if state:return state
        if kind=='query_preserved':
            state=_query_status(facts.get('before'))
            if state:return state
            expected=facts['before']['rows']
        else:expected=invariant['expected']
        rows=observed['rows']
        if invariant.get('boolean') and rows not in ([[['integer','0']]],[[['integer','1']]]):return 'error'
        return 'pass' if _equal(expected,rows,invariant['order']) else 'fail'
    if not after or not after['complete']:return 'incomplete'
    if kind=='row_count':
        table=after['tables'].get(invariant['table'])
        if table is None:return 'error'
        expected=invariant['expected']
        if expected=='baseline':
            if not before or not before['complete']:return 'incomplete'
            original=before['tables'].get(invariant['table'])
            if original is None:return 'error'
            expected=len(original['rows'])
        passed=len(table['rows'])==expected
    elif kind.startswith('object_'):
        present=any(o['type']==invariant['object_type'] and o['name']==invariant['object'] for o in after['objects'])
        passed=present if kind=='object_present' else not present
    else:
        diagnostic=after['diagnostics']['foreign_key_check' if kind=='no_foreign_key_violations' else 'integrity_check']
        if diagnostic['error']:return 'error'
        passed=diagnostic['rows']==([] if kind=='no_foreign_key_violations' else [[['text','b2s=']]])
    return 'pass' if passed else 'fail'


def _diagnostics_pass(snapshot):
    return (all(v['ok'] for v in snapshot['views'].values())
            and snapshot['diagnostics']['foreign_key_check']['error'] is None
            and snapshot['diagnostics']['foreign_key_check']['rows']==[]
            and snapshot['diagnostics']['integrity_check']['error'] is None
            and snapshot['diagnostics']['integrity_check']['rows']==[[['text','b2s=']]])


def build_summary(manifest,candidates):
    results=[]
    for raw in candidates:
        execution=raw['execution']['status']
        complete=raw['source_preserved'] and all(raw[p] is not None and raw[p]['complete'] for p in ('before','observed','reopened'))
        observed=[{'name':i['name'],'status':check(i,raw,'observed')} for i in manifest['invariants']]
        reopened=[{'name':i['name'],'status':check(i,raw,'reopened')} for i in manifest['invariants']]
        statuses=[c['status'] for c in observed+reopened]
        if execution=='worker_error':status='internal_error'
        elif not complete or execution in ('resource_limit','source_changed') or any(s in ('incomplete','not_run') for s in statuses):status='incomplete'
        elif execution in ('sqlite_error','open_transaction'):status='execution_error'
        elif execution=='unsupported':status='unsupported'
        elif any(s!='pass' for s in statuses) or not all(_diagnostics_pass(raw[p]) for p in ('observed','reopened')):status='failed'
        else:status='pass'
        results.append({'name':raw['name'],'status':status,'execution':execution,'complete':complete,
             'observed_checks':observed,'reopened_checks':reopened,
             'observed_changes':comparison(raw['before'],raw['observed']),
             'reopened_changes':comparison(raw['before'],raw['reopened'])})
    return results


def exit_code(summary):
    return max((EXIT[s['status']] for s in summary),default=5)
