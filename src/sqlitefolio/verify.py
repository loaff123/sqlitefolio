"""Independent structural verification of SQLiteFolio protocol-1 evidence.

This module deliberately does not import the producer's contract, comparisons,
invariant evaluator, engine, or packet reader. Hashes establish consistency,
not authenticity; only a separately requested replay executes trusted SQL.
"""
from collections import Counter
import base64
import binascii
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import struct

_DEFAULTS = {'rows_per_query': 10000, 'vm_steps': 2000000,
             'wall_seconds': 30, 'evidence_bytes': 16777216}
_MINIMUMS = {'rows_per_query': 1, 'vm_steps': 1000,
             'wall_seconds': 1, 'evidence_bytes': 4096}
_PHASES = ('before', 'observed', 'reopened')
_LIMITATIONS = [
    'Trusted sample input only; guardrails are not a hostile-input security sandbox.',
    'Sample checks do not prove production safety or correctness for other data.',
    'Reopened state follows controlled close; no crash durability or rollback guarantee.',
    'Row deltas are common-column multiset projections, not inferred row identity.',
    'Structural verification checks consistency, not authorship or SQLite engine correctness.',
    'Source-bound replay is separate and uses the same qualified SQLite engine.',
    'Existing migration tools and competent SQLite recipes can obtain these same facts.',
]
_OK = [[['text', 'b2s=']]]


class VerificationError(ValueError):
    """The evidence does not satisfy the versioned structural contract."""


def _require(condition, reason):
    if not condition:
        raise VerificationError(reason)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _exact(value, required, optional=()):
    _require(type(value) is dict, 'expected an object')
    _require(set(required) <= value.keys() and not (value.keys() - set(required) - set(optional)),
             'missing or unexpected object field')


def _string(value, maximum=65536, empty=False):
    _require(type(value) is str and (empty or bool(value)) and '\x00' not in value,
             'invalid text metadata')
    _require(len(value.encode('utf-8')) <= maximum, 'text exceeds byte limit')
    return value


def _name(value):
    return _string(value, 128)


def _integer(value, low=0, high=2**63-1):
    _require(type(value) is int and low <= value <= high, 'invalid integer metadata')


def _flag(value):
    _integer(value, 0, 1)


def _boolean(value, nullable=False):
    _require(type(value) is bool or nullable and value is None, 'expected boolean')


def _relative(value):
    _string(value, 1024)
    _require('\\' not in value and ':' not in value and '$' not in value
             and all(part not in ('', '.', '..') for part in value.split('/')),
             'unsafe relative path')
    return value


def _error(value, nullable=True):
    if nullable and value is None:
        return
    _exact(value, ('kind', 'message', 'sqlite_code', 'sqlite_name'))
    _string(value['kind'], 128)
    _string(value['message'], 20*1024*1024, empty=True)
    if value['sqlite_code'] is not None:
        _integer(value['sqlite_code'], 0, 2**31-1)
    if value['sqlite_name'] is not None:
        _string(value['sqlite_name'], 128)


