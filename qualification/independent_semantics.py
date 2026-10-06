"""Independent, trusted-fixture SQLite qualification, deliberately not a product helper.

Only imports SQLiteFolio at the runner/summary API boundaries in ``qualify``.
All observations, typed values, checks and multiset changes below are independently
implemented using Python's sqlite3 API. It is not a hostile-SQL sandbox.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PHASES = ('before', 'observed', 'reopened')
LIMITS = {'rows_per_query': 10000, 'vm_steps': 2000000, 'wall_seconds': 30,
          'evidence_bytes': 16777216}


class TextBytes(bytes):
    """Driver-boundary marker distinguishes raw TEXT bytes from BLOB bytes."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cell(value):
    if value is None:
        return ['null', None]
    if isinstance(value, int):
        return ['integer', str(value)]
    if isinstance(value, float):
        return ['real', struct.pack('>d', value).hex()]
    if isinstance(value, str):
        return ['text', base64.b64encode(value.encode('utf-8')).decode('ascii')]
    if isinstance(value, TextBytes):
        return ['text', base64.b64encode(value).decode('ascii')]
    if isinstance(value, bytes):
        return ['blob', base64.b64encode(value).decode('ascii')]
    raise TypeError(type(value).__name__)


def typed(rows):
    return [[cell(value) for value in row] for row in rows]


def metadata(value):
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='strict')
    return value


def native_rows(connection, sql):
    return [[metadata(value) for value in row] for row in connection.execute(sql)]


def ident(name):
    return '"' + name.replace('"', '""') + '"'


def sql_error(exc):
    return {'kind': 'sqlite_error', 'message': str(exc),
            'sqlite_code': getattr(exc, 'sqlite_errorcode', None),
            'sqlite_name': getattr(exc, 'sqlite_errorname', None)}


def query(connection, sql, order='ordered'):
    try:
        rows = typed(connection.execute(sql).fetchall())
        return {'rows': rows, 'error': None, 'complete': True}
    except sqlite3.Error as exc:
        return {'rows': None, 'error': sql_error(exc), 'complete': False}


def snapshot(connection):
    declarations = native_rows(connection,
        'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name')
    objects = [dict(zip(('type', 'name', 'table', 'sql'), row)) for row in declarations]
    flags = {row[1]: row for row in native_rows(connection, 'PRAGMA table_list') if row[0] == 'main'}
    tables, views = {}, {}
    for kind, name, _, _ in declarations:
        quoted = ident(name)
        if kind == 'table':
            columns = native_rows(connection, 'PRAGMA table_xinfo(' + quoted + ')')
            indexes = native_rows(connection, 'PRAGMA index_list(' + quoted + ')')
            indexes.sort(key=lambda row: row[1])
            rows = typed(connection.execute('SELECT * FROM ' + quoted).fetchall())
            rows.sort(key=canonical)
            tables[name] = {
                'columns': [row[1] for row in columns],
                'column_info': columns, 'table_info': flags[name],
                'foreign_keys': native_rows(connection, 'PRAGMA foreign_key_list(' + quoted + ')'),
                'indexes': [{'info': row, 'columns': native_rows(connection,
                    'PRAGMA index_xinfo(' + ident(row[1]) + ')')} for row in indexes],
                'rows': rows,
            }
        elif kind == 'view':
            fact = query(connection, 'SELECT * FROM ' + quoted + ' LIMIT 0')
            views[name] = {'ok': fact['complete'], 'error': fact['error']}
    diagnostics = {}
    for diagnostic in ('foreign_key_check', 'integrity_check'):
        fact = query(connection, 'PRAGMA ' + diagnostic)
        diagnostics[diagnostic] = {'rows': fact['rows'], 'error': fact['error']}
    diagnostics['foreign_keys'] = bool(connection.execute('PRAGMA foreign_keys').fetchone()[0])
    sequences = sorted(query(connection, 'SELECT name,seq FROM sqlite_sequence')['rows'], key=canonical) if 'sqlite_sequence' in tables else []
    return {'complete': True, 'error': None, 'objects': objects, 'tables': tables,
            'views': views, 'diagnostics': diagnostics, 'sequences': sequences}


