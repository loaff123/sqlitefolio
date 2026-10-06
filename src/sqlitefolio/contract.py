"""Strict public manifest contract. No executable configuration hooks."""
import base64
import binascii
import json
import math
import re
import struct

FORMAT = 'sqlitefolio.input.v1'
DEFAULT_LIMITS = {'rows_per_query': 10000, 'vm_steps': 2000000,
                  'wall_seconds': 30, 'evidence_bytes': 16777216}
MIN_LIMITS = {'rows_per_query': 1, 'vm_steps': 1000,
              'wall_seconds': 1, 'evidence_bytes': 4096}
NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')


class InputError(ValueError):
    """Invalid or unsupported input; no input is executed."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('utf-8')


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise InputError('duplicate JSON key: ' + key)
        out[key] = value
    return out


def read_json(data):
    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(InputError('nonfinite JSON number')))
        canonical(value)  # also reject finite-looking exponents that overflow to infinity
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise InputError('invalid strict UTF-8 JSON: ' + str(exc)) from exc


def keys(obj, required, optional=()):
    if type(obj) is not dict or not set(required) <= obj.keys() or set(obj) - set(required) - set(optional):
        raise InputError('missing or unknown contract fields')


def text(value, label, maximum=128):
    if type(value) is not str or not value or '\x00' in value:
        raise InputError('invalid ' + label)
    try:
        if len(value.encode('utf-8')) > maximum:
            raise InputError(label + ' exceeds byte cap')
    except UnicodeError as exc:
        raise InputError('invalid Unicode in ' + label) from exc
    return value


def relative(value):
    text(value, 'relative filename', 1024)
    if '\\' in value or ':' in value or '$' in value or any(p in ('', '.', '..') for p in value.split('/')):
        raise InputError('explicit relative file references only')
    return value


def cell(value):
    if type(value) is not list or len(value) != 2:
        raise InputError('typed cells must have exactly two items')
    tag, payload = value
    if tag == 'null' and payload is None:
        return
    if type(payload) is not str:
        raise InputError('typed cell payload must be a string')
    if tag == 'integer':
        if not re.fullmatch(r'0|-?[1-9][0-9]*', payload) or len(payload) > 20 or not -(2**63) <= int(payload) < 2**63:
            raise InputError('noncanonical or out-of-range integer')
    elif tag == 'real':
        if not re.fullmatch('[0-9a-f]{16}', payload) or math.isnan(struct.unpack('>d', bytes.fromhex(payload))[0]):
            raise InputError('invalid real encoding (NaN unsupported)')
    elif tag in ('text', 'blob'):
        try:
            decoded = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InputError('invalid base64') from exc
        if len(decoded) > 65536 or base64.b64encode(decoded).decode('ascii') != payload:
            raise InputError('noncanonical or oversized base64')
    else:
        raise InputError('unknown typed storage class')


def rows(value, limit=10000):
    if type(value) is not list or len(value) > limit:
        raise InputError('invalid or oversized typed row list')
    widths = set()
    for row in value:
        if type(row) is not list or len(row) > 256:
            raise InputError('invalid row width')
        widths.add(len(row))
        for item in row:
            cell(item)
    if len(widths) > 1:
        raise InputError('inconsistent row widths')


def validate_manifest(value):
    keys(value, ('format','scenario','source','profile','candidates','invariants'), ('limits',))
    if value['format'] != FORMAT or type(value['scenario']) is not str or not NAME.fullmatch(value['scenario']):
        raise InputError('unsupported format or scenario name')
    source = value['source']
    if type(source) is not dict or set(source) not in ({'database'}, {'schema','seed'}):
        raise InputError('choose database OR schema and seed source')
    for name in source.values(): relative(name)
    profile = value['profile']
    keys(profile, ('foreign_keys','transaction_mode'))
    if type(profile['foreign_keys']) is not bool or profile['transaction_mode'] != 'autocommit':
        raise InputError('explicit boolean FK/autocommit profile required')
    limits = dict(DEFAULT_LIMITS)
    supplied = value.get('limits', {})
    if type(supplied) is not dict or set(supplied) - limits.keys():
        raise InputError('unknown limits')
    for key, limit in supplied.items():
        if type(limit) is not int or not MIN_LIMITS[key] <= limit <= DEFAULT_LIMITS[key]:
            raise InputError('invalid or excessive limit: ' + key)
        if key == 'vm_steps' and limit % 1000:
            raise InputError('vm_steps must be a multiple of 1000')
        limits[key] = limit
    candidates = value['candidates']
    if type(candidates) is not list or not 1 <= len(candidates) <= 8:
        raise InputError('one to eight candidates required')
    names = set()
    for candidate in candidates:
        keys(candidate, ('name','sql'))
        name = candidate['name']
        if type(name) is not str or not NAME.fullmatch(name) or name in names:
            raise InputError('invalid or duplicate candidate name')
        names.add(name); relative(candidate['sql'])
    checks = value['invariants']
    if type(checks) is not list or len(checks) > 64:
        raise InputError('invalid or excessive invariants')
    names = set()
    variants = {'query_equals': (('sql','order','expected'), ('boolean',)),
                'query_preserved': (('sql','order'), ()), 'row_count': (('table','expected'), ()),
                'object_present': (('object_type','object'), ()),
                'object_absent': (('object_type','object'), ()),
                'no_foreign_key_violations': ((), ()), 'integrity_ok': ((), ())}
    for check in checks:
        if type(check) is not dict or type(check.get('kind')) is not str or check['kind'] not in variants:
            raise InputError('unknown invariant kind')
        required, optional = variants[check['kind']]
        keys(check, ('name','kind') + required, optional)
        name = text(check['name'], 'invariant name')
        if name in names: raise InputError('duplicate invariant name')
        names.add(name)
        kind = check['kind']
        if kind.startswith('query_'):
            text(check['sql'], 'query SQL', 65536)
            if check['order'] not in ('ordered','bag'): raise InputError('query order must be explicit')
            if kind == 'query_equals':
                rows(check['expected'], limits['rows_per_query'])
                if type(check.get('boolean', False)) is not bool: raise InputError('boolean must be boolean')
                if check.get('boolean') and check['expected'] not in ([[['integer','0']]], [[['integer','1']]]):
                    raise InputError('boolean expectation must be exactly one integer 0/1')
        elif kind == 'row_count':
            text(check['table'], 'table')
            if not (check['expected'] == 'baseline' or type(check['expected']) is int and 0 <= check['expected']):
                raise InputError('invalid row count expectation')
        elif kind.startswith('object_'):
            text(check['object'], 'object')
            if check['object_type'] not in ('table','index','trigger','view'): raise InputError('unknown object type')
    result = dict(value)
    result['limits'] = limits
    return result