def _cell(value):
    _require(type(value) is list and len(value) == 2, 'invalid typed cell shape')
    tag, body = value
    _require(type(tag) is str, 'invalid storage tag')
    if tag == 'null':
        _require(body is None, 'null payload must be null')
        return
    _require(type(body) is str, 'typed cell payload must be a string')
    if tag == 'integer':
        _require(len(body) <= 20 and re.fullmatch(r'0|-?[1-9][0-9]*', body) is not None,
                 'noncanonical typed integer')
        _require(-(2**63) <= int(body) < 2**63, 'typed integer outside i64')
    elif tag == 'real':
        _require(re.fullmatch(r'[0-9a-f]{16}', body) is not None, 'invalid real encoding')
        _require(not math.isnan(struct.unpack('>d', bytes.fromhex(body))[0]), 'NaN is unsupported')
    elif tag in ('text', 'blob'):
        _require(len(body) <= 87384, 'value exceeds byte limit')
        try:
            decoded = base64.b64decode(body, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise VerificationError('invalid base64 cell') from exc
        _require(len(decoded) <= 65536 and base64.b64encode(decoded).decode('ascii') == body,
                 'noncanonical or oversized base64 cell')
    else:
        raise VerificationError('unknown typed storage class')


def _rows(value, maximum=10000, width=None, sorted_rows=False):
    _require(type(value) is list and len(value) <= maximum, 'invalid or oversized rows')
    widths = set()
    previous = None
    for row in value:
        _require(type(row) is list and len(row) <= 256, 'invalid row width')
        widths.add(len(row))
        if width is not None:
            _require(len(row) == width, 'row and column widths differ')
        for item in row:
            _cell(item)
        if sorted_rows:
            key = _canonical(row)
            _require(previous is None or previous <= key, 'rows must be canonically sorted')
            previous = key
    _require(len(widths) <= 1, 'inconsistent row widths')
    return len(value)


def _profile(value):
    _exact(value, ('foreign_keys', 'transaction_mode'))
    _boolean(value['foreign_keys'])
    _require(value['transaction_mode'] == 'autocommit', 'unsupported transaction profile')


def _manifest(value):
    _exact(value, ('format','scenario','source','profile','candidates','invariants'), ('limits',))
    _require(value['format'] == 'sqlitefolio.input.v1', 'unsupported manifest format')
    _require(type(value['scenario']) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',value['scenario']),
             'invalid scenario')
    _profile(value['profile'])
    src = value['source']
    _require(type(src) is dict and set(src) in ({'database'}, {'schema','seed'}), 'invalid source')
    for path in src.values():
        _relative(path)
    limits = dict(_DEFAULTS)
    supplied = value.get('limits', {})
    _require(type(supplied) is dict and not supplied.keys() - limits.keys(), 'invalid limits')
    for key, amount in supplied.items():
        _integer(amount, _MINIMUMS[key], _DEFAULTS[key])
        _require(key != 'vm_steps' or amount % 1000 == 0, 'invalid VM step granularity')
        limits[key] = amount
    candidates = value['candidates']
    _require(type(candidates) is list and 1 <= len(candidates) <= 8, 'invalid candidate count')
    used = set()
    for candidate in candidates:
        _exact(candidate, ('name','sql'))
        name = candidate['name']
        _require(type(name) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name) and name not in used,
                 'invalid or duplicate candidate name')
        used.add(name)
        _relative(candidate['sql'])
    invariants = value['invariants']
    _require(type(invariants) is list and len(invariants) <= 64, 'invalid invariant count')
    variants = {
        'query_equals': (('sql','order','expected'), ('boolean',)),
        'query_preserved': (('sql','order'), ()),
        'row_count': (('table','expected'), ()),
        'object_present': (('object_type','object'), ()),
        'object_absent': (('object_type','object'), ()),
        'no_foreign_key_violations': ((), ()), 'integrity_ok': ((), ())}
    used = set()
    for check in invariants:
        _require(type(check) is dict and type(check.get('kind')) is str
                 and check['kind'] in variants, 'invalid invariant kind')
        required, optional = variants[check['kind']]
        _exact(check, ('name','kind') + required, optional)
        _name(check['name'])
        _require(check['name'] not in used, 'duplicate invariant name')
        used.add(check['name'])
        kind = check['kind']
        if kind in ('query_equals','query_preserved'):
            _string(check['sql'])
            _require(check['order'] in ('ordered','bag'), 'invalid query order')
            if kind == 'query_equals':
                _rows(check['expected'], limits['rows_per_query'])
                _boolean(check.get('boolean', False))
                if check.get('boolean'):
                    _require(check['expected'] in ([[['integer','0']]], [[['integer','1']]]),
                             'boolean expectation must be one integer 0/1')
        elif kind == 'row_count':
            _name(check['table'])
            if check['expected'] != 'baseline':
                _require(type(check['expected']) is int and check['expected'] >= 0, 'row count expectation must be nonnegative integer')
        elif kind in ('object_present','object_absent'):
            _name(check['object'])
            _require(check['object_type'] in ('table','index','trigger','view'), 'invalid object type')
    normalized = dict(value)
    normalized['limits'] = limits
    return normalized