def runtime(connection):
    return {'python': platform.python_version(), 'python_build': sys.version,
            'implementation': platform.python_implementation(), 'machine': platform.machine(),
            'sqlite': sqlite3.sqlite_version,
            'sqlite_source_id': native_rows(connection, 'SELECT sqlite_source_id()')[0][0],
            'compile_options': sorted(row[0] for row in native_rows(connection, 'PRAGMA compile_options')),
            'platform': sys.platform}


def connect(path, foreign_keys):
    connection = sqlite3.connect(path, isolation_level=None)
    connection.text_factory = TextBytes
    connection.execute('PRAGMA foreign_keys=' + str(int(foreign_keys)))
    return connection


def directory_fingerprint(directory):
    return {str(path.relative_to(directory)): {'type': 'directory'} if path.is_dir() else
            {'type': 'file', 'sha256': sha(path.read_bytes()), 'bytes': path.stat().st_size}
            for path in sorted(directory.rglob('*'))}


def reference(request, observations=()):
    """Execute original scripts once, inspect live state, close, then reopen."""
    fk = request['profile']['foreign_keys']
    with tempfile.TemporaryDirectory(prefix='sqlitefolio-native-reference-') as folder:
        target = Path(folder) / 'copy.db'
        db = connect(target, fk)
        if 'database' in request['source']:
            path = Path(request['source']['database'])
            before_digest = sha(path.read_bytes())
            source = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True,
                                     isolation_level=None)
            source.backup(db)
            source.close()
        else:
            db.executescript(request['source']['schema_sql'])
            db.executescript(request['source']['seed_sql'])
            assert not db.in_transaction, 'Qualification source must be committed'
            db.close()
            db = connect(target, fk)
        runtime_facts = runtime(db)
        before = snapshot(db)
        query_specs = [i for i in request['invariants'] if i['kind'] in ('query_equals', 'query_preserved')]
        queries = {i['name']: {'before': query(db, i['sql'], i['order'])} for i in query_specs}
        fk_start = bool(db.execute('PRAGMA foreign_keys').fetchone()[0])
        error = None
        try:
            db.executescript(request['candidate']['sql'])
        except sqlite3.Error as exc:
            error = sql_error(exc)
        execution = {'status': 'sqlite_error' if error else 'open_transaction' if db.in_transaction else 'completed',
                     'error': error, 'in_transaction': db.in_transaction,
                     'foreign_keys_start': fk_start,
                     'foreign_keys_end': bool(db.execute('PRAGMA foreign_keys').fetchone()[0])}
        observed = snapshot(db)
        for invariant in query_specs:
            queries[invariant['name']]['observed'] = query(db, invariant['sql'], invariant['order'])
        auxiliary = {item['name']: query(db, item['sql']) for item in observations if item['connection'] == 'live'}
        db.close()
        reopened = connect(target, fk)
        reopened_snapshot = snapshot(reopened)
        for invariant in query_specs:
            queries[invariant['name']]['reopened'] = query(reopened, invariant['sql'], invariant['order'])
        auxiliary.update({item['name']: query(reopened, item['sql']) for item in observations if item['connection'] == 'persisted'})
        reopened.close()
        source_preserved = 'database' not in request['source'] or before_digest == sha(path.read_bytes())
        result = {'name': request['candidate']['name'], 'runtime': runtime_facts,
                'profile': dict(request['profile']), 'execution': execution,
                'before': before, 'observed': observed, 'reopened': reopened_snapshot,
                'queries': queries, 'source_preserved': source_preserved}
        if observations:
            result['_auxiliary'] = auxiliary
        return result


