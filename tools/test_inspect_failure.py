import io
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from tools.envelope import encrypt
from tools.inspect_failure import inspect, summarize_archive, manifest_details, rule_details
import zipfile

class InspectionTests(unittest.TestCase):
    def archive(self, rows):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        path = Path(root.name) / 'test.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, value in rows:
                payload = value.encode()
                item = tarfile.TarInfo(name)
                item.size = len(payload)
                archive.addfile(item, io.BytesIO(payload))
        return path

    def test_codes_do_not_expose_private_text_or_credentials(self):
        private = 'secret-sentinel private title https://private.invalid?token=secret-sentinel'
        diagnostic = json.dumps({'phase': 'generate', 'returncode': 2,
                                 'stderr': 'HTTP 402 insufficient balance ' + private})
        result = summarize_archive(self.archive([('./output/failure-diagnostic.json', diagnostic)]))
        self.assertEqual(result['codes'], {'MODEL_PAYMENT': 2})
        self.assertNotIn('secret-sentinel', json.dumps(result))
        self.assertNotIn('private.invalid', json.dumps(result))

    def test_arbitrary_phase_is_never_echoed(self):
        result = summarize_archive(self.archive([('job.log', '{"phase":"private-secret"}')]))
        self.assertNotIn('private-secret', json.dumps(result))

    def test_manifest_counts_and_errors(self):
        value = {'collection': {'candidate_count': 31}, 'articles': [{}, {}],
                 'quality': {'errors': ['body is too short: confidential story']}}
        result = summarize_archive(self.archive([('output/failure-manifest.json', json.dumps(value))]))
        self.assertEqual(result['candidate_count'], 31)
        self.assertEqual(result['article_count'], 2)
        self.assertEqual(result['quality_error_count'], 1)
        self.assertEqual(result['codes'], {'BODY_LENGTH': 1})

    def test_specific_codes_and_lengths_never_echo_source_content(self):
        value = {'failure': {'phase': 'artifact-validation'},
                 'quality': {'errors': ['Source digest PDF is missing an exact excerpt: private-title',
                                        'body is too short: 201 chars; minimum is 900']}}
        result = summarize_archive(self.archive([('failure-manifest.json', json.dumps(value))]))
        self.assertEqual(result['codes']['PDF_EXCERPT'], 1)
        self.assertEqual(result['body_lengths'], [{'actual': 201, 'minimum': 900}])
        self.assertEqual(result['phases'], ['artifact-validation'])
        self.assertNotIn('private-title', json.dumps(result))

    def test_manifest_details_keep_text_private(self):
        result = manifest_details({'failure': {'type': 'ArtifactError', 'message': 'private headline'},
            'quality': {'errors': ['body is too short: 201 chars; minimum is 900']},
            'articles': [{'source_title': 'private headline', 'body_paragraphs': ['confi\u00addential']}]})
        self.assertEqual(result['failed_article_measurements'][0]['excerpt_measurements'][0]['soft_hyphen'], 1)
        self.assertEqual(result['body_lengths'], [{'actual': 201, 'minimum': 900}])
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('confi', json.dumps(result))

    def test_structured_terminal_diagnostics_keep_original_text_and_numbers_private(self):
        private = 'secret-private-title https://private.invalid?token=secret'
        trigger = {
            'section': 'private-section-sentinel', 'candidate_id': private, 'terminal_gate': 'deterministic_compilation',
            'compile_attempts': 3, 'rewrite_count': 2, 'factcheck_attempts': 0,
            'terminal_errors': ['body is too short: 1800 chars; minimum is 2300',
                "paragraph 2 contains numbers absent from its selected evidence: ['2026', '12345678901234567890', '-5%']",
                private],
            'revision_history': [{'gate': 'deterministic_compilation',
                'private_terminal_snapshot': {'source_window': private, 'draft': {'body_paragraphs': [private]}, 'evidence_catalog': {'private-id': private}, 'terminal': True},
                'errors': ['has too few paragraphs: 6; minimum is 8'],
                'length_repair': {'current_chars': 1600, 'gate_min_chars': 2300,
                    'generation_target_chars': 3600, 'unused_evidence_span_hints': [{'id': private, 'exact_quote': private}]},
                'evidence_span_hints': [{'paragraph': 2, 'compiled_number': '2026',
                    'selected_span_count': 3, 'maximum_span_count': 3,
                    'matching_span_ids': [private, private], 'matching_spans': [{'exact_quote': private}]}]}],
        }
        value = {'selection': {'selection_audit': {'fallback': {
            'status': 'limit_exhausted', 'attempts': 5, 'limit': 5,
            'events': [{'attempts': 1, 'trigger': trigger}],
            'terminal_failure': {'message': private, 'terminal': trigger},
        }}}}
        result = manifest_details(value)
        records = result['compilation_records']
        self.assertEqual(len(records), 2)
        self.assertNotIn('section', records[0])
        self.assertEqual(records[0]['terminal_rules'][0], {'rule': 'BODY_TOO_SHORT', 'actual': 1800, 'minimum': 2300})
        self.assertEqual(records[0]['terminal_rules'][1]['unsupported_count'], 3)
        self.assertEqual([row['kind'] for row in records[0]['terminal_rules'][1]['number_shapes']], ['year_like', 'integer', 'percent'])
        revision = records[0]['revisions'][0]
        self.assertEqual(revision['numeric_hints'][0]['matching_span_count'], 2)
        self.assertEqual(revision['length_repair']['unused_span_hint_count'], 1)
        public = json.dumps(result)
        for text in (private, 'private-section-sentinel', 'private.invalid', 'exact_quote', '2026', '12345678901234567890', '-5%'):
            self.assertNotIn(text, public)

    def test_unknown_rules_and_numeric_payloads_are_not_echoed(self):
        for error in ('body is too short: private story',
                      "paragraph 2 contains numbers absent from its selected evidence: ['private-secret']",
                      "contains numbers absent from the source: {'private-secret': 'credential'}",
                      None):
            result = rule_details(error)
            self.assertNotIn('private-secret', json.dumps(result))
            self.assertNotIn('credential', json.dumps(result))

    def test_terminal_record_fields_have_strict_types_and_bounds(self):
        trigger = {'section': 'private-secret', 'terminal_gate': 'private-secret',
            'compile_attempts': True, 'rewrite_count': -1, 'factcheck_attempts': 100001,
            'terminal_errors': ['private-secret'] * 30,
            'revision_history': [{'length_repair': {'current_chars': True}}] * 12}
        result = manifest_details({'selection': {'selection_audit': {'fallback': {
            'events': [{'trigger': trigger}] * 12}}}})
        self.assertEqual(len(result['compilation_records']), 5)
        row = result['compilation_records'][0]
        self.assertEqual(len(row['terminal_rules']), 20)
        self.assertEqual(len(row['revisions']), 8)
        for key in ('compile_attempts', 'rewrite_count', 'factcheck_attempts', 'section', 'terminal_gate'):
            self.assertNotIn(key, row)
        self.assertNotIn('private-secret', json.dumps(result))

    def test_archive_exposes_terminal_trigger_rule_measurements(self):
        value = {'failure': {'type': 'DeterministicCompilationExhausted', 'phase': 'editorial'},
            'selection': {'selection_audit': {'fallback': {'status': 'limit_exhausted',
                'replacement_failure': {'terminal': {'terminal_gate': 'deterministic_compilation',
                    'terminal_errors': ['has too many paragraphs: 19; maximum is 18']}}}}}}
        result = summarize_archive(self.archive([('failure-manifest.json', json.dumps(value))]))
        self.assertEqual(result['compilation_records'][0]['terminal_rules'][0],
                         {'rule': 'PARAGRAPHS_TOO_MANY', 'actual': 19, 'maximum': 18})

    def test_typed_source_failure_and_no_replacement_status_are_recognized(self):
        result = manifest_details({'failure': {'type': 'SourceEvidenceInsufficient'},
            'selection': {'selection_audit': {'fallback': {'status': 'no_valid_replacement'}}}})
        self.assertEqual(result['failure_type'], 'SourceEvidenceInsufficient')
        self.assertEqual(result['fallback_status'], 'no_valid_replacement')

    def test_rejects_unsafe_paths(self):
        with self.assertRaises(ValueError):
            summarize_archive(self.archive([('../job.log', 'error')]))

    def test_rejects_duplicate_entries(self):
        with self.assertRaises(ValueError):
            summarize_archive(self.archive([('job.log', 'a'), ('./job.log', 'b')]))

    def test_rejects_missing_diagnostics(self):
        with self.assertRaises(ValueError):
            summarize_archive(self.archive([('unrelated.txt', 'error')]))

    def fixture(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        folder = Path(root.name)
        key = folder / 'key'
        key.write_text('01' * 32)
        encrypted = folder / 'result.enc'
        encrypt(self.archive([('job.log', 'HTTP 402 insufficient balance')]), encrypted, key, 'result')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('result.enc', encrypted.read_bytes())
        body = buffer.getvalue()
        run = {'id': 123, 'head_branch': 'main', 'conclusion': 'failure', 'status': 'completed',
               'path': '.github/workflows/batch.yml', 'event': 'schedule', 'head_sha': 'a' * 40,
               'repository': {'id': 1}, 'head_repository': {'id': 1}, 'run_attempt': 1}
        metadata = {'id': 456, 'name': 'batch-123-1', 'expired': False, 'size_in_bytes': len(body),
                    'digest': 'sha256:' + hashlib.sha256(body).hexdigest(),
                    'workflow_run': {'id': 123, 'head_sha': 'a' * 40}}
        return key, run, metadata, body

    def test_authenticated_cloud_path(self):
        key, run, metadata, body = self.fixture()
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'owner/repo'}), patch(
            'tools.inspect_failure.api', side_effect=[run, {'total_count': 1, 'artifacts': [metadata]}, body]
        ):
            result = inspect(123, key)
        self.assertTrue(result['authenticated'])
        self.assertEqual(result['codes'], {'MODEL_PAYMENT': 2})

    def test_refuses_foreign_source_before_artifact_read(self):
        key, run, _, _ = self.fixture()
        run['head_repository']['id'] = 2
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'owner/repo'}), patch(
            'tools.inspect_failure.api', return_value=run
        ) as api, self.assertRaises(ValueError):
            inspect(123, key)
        self.assertEqual(api.call_count, 1)

    def test_refuses_truncated_download_before_decrypt(self):
        key, run, metadata, body = self.fixture()
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'owner/repo'}), patch(
            'tools.inspect_failure.api', side_effect=[run, {'total_count': 1, 'artifacts': [metadata]}, body[:-1]]
        ), patch('tools.inspect_failure.decrypt') as decrypt, self.assertRaises(ValueError):
            inspect(123, key)
        decrypt.assert_not_called()

    def test_refuses_wrong_envelope_key(self):
        key, run, metadata, body = self.fixture()
        key.write_text('02' * 32)
        with patch.dict('os.environ', {'GITHUB_REPOSITORY': 'owner/repo'}), patch(
            'tools.inspect_failure.api', side_effect=[run, {'total_count': 1, 'artifacts': [metadata]}, body]
        ), self.assertRaises(Exception):
            inspect(123, key)