def _runtime(value):
    _exact(value, ('python','sqlite','sqlite_source_id','compile_options','platform','python_build','implementation','machine'))
    _string(value['python_build'])
    _require(value['implementation'] == 'CPython' and value['machine'] == 'x86_64', 'unqualified interpreter or machine')
    _require(type(value['python']) is str and re.fullmatch(r'3\.12\.[0-9]+',value['python']),
             'unqualified Python runtime')
    _require(value['sqlite'] == '3.53.1' and value['platform'] == 'linux', 'unqualified runtime')
    _string(value['sqlite_source_id'])
    options = value['compile_options']
    _require(type(options) is list and 0 < len(options) <= 512, 'invalid compile options')
    for option in options:
        _string(option)
    _require(options == sorted(set(options)), 'compile options must be sorted and unique')


def _native_row(value, width):
    _require(type(value) is list and len(value) == width, 'invalid native metadata row')


def _table(name, value, limits):
    _exact(value, ('columns','column_info','table_info','foreign_keys','indexes','rows'))
    columns = value['columns']
    _require(type(columns) is list and 1 <= len(columns) <= 256, 'invalid columns')
    for column in columns:
        _name(column)
    _require(len(set(columns)) == len(columns), 'duplicate columns')
    ci = value['column_info']
    _require(type(ci) is list and len(ci) == len(columns), 'inconsistent column metadata')
    for number, row in enumerate(ci):
        _native_row(row, 7)
        _integer(row[0], 0, 255)
        _require(row[0] == number and row[1] == columns[number], 'column metadata order differs')
        _string(row[2], 16*1024*1024, empty=True)
        _flag(row[3])
        if row[4] is not None:
            _string(row[4], 16*1024*1024, empty=True)
        _integer(row[5], 0, 256)
        _integer(row[6], 0, 3)
        _require(row[6] != 1, 'hidden virtual-table columns unsupported')
    ti = value['table_info']
    _native_row(ti, 6)
    _require(ti[:3] == ['main',name,'table'], 'table metadata identity mismatch')
    _integer(ti[3], 1, 256)
    _require(ti[3] == len(columns), 'table metadata column count differs')
    _flag(ti[4]); _flag(ti[5])
    fk = value['foreign_keys']
    _require(type(fk) is list, 'invalid foreign-key metadata')
    for row in fk:
        _native_row(row,8)
        _integer(row[0]); _integer(row[1]); _name(row[2]); _name(row[3])
        if row[4] is not None: _name(row[4])
        for item in row[5:]: _string(item, 128)
    indexes = value['indexes']
    _require(type(indexes) is list and len(indexes) <= 512, 'invalid indexes')
    index_names = []
    for index in indexes:
        _exact(index, ('info','columns'))
        row = index['info']; _native_row(row,5)
        _integer(row[0]); _name(row[1]); _flag(row[2])
        _require(row[3] in ('c','u','pk'), 'invalid index origin'); _flag(row[4])
        index_names.append(row[1])
        _require(type(index['columns']) is list and len(index['columns']) <= 512, 'invalid index columns')
        for pos, column in enumerate(index['columns']):
            _native_row(column,6)
            _integer(column[0]); _require(column[0] == pos, 'index column order differs')
            _integer(column[1], -2, 255)
            if column[2] is not None: _name(column[2])
            _flag(column[3])
            if column[4] is not None: _name(column[4])
            _flag(column[5])
    _require(index_names == sorted(set(index_names)), 'index names must be sorted and unique')
    native_count = 1 + len(ci) + len(fk) + len(indexes) + sum(len(index['columns']) for index in indexes)
    return native_count + _rows(value['rows'], limits['rows_per_query'], len(columns), sorted_rows=True)