def comparison(before, after):
    if before is None or after is None or not before['complete'] or not after['complete']:
        return {'complete': False, 'objects_added': None, 'objects_removed': None,
                'objects_changed': None, 'tables': {}}
    left = {obj['type'] + ':' + obj['name']: obj for obj in before['objects']}
    right = {obj['type'] + ':' + obj['name']: obj for obj in after['objects']}
    result = {'complete': True, 'objects_added': sorted(right.keys() - left.keys()),
              'objects_removed': sorted(left.keys() - right.keys()),
              'objects_changed': sorted(k for k in left.keys() & right.keys() if left[k] != right[k]),
              'tables': {}}
    for name in sorted(before['tables'].keys() | after['tables'].keys()):
        old = before['tables'].get(name)
        new = after['tables'].get(name)
        oldcols, newcols = (old or {}).get('columns', []), (new or {}).get('columns', [])
        common = [column for column in oldcols if column in newcols]
        entry = {'kind': 'added' if old is None else 'removed' if new is None else 'projection',
                 'common_columns': common,
                 'columns_added': [c for c in newcols if c not in oldcols],
                 'columns_removed': [c for c in oldcols if c not in newcols],
                 'rows_before': len(old['rows']) if old else 0,
                 'rows_after': len(new['rows']) if new else 0,
                 'projected_added': None, 'projected_removed': None,
                 'metadata_changed': old is None or new is None or any(old[k] != new[k]
                    for k in ('column_info', 'table_info', 'foreign_keys', 'indexes'))}
        if old and new and common:
            oldindex, newindex = [oldcols.index(c) for c in common], [newcols.index(c) for c in common]
            oldbag = Counter(canonical([row[c] for c in oldindex]) for row in old['rows'])
            newbag = Counter(canonical([row[c] for c in newindex]) for row in new['rows'])
            entry['projected_added'] = sum((newbag - oldbag).values())
            entry['projected_removed'] = sum((oldbag - newbag).values())
        result['tables'][name] = entry
    return result


def check(invariant, candidate, phase):
    kind = invariant['kind']
    snap, baseline = candidate[phase], candidate['before']
    if kind in ('query_equals', 'query_preserved'):
        facts = candidate['queries'][invariant['name']]
        observed = facts[phase]
        if not observed['complete']:
            return 'not_run' if (observed['error'] or {}).get('kind') == 'not_run' else 'error'
        wanted = invariant.get('expected')
        if kind == 'query_preserved':
            if not facts['before']['complete']:
                return 'error'
            wanted = facts['before']['rows']
        if invariant.get('boolean', False):
            allowed = [[[['integer', '0']]], [[['integer', '1']]]]
            if observed['rows'] not in allowed:
                return 'error'
        left, right = observed['rows'], wanted
        if invariant['order'] == 'bag':
            left, right = sorted(left, key=canonical), sorted(right, key=canonical)
        return 'pass' if left == right else 'fail'
    if snap is None or not snap['complete']:
        return 'incomplete'
    if kind == 'row_count':
        table = invariant['table']
        if table not in snap['tables']:
            return 'error'
        expected = invariant['expected']
        if expected == 'baseline':
            if baseline is None or not baseline['complete']:
                return 'incomplete'
            if table not in baseline['tables']:
                return 'error'
            expected = len(baseline['tables'][table]['rows'])
        return 'pass' if len(snap['tables'][table]['rows']) == expected else 'fail'
    if kind in ('object_present', 'object_absent'):
        present = any(o['type'] == invariant['object_type'] and o['name'] == invariant['object'] for o in snap['objects'])
        return 'pass' if present == (kind == 'object_present') else 'fail'
    diagnostic = snap['diagnostics']['foreign_key_check' if kind == 'no_foreign_key_violations' else 'integrity_check']
    if diagnostic['error']:
        return 'error'
    expected = [] if kind == 'no_foreign_key_violations' else [[['text', 'b2s=']]]
    return 'pass' if diagnostic['rows'] == expected else 'fail'


