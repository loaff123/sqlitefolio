"""Original benign qualification inputs and handwritten predictions, frozen before runs."""
import base64
import hashlib
import json
from pathlib import Path


def t(value):
    return ['text', base64.b64encode(value.encode()).decode()]


def i(value):
    return ['integer', str(value)]


def eq(name, sql, rows, order='ordered', boolean=False):
    result = {'name': name, 'kind': 'query_equals', 'sql': sql, 'order': order, 'expected': rows}
    if boolean:
        result['boolean'] = True
    return result


def preserved(name, sql, order='bag'):
    return {'name': name, 'kind': 'query_preserved', 'sql': sql, 'order': order}


def count(table, expected='baseline'):
    return {'name': table + '_count', 'kind': 'row_count', 'table': table, 'expected': expected}


cases = [
    {'id': 'duplicate_multiset', 'foreign_keys': True,
     'schema_sql': 'CREATE TABLE bag(label TEXT, amount INTEGER);',
     'seed_sql': "INSERT INTO bag VALUES ('same',1),('same',1),('other',2);",
     'migration_sql': 'DELETE FROM bag WHERE rowid=(SELECT min(rowid) FROM bag);',
     'invariants': [preserved('duplicate_rows', 'SELECT label,amount FROM bag'), count('bag'),
                    eq('remaining_duplicate', "SELECT count(*) FROM bag WHERE label='same'", [[i(1)]])],
     'predicted': {'execution': 'completed', 'status': 'failed',
                   'observed_checks': ['fail', 'fail', 'pass'], 'reopened_checks': ['fail', 'fail', 'pass'],
                   'tables': {'bag': {'projected_added': 0, 'projected_removed': 1, 'rows_before': 3, 'rows_after': 2}}}},
    {'id': 'rename_projection', 'foreign_keys': True,
     'schema_sql': 'CREATE TABLE labels(old_name TEXT);', 'seed_sql': "INSERT INTO labels VALUES ('oak'),('oak');",
     'migration_sql': 'ALTER TABLE labels RENAME COLUMN old_name TO new_name;',
     'invariants': [count('labels'), eq('renamed_values', 'SELECT new_name FROM labels', [[t('oak')], [t('oak')]], 'bag')],
     'predicted': {'execution': 'completed', 'status': 'pass', 'observed_checks': ['pass', 'pass'],
                   'reopened_checks': ['pass', 'pass'], 'tables': {'labels': {'common_columns': [],
                    'columns_added': ['new_name'], 'columns_removed': ['old_name'],
                    'projected_added': None, 'projected_removed': None}}}},
    {'id': 'generated_columns', 'foreign_keys': True,
     'schema_sql': 'CREATE TABLE measurements(base INTEGER, doubled INTEGER GENERATED ALWAYS AS(base*2) VIRTUAL, squared INTEGER GENERATED ALWAYS AS(base*base) STORED);',
     'seed_sql': 'INSERT INTO measurements(base) VALUES(2),(3);',
     'migration_sql': 'UPDATE measurements SET base=4 WHERE base=2;',
     'invariants': [eq('computed_values', 'SELECT * FROM measurements ORDER BY base', [[i(3), i(6), i(9)], [i(4), i(8), i(16)]]),
                    eq('generated_metadata', "SELECT name,hidden FROM pragma_table_xinfo('measurements') ORDER BY cid", [[t('base'), i(0)], [t('doubled'), i(2)], [t('squared'), i(3)]])],
     'predicted': {'execution': 'completed', 'status': 'pass', 'observed_checks': ['pass', 'pass'],
                   'reopened_checks': ['pass', 'pass'], 'tables': {'measurements': {'projected_added': 1, 'projected_removed': 1}}}},
    {'id': 'sequence_retention', 'foreign_keys': True,
     'schema_sql': 'CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT);',
     'seed_sql': "INSERT INTO events(id,note) VALUES(8,'old'); DELETE FROM events;",
     'migration_sql': "INSERT INTO events(note) VALUES('new');",
     'invariants': [eq('sequence', 'SELECT name,seq FROM sqlite_sequence', [[t('events'), i(9)]]),
                    eq('assigned_id', 'SELECT id FROM events', [[i(9)]]), count('events', 1)],
     'predicted': {'execution': 'completed', 'status': 'pass', 'observed_checks': ['pass', 'pass', 'pass'],
                   'reopened_checks': ['pass', 'pass', 'pass'], 'tables': {'sqlite_sequence': {'projected_added': 1, 'projected_removed': 1}}}},
    {'id': 'native_metadata', 'foreign_keys': True,
     'schema_sql': "CREATE TABLE parents(a TEXT COLLATE NOCASE,b INTEGER,PRIMARY KEY(a,b)) WITHOUT ROWID,STRICT; CREATE TABLE children(a TEXT,b INTEGER,note TEXT,FOREIGN KEY(a,b) REFERENCES parents(a,b) ON UPDATE CASCADE ON DELETE RESTRICT); CREATE UNIQUE INDEX child_expression ON children(lower(note)) WHERE note IS NOT NULL; CREATE VIEW parent_view AS SELECT a,b FROM parents;",
     'seed_sql': "INSERT INTO parents VALUES('p',1); INSERT INTO children VALUES('p',1,'Note');",
     'migration_sql': "CREATE INDEX child_desc ON children(b DESC,a COLLATE NOCASE); UPDATE parents SET b=2 WHERE a='p';",
     'invariants': [eq('cascade_update', 'SELECT a,b,note FROM children', [[t('p'), i(2), t('Note')]]),
                    {'name': 'fk_ok', 'kind': 'no_foreign_key_violations'},
                    {'name': 'integrity', 'kind': 'integrity_ok'},
                    {'name': 'expression_present', 'kind': 'object_present', 'object_type': 'index', 'object': 'child_expression'},
                    {'name': 'not_here', 'kind': 'object_absent', 'object_type': 'trigger', 'object': 'unused'}],
     'predicted': {'execution': 'completed', 'status': 'pass', 'observed_checks': ['pass'] * 5,
                   'reopened_checks': ['pass'] * 5, 'tables': {'parents': {'metadata_changed': False}, 'children': {'metadata_changed': True}}}},
    {'id': 'typed_values', 'foreign_keys': True,
     'schema_sql': 'CREATE TABLE valueset(id INTEGER, rawtext TEXT, rawblob BLOB, hi INTEGER, lo INTEGER, positive_inf REAL, nullable, zero);',
     'seed_sql': "INSERT INTO valueset VALUES(1,CAST(X'FF00' AS TEXT),X'FF00',9223372036854775807,-9223372036854775808,1e999,NULL,-0.0);",
     'migration_sql': '-- Preserve all SQLite-observed storage classes and bytes.\nSELECT 1;',
     'invariants': [preserved('all_typed_values', 'SELECT * FROM valueset'),
                    eq('sqlite_storage_classes', 'SELECT typeof(rawtext),typeof(rawblob),typeof(hi),typeof(positive_inf),typeof(nullable),typeof(zero) FROM valueset', [[t('text'),t('blob'),t('integer'),t('real'),t('null'),t('real')]])],
     'predicted': {'execution': 'completed', 'status': 'pass', 'observed_checks': ['pass', 'pass'],
                   'reopened_checks': ['pass', 'pass'], 'tables': {'valueset': {'projected_added': 0, 'projected_removed': 0}}}},
    {'id': 'constraint_declaration', 'foreign_keys': True,
     'schema_sql': 'CREATE TABLE stock(id INTEGER PRIMARY KEY, qty INTEGER CONSTRAINT nonnegative CHECK(qty>=0));',
     'seed_sql': 'INSERT INTO stock VALUES(1,4);',
     'migration_sql': 'CREATE TABLE replacement(id INTEGER PRIMARY KEY, qty INTEGER); INSERT INTO replacement SELECT * FROM stock; DROP TABLE stock; ALTER TABLE replacement RENAME TO stock;',
     'invariants': [preserved('values_preserved', 'SELECT * FROM stock'),
                    eq('constraint_survives', "SELECT instr(sql,'CHECK')>0 FROM sqlite_schema WHERE name='stock'", [[i(1)]], boolean=True)],
     'predicted': {'execution': 'completed', 'status': 'failed', 'observed_checks': ['pass', 'fail'],
                   'reopened_checks': ['pass', 'fail'], 'tables': {'stock': {'metadata_changed': False, 'projected_added': 0, 'projected_removed': 0}}}},
    {'id': 'ordered_bag_and_boolean', 'foreign_keys': True,
     'schema_sql': 'CREATE TABLE tags(id INTEGER PRIMARY KEY,label TEXT);',
     'seed_sql': "INSERT INTO tags VALUES(1,'b'),(2,'a'),(3,'a');", 'migration_sql': 'SELECT 1;',
     'invariants': [eq('bag_counts', 'SELECT label FROM tags ORDER BY id', [[t('a')],[t('b')],[t('a')]], 'bag'),
                    eq('ordered_differs', 'SELECT label FROM tags ORDER BY id', [[t('a')],[t('a')],[t('b')]]),
                    eq('strict_boolean', 'SELECT 2', [[i(1)]], boolean=True),
                    eq('integer_not_real', 'SELECT 1.0', [[i(1)]]),
                    eq('valid_boolean', 'SELECT count(*)=3 FROM tags', [[i(1)]], boolean=True)],
     'predicted': {'execution': 'completed', 'status': 'failed', 'observed_checks': ['pass','fail','error','fail','pass'],
                   'reopened_checks': ['pass','fail','error','fail','pass'], 'tables': {'tags': {'projected_added': 0, 'projected_removed': 0}}}},
    {'id': 'savepoint_close', 'foreign_keys': False,
     'schema_sql': 'CREATE TABLE notes(id INTEGER PRIMARY KEY,value TEXT);',
     'seed_sql': "INSERT INTO notes VALUES(1,'draft');",
     'migration_sql': "SAVEPOINT review; UPDATE notes SET value='reviewed';",
     'invariants': [eq('reviewed', 'SELECT value FROM notes', [[t('reviewed')]]), count('notes')],
     'predicted': {'execution': 'open_transaction', 'status': 'execution_error', 'observed_checks': ['pass','pass'],
                   'reopened_checks': ['fail','pass'], 'tables': {'notes': {'projected_added': 1, 'projected_removed': 1}}}},
    {'id': 'add_drop_empty_tables', 'foreign_keys': True,
     'schema_sql': 'CREATE TABLE old_empty(a TEXT); CREATE TABLE kept(a INTEGER,b TEXT);',
     'seed_sql': "INSERT INTO kept VALUES(1,'before'),(1,'before');",
     'migration_sql': "DROP TABLE old_empty; CREATE TABLE new_empty(a INTEGER); ALTER TABLE kept ADD COLUMN fresh TEXT DEFAULT 'new'; ALTER TABLE kept DROP COLUMN b;",
     'invariants': [count('kept'), count('new_empty', 0), {'name': 'old_gone', 'kind': 'object_absent', 'object_type': 'table', 'object': 'old_empty'}],
     'predicted': {'execution': 'completed', 'status': 'pass', 'observed_checks': ['pass']*3, 'reopened_checks': ['pass']*3,
                   'tables': {'kept': {'common_columns': ['a'], 'columns_added': ['fresh'], 'columns_removed': ['b'],
                                       'projected_added': 0, 'projected_removed': 0},
                              'old_empty': {'kind': 'removed'}, 'new_empty': {'kind': 'added'}}}},
]

if __name__ == '__main__':
    root = Path(__file__).parent
    output = root / 'additional_cases.json'
    if output.exists():
        raise SystemExit('Refusing to overwrite frozen independently authored cases')
    data = {'provenance': 'Original benign fixtures and handwritten outcomes, authored before executing this qualification or reading production implementation.', 'cases': cases}
    output.write_text(json.dumps(data, indent=2) + '\n')
    (root / 'ADDITIONAL_FREEZE.json').write_text(json.dumps({'file': output.name, 'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'case_count': len(cases), 'predictions_corrections': 0}, indent=2) + '\n')