def _snapshot(value, limits):
    if value is None:
        return 0
    _exact(value, ('complete','error','objects','tables','views','diagnostics','sequences'))
    _boolean(value['complete'])
    _error(value['error'], nullable=value['complete'])
    _require(not value['complete'] or value['error'] is None, 'complete snapshot has an error')
    objects = value['objects']
    _require(type(objects) is list and len(objects) <= 512, 'invalid schema objects')
    identities = []
    for item in objects:
        _exact(item, ('type','name','table','sql'))
        _require(item['type'] in ('table','index','trigger','view'), 'invalid schema object type')
        _name(item['name']); _name(item['table'])
        if item['sql'] is not None: _string(item['sql'], 16*1024*1024, empty=True)
        identities.append((item['type'],item['name']))
    _require(identities == sorted(set(identities)), 'object identities must be sorted and unique')
    tables = value['tables']; views = value['views']
    _require(type(tables) is dict and len(tables) <= 128, 'invalid tables')
    _require(type(views) is dict and len(views) <= 512, 'invalid views')
    count = 0
    for name, table in tables.items():
        _name(name)
        _require(not value['complete'] or ('table',name) in identities, 'table without matching schema object')
        count += _table(name,table,limits)
    for name, view in views.items():
        _name(name)
        _require(not value['complete'] or ('view',name) in identities, 'view without matching schema object')
        _exact(view, ('ok','error')); _boolean(view['ok']); _error(view['error'],nullable=view['ok'])
        _require(not view['ok'] or view['error'] is None, 'successful view has an error')
    if value['complete']:
        # Native table_list also observes views and sqlite_schema, beyond each
        # retained table_info row counted by _table.
        count += len(views) + 1
        _require(set(tables) == {name for kind,name in identities if kind=='table'}, 'missing table facts')
        _require(set(views) == {name for kind,name in identities if kind=='view'}, 'missing view facts')
    diag = value['diagnostics']
    _require(type(diag) is dict, 'invalid diagnostics')
    _require(not value['complete'] or bool(diag), 'complete snapshot lacks diagnostics')
    if diag:
        _exact(diag, ('foreign_key_check','integrity_check','foreign_keys'))
        _boolean(diag['foreign_keys'])
    for key, width in (() if not diag else (('foreign_key_check',4), ('integrity_check',1))):
        item = diag[key]
        _exact(item, ('rows','error')); _error(item['error'])
        count += _rows(item['rows'], limits['rows_per_query'], width)
        _require(not value['complete'] or item['error'] is None, 'erroring diagnostic in complete snapshot')
    count += _rows(value['sequences'], limits['rows_per_query'], 2, sorted_rows=True)
    if value['complete']:
        if 'sqlite_sequence' in tables:
            _require(value['sequences'] == tables['sqlite_sequence']['rows'], 'sequence facts disagree')
        else:
            _require(value['sequences'] == [], 'sequence rows without sequence table')
    return count


def _query_fact(value, limits):
    _exact(value, ('rows','error','complete'))
    _boolean(value['complete']); _error(value['error'],nullable=value['complete'])
    if value['complete']:
        _require(value['error'] is None and value['rows'] is not None, 'complete query has missing facts or error')
    elif value['error']['kind'] == 'not_run':
        _require(value['rows'] is None, 'not_run query contains invented rows')
    return 0 if value['rows'] is None else _rows(value['rows'], limits['rows_per_query'])