def summary(candidate, invariants):
    checks = {phase: [{'name': i['name'], 'status': check(i, candidate, phase)} for i in invariants]
              for phase in ('observed', 'reopened')}
    complete = candidate['source_preserved'] and all(candidate[p] is not None and candidate[p]['complete'] for p in PHASES)
    execution = candidate['execution']['status']
    bad_diagnostics = any(snap and (any(not v['ok'] for v in snap['views'].values())
        or snap['diagnostics']['foreign_key_check']['rows'] != []
        or snap['diagnostics']['integrity_check']['rows'] != [[['text', 'b2s=']]])
        for snap in (candidate['observed'], candidate['reopened']))
    if execution == 'worker_error':
        status = 'internal_error'
    elif not complete or not candidate['source_preserved'] or execution in ('resource_limit', 'source_changed'):
        status = 'incomplete'
    elif execution in ('sqlite_error', 'open_transaction'):
        status = 'execution_error'
    elif execution == 'unsupported':
        status = 'unsupported'
    elif bad_diagnostics or any(c['status'] != 'pass' for phase in checks.values() for c in phase):
        status = 'failed'
    else:
        status = 'pass'
    return {'name': candidate['name'], 'status': status, 'execution': execution, 'complete': complete,
            'observed_checks': checks['observed'], 'reopened_checks': checks['reopened'],
            'observed_changes': comparison(candidate['before'], candidate['observed']),
            'reopened_changes': comparison(candidate['before'], candidate['reopened'])}


def request_for(case, source=None):
    invariants = case.get('invariants')
    if invariants is None:
        invariants = [{'name': c['name'], 'kind': 'query_equals', 'sql': c['sql'],
                       'order': 'ordered', 'expected': typed(c['expect_rows'])} for c in case['checks']]
    return {'format': 'sqlitefolio.worker.v1',
            'source': source or {'schema_sql': case['schema_sql'], 'seed_sql': case['seed_sql']},
            'candidate': {'name': case['id'], 'sql': case['migration_sql']},
            'profile': {'foreign_keys': case['foreign_keys'], 'transaction_mode': 'autocommit'},
            'invariants': invariants, 'limits': dict(LIMITS)}


def validate_freeze(directory):
    frozen = json.loads((directory / 'FROZEN_SHA256.json').read_text())
    mismatches = [name for name, digest in frozen['sha256'].items()
                  if not (directory / name).is_file() or sha((directory / name).read_bytes()) != digest]
    cases = [json.loads(path.read_text()) for path in sorted(directory.glob('[0-9][0-9]_*.json'))]
    return {'case_count': len(cases), 'check_count': sum(len(c['checks']) for c in cases),
            'mismatches': mismatches, 'frozen_sha256': sha((directory / 'FROZEN_SHA256.json').read_bytes()),
            'files': {p.name: sha(p.read_bytes()) for p in sorted(directory.iterdir()) if p.is_file()}}


def prediction_errors(candidate, case, prediction, auxiliary=None):
    result = []
    statuses = {'applied': 'completed', 'error': 'sqlite_error', 'open_transaction': 'open_transaction'}
    execution = candidate['execution']
    for key, wanted in [('status', statuses[prediction['status']]),
                        ('in_transaction', prediction['in_transaction']),
                        ('foreign_keys_end', prediction['foreign_keys_end'])]:
        if execution[key] != wanted:
            result.append('execution.' + key)
    if prediction['error_contains'] and prediction['error_contains'] not in (execution['error'] or {}).get('message', ''):
        result.append('execution.error')
    for spec, wanted in zip(request_for(case)['invariants'], prediction['check_outcomes'], strict=True):
        actual = candidate['queries'][wanted['name']]['observed']
        if 'actual_rows' in wanted and actual['rows'] != typed(wanted['actual_rows']):
            result.append(wanted['name'] + '.rows')
        if 'error_contains' in wanted and wanted['error_contains'] not in (actual['error'] or {}).get('message', ''):
            result.append(wanted['name'] + '.error')
        if (check(spec, candidate, 'observed') == 'pass') != wanted['passed']:
            result.append(wanted['name'] + '.passed')
    if auxiliary is not None:
        for wanted in prediction['observations']:
            actual = auxiliary[wanted['name']]
            if 'expect_rows' in wanted and actual['rows'] != typed(wanted['expect_rows']):
                result.append(wanted['name'] + '.auxiliary_rows')
            if 'error_contains' in wanted and wanted['error_contains'] not in (actual['error'] or {}).get('message', ''):
                result.append(wanted['name'] + '.auxiliary_error')
    return result