class TerminalShapeTests(unittest.TestCase):
    def test_exact_private_draft_only_emits_shape_and_feasibility(self):
        from tools.inspect_failure import terminal_shape
        import hashlib
        secret='private-sentinel secret text https://private.invalid?token=hidden'
        draft={'body_paragraphs':[secret]*27,'paragraph_evidence_ids':[['private-span']]*27}
        snapshot={'schema_version':1,'terminal':True,'draft_complete':True,'draft':draft,
            'draft_sha256':hashlib.sha256(json.dumps(draft,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        result=terminal_shape(snapshot)
        self.assertEqual(result['paragraph_count'],27)
        self.assertEqual(result['minimum_adjacent_groups_with_all_evidence'],1)
        for value in ('private-sentinel','private-span','private.invalid','hidden'):
            self.assertNotIn(value,json.dumps(result))
        snapshot['draft_sha256']='a'*64
        self.assertIsNone(terminal_shape(snapshot))

    def test_all_citations_remain_in_the_partition_bound(self):
        from tools.inspect_failure import terminal_shape
        import hashlib
        draft={'body_paragraphs':['text']*27,'paragraph_evidence_ids':[[f'private-{i}-{j}' for j in range(3)] for i in range(27)]}
        snapshot={'schema_version':1,'terminal':True,'draft_complete':True,'draft':draft,'draft_sha256':hashlib.sha256(json.dumps(draft,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        self.assertEqual(terminal_shape(snapshot)['minimum_adjacent_groups_with_all_evidence'],27)

if __name__ == '__main__':
    unittest.main()