def _validate_candidate(value, manifest):
    _exact(value, ('name','runtime','profile','execution','before','observed','reopened','queries','source_preserved'))
    _name(value['name']); _runtime(value['runtime']); _profile(value['profile'])
    _require(value['profile'] == manifest['profile'], 'profile differs from manifest')
    _boolean(value['source_preserved'])
    ex = value['execution']
    _exact(ex, ('status','error','in_transaction','foreign_keys_start','foreign_keys_end'))
    _require(ex['status'] in ('completed','sqlite_error','open_transaction','resource_limit','unsupported','worker_error','source_changed'),
             'invalid execution status')
    _error(ex['error'],nullable=ex['status'] in ('completed','open_transaction'))
    if ex['status'] in ('completed','open_transaction'):
        _require(ex['error'] is None, 'non-error execution contains an error')
    else:
        _require(ex['error']['kind'] == ex['status'], 'execution status and error kind differ')
    for field in ('in_transaction','foreign_keys_start','foreign_keys_end'):
        _boolean(ex[field],nullable=True)
    if ex['status'] == 'completed':
        _require(ex['error'] is None and ex['in_transaction'] is False, 'completed execution state is inconsistent')
        _require(type(ex['foreign_keys_start']) is bool and type(ex['foreign_keys_end']) is bool,
                 'completed execution has unavailable FK modes')
    if ex['status'] == 'open_transaction':
        _require(ex['in_transaction'] is True, 'open_transaction execution has no open transaction')
    if ex['foreign_keys_start'] is not None:
        _require(ex['foreign_keys_start'] == manifest['profile']['foreign_keys'], 'initial FK mode differs')
    total = sum(_snapshot(value[phase],manifest['limits']) for phase in _PHASES)
    for phase in ('before','reopened'):
        if value[phase] is not None and value[phase]['diagnostics']:
            _require(value[phase]['diagnostics']['foreign_keys'] == manifest['profile']['foreign_keys'],
                     'observer FK mode differs from manifest')
    if value['observed'] is not None and value['observed']['diagnostics'] and ex['foreign_keys_end'] is not None:
        _require(value['observed']['diagnostics']['foreign_keys'] == ex['foreign_keys_end'],
                 'observed FK mode differs from execution')
    queries = value['queries']
    names = {check['name'] for check in manifest['invariants'] if check['kind'].startswith('query_')}
    _exact(queries, names)
    for query in queries.values():
        _exact(query, _PHASES)
        total += sum(_query_fact(query[phase],manifest['limits']) for phase in _PHASES)
    _require(total <= 100000, 'total observed row limit exceeded')
    raw_size = len(_canonical(value))
    _require(raw_size <= 20*1024*1024, 'worker response exceeds fixed limit')
    if raw_size > manifest['limits']['evidence_bytes']:
        minimal = all(value[phase] is None for phase in _PHASES) and all(
            not fact['complete'] and fact['rows'] is None and fact['error']['kind']=='not_run'
            for query in queries.values() for fact in query.values())
        _require(minimal and ex['status'] in ('resource_limit','worker_error','source_changed'),
                 'raw evidence exceeds declared limit')


def _query_status(fact):
    if fact['complete']:
        return None
    kind = fact['error']['kind']
    return 'not_run' if kind == 'not_run' else 'incomplete' if kind in ('resource_limit','incomplete') else 'error'


def _evaluate(check, candidate, phase):
    kind = check['kind']
    if kind.startswith('query_'):
        facts = candidate['queries'][check['name']]
        problem = _query_status(facts[phase])
        if problem: return problem
        observed = facts[phase]['rows']
        if kind == 'query_equals':
            expected = check['expected']
            if check.get('boolean') and observed not in ([[['integer','0']]], [[['integer','1']]]):
                return 'error'
        else:
            problem = _query_status(facts['before'])
            if problem: return problem
            expected = facts['before']['rows']
        if check['order'] == 'bag':
            return 'pass' if Counter(map(_canonical,observed)) == Counter(map(_canonical,expected)) else 'fail'
        return 'pass' if observed == expected else 'fail'
    observed = candidate[phase]
    if observed is None or not observed['complete']:
        return 'incomplete'
    if kind == 'row_count':
        if check['table'] not in observed['tables']:
            return 'fail'
        expected = check['expected']
        if expected == 'baseline':
            before = candidate['before']
            if before is None or not before['complete']:
                return 'incomplete'
            if check['table'] not in before['tables']:
                return 'error'
            expected = len(before['tables'][check['table']]['rows'])
        return 'pass' if len(observed['tables'][check['table']]['rows']) == expected else 'fail'
    if kind in ('object_present','object_absent'):
        found = any(obj['type']==check['object_type'] and obj['name']==check['object'] for obj in observed['objects'])
        return 'pass' if found == (kind=='object_present') else 'fail'
    diagnostic = observed['diagnostics']['foreign_key_check' if kind=='no_foreign_key_violations' else 'integrity_check']
    if diagnostic['error'] is not None:
        return 'incomplete'
    return 'pass' if diagnostic['rows'] == ([] if kind=='no_foreign_key_violations' else _OK) else 'fail'