def run_frozen_reference(path):
    case = json.loads(path.read_text())
    prediction = json.loads((path.parent / 'expected.json').read_text())['cases'][case['id']]
    request = request_for(case)
    facts = reference(request, prediction['observations'])
    auxiliary = facts.pop('_auxiliary')
    errors = prediction_errors(facts, case, prediction, auxiliary)
    return {'reference': facts, 'expected_summary': summary(facts, request['invariants']),
            'prediction_matches': not errors, 'prediction_errors': errors, 'auxiliary_observations': auxiliary}


def additional_cases():
    directory = Path(__file__).parent
    freeze = json.loads((directory / 'ADDITIONAL_FREEZE.json').read_text())
    data = (directory / freeze['file']).read_bytes()
    assert sha(data) == freeze['sha256'], 'Independent additional fixtures changed'
    return json.loads(data)['cases']


def run_additional_references():
    return {case['id']: {'reference': facts, 'expected_summary': summary(facts, request['invariants'])}
            for case in additional_cases() for request in [request_for(case)] for facts in [reference(request)]}


def additional_prediction_errors(case, observed_summary):
    wanted = case['predicted']
    errors = []
    for key in ('execution', 'status'):
        if observed_summary[key] != wanted[key]:
            errors.append(key)
    for key in ('observed_checks', 'reopened_checks'):
        if [c['status'] for c in observed_summary[key]] != wanted[key]:
            errors.append(key)
    for table, facts in wanted['tables'].items():
        for key, value in facts.items():
            if observed_summary['observed_changes']['tables'][table][key] != value:
                errors.append(table + '.' + key)
    return errors


def validate_additional_predictions():
    cases = additional_cases()
    references = run_additional_references()
    return {'case_count': len(cases), 'mismatches': [case['id'] + ': ' + error
        for case in cases for error in additional_prediction_errors(case, references[case['id']]['expected_summary'])]}


def differences(left, right, location='$'):
    if type(left) is not type(right):
        return [location + ': type ' + type(left).__name__ + ' != ' + type(right).__name__]
    if isinstance(left, dict):
        results = [location + '.' + k + ': key missing' for k in sorted(left.keys() ^ right.keys())]
        for key in sorted(left.keys() & right.keys()):
            results.extend(differences(left[key], right[key], location + '.' + key))
        return results
    if isinstance(left, list):
        if len(left) != len(right):
            return [location + ': list length ' + str(len(left)) + ' != ' + str(len(right))]
        return [error for index, (a, b) in enumerate(zip(left, right))
                for error in differences(a, b, location + '[' + str(index) + ']')]
    return [] if left == right else [location + ': ' + repr(left) + ' != ' + repr(right)]


