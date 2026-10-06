"""Independent native-semantic qualification; no production helper is an oracle."""
from pathlib import Path
import importlib.util
import unittest
import sys
import json
import os
import subprocess
import tempfile
import platform

ROOT = Path(__file__).resolve().parents[1]


def oracle():
    source = ROOT / 'qualification' / 'independent_semantics.py'
    if not source.exists():
        return None
    spec = importlib.util.spec_from_file_location('sqlitefolio_independent_oracle', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IndependentReferenceTests(unittest.TestCase):
    def test_original_frozen_12_cases_and_30_checks_remain_byte_frozen(self):
        ref = oracle()
        self.assertIsNotNone(ref, 'Independent qualification implementation is missing')
        report = ref.validate_freeze(ROOT / 'fixtures' / 'frozen')
        self.assertEqual(report['case_count'], 12)
        self.assertEqual(report['check_count'], 30)
        self.assertEqual(report['mismatches'], [])

    def test_oracle_retains_duplicate_multiplicity_and_projection_boundary(self):
        ref = oracle()
        self.assertIsNotNone(ref, 'Independent qualification implementation is missing')
        rows = ref.run_additional_references()
        duplicate = rows['duplicate_multiset']
        change = duplicate['expected_summary']['observed_changes']['tables']['bag']
        self.assertEqual(change['projected_removed'], 1)
        self.assertEqual(change['projected_added'], 0)
        rename = rows['rename_projection']['expected_summary']['observed_changes']['tables']['labels']
        self.assertEqual(rename['common_columns'], [])
        self.assertIsNone(rename['projected_added'])
        self.assertIsNone(rename['projected_removed'])

    def test_oracle_records_open_transaction_and_reopened_rollback_separately(self):
        ref = oracle()
        self.assertIsNotNone(ref, 'Independent qualification implementation is missing')
        result = ref.run_frozen_reference(ROOT / 'fixtures' / 'frozen' / '08_unique_collision_transaction.json')
        facts = result['reference']
        self.assertEqual(facts['execution']['status'], 'sqlite_error')
        self.assertTrue(facts['execution']['in_transaction'])
        self.assertEqual(facts['observed']['tables']['contacts']['columns'], ['id', 'email', 'normalized'])
        self.assertEqual(facts['reopened']['tables']['contacts']['columns'], ['id', 'email'])
        self.assertTrue(result['prediction_matches'])

    def test_oracle_keeps_blob_and_invalid_utf8_text_distinct(self):
        ref = oracle()
        self.assertIsNotNone(ref, 'Independent qualification implementation is missing')
        row = ref.run_additional_references()['typed_values']['reference']['observed']['tables']['valueset']['rows'][0]
        self.assertEqual(row[1], ['text', '/wA='])
        self.assertEqual(row[2], ['blob', '/wA='])
        self.assertEqual(row[3], ['integer', '9223372036854775807'])
        self.assertEqual(row[4], ['integer', '-9223372036854775808'])
        self.assertEqual(row[5], ['real', '7ff0000000000000'])
        self.assertEqual(row[6], ['null', None])

    def test_handwritten_additional_predictions_are_checked_without_rewriting_them(self):
        ref = oracle()
        self.assertIsNotNone(ref, 'Independent qualification implementation is missing')
        self.assertTrue(hasattr(ref, 'validate_additional_predictions'), 'Additional prediction validator is missing')
        report = ref.validate_additional_predictions()
        self.assertEqual(report['case_count'], 10)
        self.assertEqual(report['mismatches'], [])

    def test_all_frozen_auxiliary_live_and_reopened_observations_are_validated(self):
        ref = oracle()
        self.assertIsNotNone(ref, 'Independent qualification implementation is missing')
        result = ref.run_frozen_reference(ROOT / 'fixtures' / 'frozen' / '08_unique_collision_transaction.json')
        self.assertIn('auxiliary_observations', result, 'Original auxiliary predictions must also be checked')
        self.assertEqual(len(result['auxiliary_observations']), 2)
        self.assertEqual(result['prediction_errors'], [])

    def test_schema_seed_connection_settings_do_not_leak_to_candidate(self):
        ref = oracle()
        self.assertIsNotNone(ref, 'Independent qualification implementation is missing')
        case = {'id': 'fresh_profile', 'foreign_keys': True,
                'schema_sql': 'PRAGMA foreign_keys=OFF; CREATE TABLE t(x);',
                'seed_sql': 'INSERT INTO t VALUES(1);', 'migration_sql': 'SELECT 1;', 'invariants': []}
        request = ref.request_for(case)
        native = ref.reference(request)
        self.assertIn('python_build', native['runtime'], 'Exact interpreter build must be retained')
        self.assertEqual(native['runtime']['python_build'], sys.version)
        self.assertEqual(native['runtime']['implementation'], platform.python_implementation())
        self.assertEqual(native['runtime']['machine'], platform.machine())
        self.assertTrue(native['execution']['foreign_keys_start'])
        self.assertTrue(native['before']['diagnostics']['foreign_keys'])
        from sqlitefolio.runner import run_candidate
        actual = run_candidate(request)
        self.assertEqual(ref.differences(native, actual), [])

    def test_24_original_executions_match_every_native_fact_and_check(self):
        ref = oracle()
        self.assertIsNotNone(ref, 'Independent qualification implementation is missing')
        report = ref.qualify(ROOT / 'fixtures' / 'frozen', include_additional=True)
        self.assertEqual(report['frozen_execution_count'], 24)
        self.assertEqual(report['frozen_named_checks'], 30)
        self.assertEqual(report['mismatches'], [])
        self.assertIn('verifier_summary', report['executions'][0], 'Independent structural reconstruction must be compared too')
        self.assertTrue(all(not item['verifier_mismatches'] for item in report['executions']))
        self.assertTrue(all(item['source_preserved'] and item['source_directory_preserved'] for item in report['executions']))


class IndependentPacketBoundaryTests(unittest.TestCase):
    def cli(self, *args):
        env = dict(os.environ)
        import sqlitefolio
        env['PYTHONPATH'] = str(Path(sqlitefolio.__file__).resolve().parent.parent)
        return subprocess.run([sys.executable, '-m', 'sqlitefolio', *map(str, args)],
                              env=env, text=True, capture_output=True, timeout=40)

    def make_packet(self, directory, *, rows_limit=None):
        folder = Path(directory)
        (folder / 'schema.sql').write_text('CREATE TABLE bag(x INTEGER);')
        (folder / 'seed.sql').write_text('INSERT INTO bag VALUES(1),(1);')
        (folder / 'change.sql').write_text('-- Original trusted no-op candidate.\nSELECT 1;')
        manifest = {'format': 'sqlitefolio.input.v1', 'scenario': 'packet_boundary',
                    'source': {'schema': 'schema.sql', 'seed': 'seed.sql'},
                    'profile': {'foreign_keys': True, 'transaction_mode': 'autocommit'},
                    'candidates': [{'name': 'noop', 'sql': 'change.sql'}], 'invariants': []}
        if rows_limit is not None:
            manifest['limits'] = {'rows_per_query': rows_limit}
        source = folder / 'original.json'
        source.write_text(json.dumps(manifest))
        packet = folder / 'packet'
        process = self.cli('rehearse', source, '--out', packet, '--trust-input')
        self.assertEqual(process.returncode, 4 if rows_limit is not None else 0,
                         process.stdout + process.stderr)
        return source, packet

    def test_stale_raw_fact_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            _, packet = self.make_packet(folder)
            data = json.loads((packet / 'packet.json').read_text())
            data['candidates'][0]['observed']['tables']['bag']['rows'].append([['integer', '9']])
            (packet / 'packet.json').write_text(json.dumps(data))
            self.assertEqual(self.cli('verify', packet).returncode, 6)

    def test_rehashed_false_summary_is_rejected_even_with_matching_html(self):
        with tempfile.TemporaryDirectory() as folder:
            _, packet = self.make_packet(folder)
            data = json.loads((packet / 'packet.json').read_text())
            data['summary'][0]['observed_changes']['tables']['bag']['projected_added'] = 900
            from sqlitefolio.report import render
            (packet / 'packet.json').write_text(json.dumps(data))
            (packet / 'report.html').write_text(render(data))
            self.assertEqual(self.cli('verify', packet).returncode, 6)

    def test_exact_retained_sql_bytes_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            _, packet = self.make_packet(folder)
            data = json.loads((packet / 'packet.json').read_text())
            sql_path = packet / data['input']['candidates'][0]['sql']
            self.assertTrue(sql_path.is_file())
            sql_path.write_bytes(sql_path.read_bytes() + b'\n-- Changed exact bytes.\n')
            self.assertEqual(self.cli('verify', packet).returncode, 6)

    def test_consistent_fabrication_can_verify_but_source_bound_replay_rejects_it(self):
        with tempfile.TemporaryDirectory() as folder:
            original, packet = self.make_packet(folder)
            data = json.loads((packet / 'packet.json').read_text())
            candidate = data['candidates'][0]
            for phase in ('observed', 'reopened'):
                candidate[phase]['tables']['bag']['rows'] = [[['integer', '9']], [['integer', '9']]]
            data['summary'] = [oracle().summary(candidate, data['input']['invariants'])]
            from sqlitefolio.report import render
            (packet / 'packet.json').write_text(json.dumps(data))
            (packet / 'report.html').write_text(render(data))
            structural = self.cli('verify', packet)
            self.assertEqual(structural.returncode, 0, structural.stdout + structural.stderr)
            replay = self.cli('replay', packet, '--manifest', original, '--trust-input')
            self.assertEqual(replay.returncode, 6, replay.stdout + replay.stderr)

    def test_replay_binds_exact_original_sql_even_for_identical_after_state(self):
        with tempfile.TemporaryDirectory() as folder:
            original, packet = self.make_packet(folder)
            path = Path(folder) / 'change.sql'
            path.write_bytes(path.read_bytes() + b'\n-- Different original bytes, identical SQL effect.\n')
            process = self.cli('replay', packet, '--manifest', original, '--trust-input')
            self.assertEqual(process.returncode, 6, process.stdout + process.stderr)

    def test_rehashed_retained_sql_is_not_authentication_and_replay_rejects_substitution(self):
        with tempfile.TemporaryDirectory() as folder:
            original, packet = self.make_packet(folder)
            data = json.loads((packet / 'packet.json').read_text())
            name = data['input']['candidates'][0]['sql']
            path = packet / name
            path.write_bytes(path.read_bytes() + b'\n-- Substituted exact migration bytes.\n')
            data['files'][name] = oracle().sha(path.read_bytes())
            from sqlitefolio.report import render
            (packet / 'packet.json').write_text(json.dumps(data))
            (packet / 'report.html').write_text(render(data))
            structural = self.cli('verify', packet)
            self.assertEqual(structural.returncode, 0, structural.stdout + structural.stderr)
            replay = self.cli('replay', packet, '--manifest', original, '--trust-input')
            self.assertEqual(replay.returncode, 6, replay.stdout + replay.stderr)

    def test_replay_binds_source_bytes_even_if_sample_rows_are_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            original, packet = self.make_packet(folder)
            seed = Path(folder) / 'seed.sql'
            seed.write_bytes(seed.read_bytes() + b'\nSELECT 1; -- same database, different seed bytes\n')
            process = self.cli('replay', packet, '--manifest', original, '--trust-input')
            self.assertEqual(process.returncode, 6, process.stdout + process.stderr)

    def test_replay_binds_recorded_runtime_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            original, packet = self.make_packet(folder)
            data = json.loads((packet / 'packet.json').read_text())
            data['candidates'][0]['runtime']['python_build'] += ' [different build]'
            from sqlitefolio.report import render
            (packet / 'packet.json').write_text(json.dumps(data))
            (packet / 'report.html').write_text(render(data))
            process = self.cli('replay', packet, '--manifest', original, '--trust-input')
            self.assertEqual(process.returncode, 6, process.stdout + process.stderr)

    def test_real_incomplete_packet_is_consistent_but_never_zero_exit(self):
        with tempfile.TemporaryDirectory() as folder:
            _, packet = self.make_packet(folder, rows_limit=1)
            process = self.cli('verify', packet)
            self.assertEqual(process.returncode, 4, process.stdout + process.stderr)
            data = json.loads((packet / 'packet.json').read_text())
            self.assertFalse(data['summary'][0]['complete'])
            self.assertFalse(data['summary'][0]['observed_changes']['complete'])
            self.assertIsNone(data['summary'][0]['observed_changes']['objects_removed'])



if __name__ == '__main__':
    unittest.main()