def _changes(before, after):
    if before is None or after is None or not before['complete'] or not after['complete']:
        return {'complete':False,'objects_added':None,'objects_removed':None,'objects_changed':None,'tables':{}}
    left = {x['type']+':'+x['name']:x for x in before['objects']}
    right = {x['type']+':'+x['name']:x for x in after['objects']}
    tables = {}
    for name in sorted(before['tables'].keys() | after['tables'].keys()):
        old = before['tables'].get(name); new = after['tables'].get(name)
        old_columns = old['columns'] if old else []; new_columns = new['columns'] if new else []
        common = [column for column in old_columns if column in new_columns]
        added = removed = None
        if old is not None and new is not None and common:
            a = [old_columns.index(column) for column in common]
            b = [new_columns.index(column) for column in common]
            left_rows = Counter(_canonical([row[i] for i in a]) for row in old['rows'])
            right_rows = Counter(_canonical([row[i] for i in b]) for row in new['rows'])
            added = sum((right_rows-left_rows).values()); removed = sum((left_rows-right_rows).values())
        tables[name] = {
            'kind':'added' if old is None else 'removed' if new is None else 'projection',
            'common_columns':common,
            'columns_added':[column for column in new_columns if column not in old_columns],
            'columns_removed':[column for column in old_columns if column not in new_columns],
            'rows_before':len(old['rows']) if old else 0, 'rows_after':len(new['rows']) if new else 0,
            'projected_added':added,'projected_removed':removed,
            'metadata_changed':old is None or new is None or any(old[key]!=new[key] for key in ('column_info','table_info','foreign_keys','indexes')),
        }
    return {'complete':True,'objects_added':sorted(right.keys()-left.keys()),
            'objects_removed':sorted(left.keys()-right.keys()),
            'objects_changed':sorted(key for key in left.keys() & right.keys() if left[key]!=right[key]),
            'tables':tables}


def _summary(candidate, manifest):
    complete = all(candidate[phase] is not None and candidate[phase]['complete'] for phase in _PHASES) and candidate['source_preserved']
    checks = {phase:[{'name':check['name'],'status':_evaluate(check,candidate,phase)} for check in manifest['invariants']]
              for phase in ('observed','reopened')}
    states = [check['status'] for phase in checks.values() for check in phase]
    status = candidate['execution']['status']
    if status == 'worker_error':
        verdict = 'internal_error'
    elif not complete or status in ('source_changed','resource_limit') or any(x in ('not_run','incomplete') for x in states):
        verdict = 'incomplete'
    elif status in ('sqlite_error','open_transaction'):
        verdict = 'execution_error'
    elif status == 'unsupported':
        verdict = 'unsupported'
    else:
        bad = any(x != 'pass' for x in states)
        for phase in ('observed','reopened'):
            snapshot = candidate[phase]
            bad = bad or any(not view['ok'] for view in snapshot['views'].values())
            bad = bad or snapshot['diagnostics']['foreign_key_check']['rows'] != []
            bad = bad or snapshot['diagnostics']['integrity_check']['rows'] != _OK
        verdict = 'failed' if bad else 'pass'
    return {'name':candidate['name'],'status':verdict,'execution':status,'complete':complete,
            'observed_checks':checks['observed'],'reopened_checks':checks['reopened'],
            'observed_changes':_changes(candidate['before'],candidate['observed']),
            'reopened_changes':_changes(candidate['before'],candidate['reopened'])}


