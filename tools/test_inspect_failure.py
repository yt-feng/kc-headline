import io
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from tools.envelope import encrypt
from tools.inspect_failure import inspect, summarize_archive
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

if __name__ == '__main__':
    unittest.main()