def qualify(directory, include_additional=True, candidate_runner=None, candidate_summary=None, candidate_verifier=None):
    if candidate_runner is None:
        from sqlitefolio.runner import run_candidate
        candidate_runner = run_candidate
    if candidate_summary is None:
        from sqlitefolio.summary import build_summary
        candidate_summary = lambda actual, invariants: build_summary({'invariants': invariants}, [actual])[0]
    if candidate_verifier is None:
        from sqlitefolio.verify import reconstruct
        candidate_verifier = reconstruct
    freeze = validate_freeze(directory)
    assert not freeze['mismatches'], 'Frozen input changed'
    predictions = json.loads((directory / 'expected.json').read_text())['cases']
    frozen = [json.loads(path.read_text()) for path in sorted(directory.glob('[0-9][0-9]_*.json'))]
    cases = frozen + (additional_cases() if include_additional else [])
    records, failures = [], []
    for case in cases:
        for mode in ('schema', 'file'):
            with tempfile.TemporaryDirectory(prefix='sqlitefolio-qualify-') as folder:
                source_folder = Path(folder) / 'source'
                source_folder.mkdir()
                (source_folder / 'schema.sql').write_bytes(case['schema_sql'].encode('utf-8'))
                (source_folder / 'seed.sql').write_bytes(case['seed_sql'].encode('utf-8'))
                (source_folder / 'candidate.sql').write_bytes(case['migration_sql'].encode('utf-8'))
                source = None
                if mode == 'file':
                    source_path = source_folder / 'sample.db'
                    db = connect(source_path, case['foreign_keys'])
                    db.executescript(case['schema_sql'])
                    db.executescript(case['seed_sql'])
                    assert not db.in_transaction
                    db.close()
                    source = {'database': str(source_path)}
                request = request_for(case, source)
                source_before = directory_fingerprint(source_folder)
                prediction = predictions.get(case['id'])
                expected = reference(request, prediction['observations'] if prediction else ())
                auxiliary = expected.pop('_auxiliary', {})
                predicted = prediction_errors(expected, case, prediction, auxiliary) if prediction else []
                actual = candidate_runner(request)
                expected_summary = summary(expected, request['invariants'])
                actual_summary = candidate_summary(actual, request['invariants'])
                if prediction is None:
                    predicted += additional_prediction_errors(case, expected_summary)
                verification_input = {'format': 'sqlitefolio.input.v1', 'scenario': case['id'],
                    'source': {'database': 'sample.db'} if mode == 'file' else {'schema': 'inputs/schema.sql', 'seed': 'inputs/seed.sql'},
                    'profile': request['profile'], 'candidates': [{'name': case['id'], 'sql': 'inputs/candidate-1.sql'}],
                    'invariants': request['invariants'], 'limits': request['limits']}
                verifier_summary = candidate_verifier(verification_input, [actual])[0]
                verifier_mismatches = differences(expected_summary, verifier_summary)
                raw_mismatches = differences(expected, actual)
                summary_mismatches = differences(expected_summary, actual_summary)
                source_after = directory_fingerprint(source_folder)
                preserved = source_before == source_after
                record = {'case': case['id'], 'mode': mode,
                          'corpus': 'frozen12' if case in frozen else 'independent_additional',
                          'source_preserved': preserved, 'source_directory_preserved': preserved,
                          'source_files': source_before, 'prediction_errors': predicted, 'auxiliary_observations': auxiliary,
                          'raw_mismatches': raw_mismatches, 'summary_mismatches': summary_mismatches,
                          'reference': expected, 'candidate': actual,
                          'expected_summary': expected_summary, 'candidate_summary': actual_summary,
                          'verifier_summary': verifier_summary, 'verifier_mismatches': verifier_mismatches}
                records.append(record)
                for failure in predicted + raw_mismatches + summary_mismatches + verifier_mismatches:
                    failures.append(case['id'] + '/' + mode + ': ' + failure)
                if not preserved:
                    failures.append(case['id'] + '/' + mode + ': source bytes or directory changed')
    return {'format': 'sqlitefolio.independent-qualification.v1',
            'scope': 'Original trusted synthetic inputs only; same SQLite engine; not a security test or independent engine validation.',
            'frozen': freeze, 'frozen_execution_count': 2 * len(frozen),
            'frozen_named_checks': freeze['check_count'], 'additional_execution_count': 2 * (len(cases) - len(frozen)),
            'executions': records, 'mismatches': failures,
            'coverage': ['every typed cell and duplicate multiplicity', 'every raw sqlite_schema declaration',
                         'every column/table_list flag/index/FK fact', 'view resolution',
                         'native FK and integrity diagnostic', 'sqlite_sequence', 'all query phases',
                         'all named checks', 'execution status/error/FK modes/open transaction',
                         'common-column multiset projections', 'independent verifier reconstruction', 'source bytes and directory contents']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, default=ROOT / 'fixtures' / 'frozen')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference-only', action='store_true')
    args = parser.parse_args()
    if args.reference_only:
        report = {'freeze': validate_freeze(args.fixtures),
                  'frozen': {p.stem: run_frozen_reference(p) for p in sorted(args.fixtures.glob('[0-9][0-9]_*.json'))},
                  'additional': run_additional_references(), 'additional_predictions': validate_additional_predictions()}
        failures = report['freeze']['mismatches'] + report['additional_predictions']['mismatches'] + [name for name, r in report['frozen'].items() if not r['prediction_matches']]
    else:
        report = qualify(args.fixtures)
        failures = report['mismatches']
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False) + '\n')
    print(canonical({'passed': not failures, 'mismatch_count': len(failures), 'output': args.output.name}))
    return int(bool(failures))


if __name__ == '__main__':
    raise SystemExit(main())