def reconstruct(input_manifest, candidates):
    """Validate protocol facts and independently reconstruct every summary field.

    This semantic API raises ValueError for malformed facts. verify_packet wraps
    all malformed-packet errors into its controlled result object.
    """
    try:
        manifest = _manifest(input_manifest)
        _require(type(candidates) is list and len(candidates) == len(manifest['candidates']),
                 'candidate count differs from manifest')
        for expected, actual in zip(manifest['candidates'],candidates):
            _validate_candidate(actual,manifest)
            _require(actual['name'] == expected['name'], 'candidate ordering or identity differs')
        first = candidates[0]
        baselines = [item['before'] for item in candidates if item['before'] is not None and item['before']['complete']]
        _require(not baselines or all(item == baselines[0] for item in baselines), 'candidate complete baseline snapshots differ')
        for actual in candidates[1:]:
            _require(actual['runtime'] == first['runtime'], 'candidate runtimes differ')
        return [_summary(candidate,manifest) for candidate in candidates]
    except (TypeError, KeyError, IndexError, UnicodeError, OverflowError, RecursionError) as exc:
        raise VerificationError('malformed raw evidence: '+str(exc)) from exc


def _load(data):
    def pairs(items):
        out = {}
        for key,value in items:
            _require(key not in out, 'duplicate JSON key')
            out[key] = value
        return out
    def bad_constant(value):
        raise VerificationError('nonfinite JSON number')
    value = json.loads(data.decode('utf-8'),object_pairs_hook=pairs,parse_constant=bad_constant)
    # The decoder can turn 1e999 into infinity without invoking parse_constant.
    _canonical(value)
    return value


def _safe_root(path):
    # Inspect lexical components before normalizing '..': a symlink followed by
    # '..' must not be hidden by abspath's purely lexical simplification.
    incoming = Path(path)
    if not incoming.is_absolute():
        incoming = Path.cwd() / incoming
    current = Path(incoming.anchor)
    for component in incoming.parts[1:]:
        if component == '..':
            current = current.parent
        else:
            current = current / component
            _require(not current.is_symlink(), 'symlink packet path component')
    _require(current.is_dir(), 'packet must be a directory')
    return current


def _read(path, maximum):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path,flags)
    try:
        before = os.fstat(fd)
        _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'packet entry is not an unlinked regular file')
        _require(before.st_size <= maximum, 'packet file exceeds size limit')
        chunks = []; size = 0
        while True:
            block = os.read(fd,min(65536,maximum+1-size))
            if not block: break
            size += len(block); _require(size <= maximum, 'packet file grew beyond limit')
            chunks.append(block)
        after = os.fstat(fd)
        _require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns,before.st_nlink)==
                 (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns,after.st_nlink),
                 'packet file changed while reading')
        return b''.join(chunks)
    finally:
        os.close(fd)


def _inventory(root, expected):
    actual = set()
    for child in root.iterdir():
        _require(not child.is_symlink(), 'symlink in packet')
        if child.name == 'inputs':
            _require(child.is_dir(), 'inputs is not a directory')
            for item in child.iterdir():
                _require(not item.is_symlink() and item.is_file(), 'non-file or nested directory in inputs')
                actual.add('inputs/'+item.name)
        else:
            _require(child.is_file(), 'extra directory in packet')
            actual.add(child.name)
    _require(actual == expected, 'packet filename set differs from protocol')


def _normalize_original(value):
    result = _manifest(value)
    result['source'] = {'database':'sample.db'} if 'database' in result['source'] else {'schema':'inputs/schema.sql','seed':'inputs/seed.sql'}
    result['candidates'] = [{'name':item['name'],'sql':f'inputs/candidate-{number}.sql'}
                            for number,item in enumerate(result['candidates'],1)]
    return result


def verify_packet(path):
    """Return a controlled structural-only verification result; never execute SQL."""
    try:
        root = _safe_root(path)
        packet_bytes = _read(root/'packet.json',32*1024*1024)
        packet = _load(packet_bytes)
        _exact(packet, ('format','input','input_sha256','files','source','candidates','summary','limitations'))
        _require(packet['format']=='sqlitefolio.packet.v1', 'unsupported packet format')
        normalized = _manifest(packet['input'])
        _require(normalized == packet['input'], 'packet input must contain normalized limits')
        _require(packet['input_sha256']==_hash(_canonical(normalized)), 'input digest mismatch')
        _require(packet['limitations']==_LIMITATIONS, 'versioned limitations differ')
        source = packet['source']
        _exact(source, ('kind','sha256','snapshot_sha256','included'))
        _boolean(source['included'])
        for key in ('sha256','snapshot_sha256'):
            _require(type(source[key]) is str and re.fullmatch(r'[0-9a-f]{64}',source[key]), 'invalid source digest')
        db_source = 'database' in normalized['source']
        _require(not source['included'] or db_source, 'only database sources may include sample.db')
        _require(source['kind'] == ('database' if db_source else 'sql'), 'source kind differs from manifest')
        retained = {'inputs/original-manifest.json'}
        retained.update(f'inputs/candidate-{n}.sql' for n in range(1,len(normalized['candidates'])+1))
        if not db_source:
            retained.update(('inputs/schema.sql','inputs/seed.sql'))
        if source['included']:
            retained.add('sample.db')
        _exact(packet['files'], retained)
        _inventory(root, retained | {'packet.json','manifest.json','report.html'})
        total = 0; contents = {}
        for name, expected_digest in packet['files'].items():
            _require(type(expected_digest) is str and re.fullmatch(r'[0-9a-f]{64}',expected_digest), 'invalid retained-file digest')
            contents[name] = _read(root/name,16*1024*1024 if name=='sample.db' else 1024*1024)
            total += len(contents[name])
            _require(_hash(contents[name]) == expected_digest, 'retained-file digest mismatch')
            if name.endswith('.sql'):
                contents[name].decode('utf-8')
        _require(total <= 32*1024*1024, 'retained input bytes exceed limit')
        original = _load(contents['inputs/original-manifest.json'])
        _require(_normalize_original(original) == normalized, 'original and normalized input contracts differ')
        # Repeated original references name the same input bytes, even though
        # the review packet gives each role its own normalized retained file.
        identities = {}
        bindings = [(item['sql'], contents[f'inputs/candidate-{number}.sql'])
                    for number,item in enumerate(original['candidates'],1)]
        if not db_source:
            bindings += [(original['source']['schema'],contents['inputs/schema.sql']),
                         (original['source']['seed'],contents['inputs/seed.sql'])]
        for name, data in bindings:
            identity = _hash(data)
            _require(name not in identities or identities[name] == identity,
                     'repeated original file reference has inconsistent bytes')
            identities[name] = identity
        if db_source and original['source']['database'] in identities:
            _require(identities[original['source']['database']] == source['sha256'],
                     'source database and SQL file identity disagree')
        _require(_read(root/'manifest.json',1024*1024) == _canonical(normalized), 'manifest.json differs from canonical packet input')
        if not db_source:
            expected = _hash(_canonical({'schema_sha256':_hash(contents['inputs/schema.sql']),
                                         'seed_sha256':_hash(contents['inputs/seed.sql'])}))
            _require(source['sha256'] == expected, 'source SQL digest mismatch')
        elif source['included']:
            _require(source['sha256']==_hash(contents['sample.db']), 'included source database digest mismatch')
        rebuilt = reconstruct(normalized,packet['candidates'])
        _require(source['snapshot_sha256']==_hash(_canonical(packet['candidates'][0]['before'])), 'source snapshot digest mismatch')
        # Canonical comparison avoids Python equating True and 1 in forged summaries.
        _require(_canonical(packet['summary']) == _canonical(rebuilt), 'summary differs from independent reconstruction')
        from .report import render
        report_bytes = _read(root/'report.html',64*1024*1024)
        _require(report_bytes == render(packet).encode('utf-8'), 'report differs from reconstructed packet')
        _inventory(root, retained | {'packet.json','manifest.json','report.html'})
        exits = {'pass':0,'failed':1,'unsupported':2,'execution_error':3,'incomplete':4,'internal_error':5}
        code = max(exits[item['status']] for item in rebuilt)
        return {'valid':True,'passing':code==0,'exit_code':code,
                'packet_sha256':_hash(packet_bytes),'report_sha256':_hash(report_bytes),
                'message':'Structurally consistent '+('passing' if code==0 else 'nonpassing')+' packet; hashes do not authenticate authorship or native SQLite truth.'}
    except Exception as exc:
        return {'valid':False,'passing':False,'exit_code':6,
                'message':'Structural verification failed: '+str(exc)}
