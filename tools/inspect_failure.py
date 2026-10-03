"""Inspect an authenticated failed run in Actions; publish only fixed codes/counts."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tempfile
import zipfile
from tools.envelope import decrypt

MAX_BYTES = 32 * 1024 * 1024
WORKFLOWS = {
    '.github/workflows/batch.yml': ('batch', 'result.enc'),
    '.github/workflows/localized.yml': ('localized-result', 'localized-result.enc'),
}
PATTERNS = {
    'SOURCE_COUNT': r'exactly (?:five|5)|not enough|insufficient candidates|no candidates|no eligible|selected.*0',
    'SOURCE_TEXT': r'no collected source text|source text.*(?:short|empty)|source body',
    'SOURCE_DIVERSITY': r'four publishers|publisher domains|same.publisher|section allocation|slot requires',
    'SOURCE_WINDOW': r'outside.*window|publication.*(?:invalid|window)|out.of.window|not.*recent',
    'SOURCE_LANGUAGE': r'English source title|English publisher|contains CJK|not English',
    'SOURCE_DUPLICATE': r'duplicat|must be unique',
    'SOURCE_ACCESS': r'HTTP (?:401|403|404)|access denied|forbidden|blocked|robots|fetch.*fail',
    'MODEL_PAYMENT': r'HTTP 402|insufficient balance|payment required',
    'MODEL_RATE_LIMIT': r'HTTP 429|rate.limit',
    'MODEL_RESPONSE': r'invalid JSON|did not contain a JSON|no message output|not return a completed|finish_reason.*length',
    'TIMEOUT': r'timeout|timed out|deadline',
    'BODY_LENGTH': r'body is too short|too few paragraphs|too many paragraphs',
    'EVIDENCE': r'evidence|source span|unsupported.*number|absent from the source|fact.check',
    'PDF': r'PDF|LibreOffice|soffice|page.count',
    'PDF_EXCERPT': r'PDF is missing an exact excerpt',
    'PDF_TEXT_MARKER': r'PDF text layer is missing',
    'PDF_NO_TEXT': r'PDF page.*no readable text',
    'PDF_LANGUAGE': r'PDF must contain English text only',
    'PDF_COUNT': r'PDF page count|page-count quality gate',
    'PDF_CONVERSION': r'LibreOffice conversion failed',
    'PDF_BLANK': r'Rendered PDF page.*blank',
    'PDF_FONT': r'PDF does not embed a recognized',
    'MODEL_EVIDENCE_INSUFFICIENT': r'Insufficient source evidence',
    'COMPILATION_EXHAUSTED': r'Deterministic compilation failed|Compilation retry budget exhausted',
    'PARAGRAPH_NUMBERS': r'numbers absent from.*(?:paragraph|source)',
    'PARAGRAPH_EVIDENCE': r'paragraph.*(?:evidence|span).*(?:align|match|invalid|insufficient)',
    'FACTCHECK_FAILED': r'independent fact.check|fact.check.*(?:fail|rejected)',
    'VALIDATION': r'quality gate|validation failed|contract|checksum|digest mismatch',
    'STATE': r'current or next|unpublished|state.*(?:invalid|contract)|already.published|future date',
    'EXECUTION': r'ModuleNotFoundError|ImportError|NameError|AttributeError|TypeError|KeyError',
}
PHASES = {'generate', 'validate', 'environment', 'internal', 'issue', 'runtime', 'plan', 'manifest-contract', 'generation', 'strict-validation', 'editorial', 'quality', 'render', 'artifact-validation'}

def summarize_text(value: str) -> dict:
    return {code: len(re.findall(pattern, value, re.I)) for code, pattern in PATTERNS.items()
            if re.search(pattern, value, re.I)}

def summarize_archive(path: Path) -> dict:
    result = {'schema_version': 1, 'files_inspected': 0, 'codes': {}, 'phases': [],
              'diagnostic_present': False, 'manifest_present': False}
    seen = set()
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 1000 or sum(m.size for m in members) > MAX_BYTES:
            raise ValueError('archive bounds')
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not (member.isfile() or member.isdir()):
                raise ValueError('unsafe archive entry')
            if member.isdir():
                continue
            normalized = str(name)
            if normalized in seen:
                raise ValueError('duplicate archive entry')
            seen.add(normalized)
            if name.name not in {'failure-diagnostic.json', 'failure-manifest.json', 'job.log', 'localized-job.log'}:
                continue
            raw = archive.extractfile(member).read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError('member bounds')
            text = raw.decode('utf-8')
            result['files_inspected'] += 1
            if name.name.endswith('.json'):
                data = json.loads(text)
                if not isinstance(data, dict):
                    raise ValueError('invalid diagnostic')
                if name.name == 'failure-diagnostic.json':
                    result['diagnostic_present'] = True
                    phase = data.get('phase')
                    result['phases'].append(phase if phase in PHASES else 'other')
                    returncode = data.get('returncode')
                    if type(returncode) is int and -255 <= returncode <= 255:
                        result['returncode'] = returncode
                    text = str(data.get('stderr', '')) + '\n' + str(data.get('stdout', ''))
                else:
                    result['manifest_present'] = True
                    failure = data.get('failure', {})
                    if isinstance(failure, dict):
                        phase = failure.get('phase')
                        result['phases'].append(phase if phase in PHASES else 'other')
                    metrics = []
                    for actual, minimum in re.findall(r'body is too short: ([0-9]{1,6}) chars; minimum is ([0-9]{1,6})', text):
                        metrics.append({'actual': int(actual), 'minimum': int(minimum)})
                    if metrics:
                        result['body_lengths'] = metrics[:10]
                    for section, key in [('collection', 'candidate_count'), ('quality', 'errors')]:
                        section_data = data.get(section, {})
                        if not isinstance(section_data, dict):
                            continue
                        item = section_data.get(key)
                        if key == 'errors' and isinstance(item, list):
                            result['quality_error_count'] = len(item)
                            text = '\n'.join(str(v) for v in item)
                        elif type(item) is int and 0 <= item <= 100000:
                            result['candidate_count'] = item
                    if isinstance(data.get('articles'), list):
                        result['article_count'] = len(data['articles'])
            for code, count in summarize_text(text).items():
                result['codes'][code] = result['codes'].get(code, 0) + count
    result['phases'] = sorted(set(result['phases']))
    if not result['files_inspected']:
        raise ValueError('no diagnostic records')
    return result

def api(endpoint: str, binary=False):
    completed = subprocess.run(['gh', 'api', endpoint], capture_output=True, timeout=90)
    if completed.returncode:
        raise ValueError('remote read failed')
    return completed.stdout if binary else json.loads(completed.stdout)

def inspect(run_id: int, key_file: Path) -> dict:
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('repository')
    run = api(f'repos/{repo}/actions/runs/{run_id}')
    if (run['id'] != run_id or run['head_branch'] != 'main' or run['conclusion'] != 'failure'
            or run['event'] not in {'schedule', 'workflow_dispatch'}
            or run['head_repository']['id'] != run['repository']['id']
            or run['status'] != 'completed' or run['path'] not in WORKFLOWS):
        raise ValueError('run identity')
    prefix, filename = WORKFLOWS[run['path']]
    expected = f'{prefix}-{run_id}-{run["run_attempt"]}'
    listing = api(f'repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100')
    if len(listing['artifacts']) != listing['total_count']:
        raise ValueError('incomplete inventory')
    matches = [a for a in listing['artifacts'] if a['name'] == expected]
    if len(matches) != 1:
        raise ValueError('artifact identity')
    artifact = matches[0]
    if (artifact['expired'] or not 0 < artifact['size_in_bytes'] <= MAX_BYTES
            or artifact['workflow_run']['id'] != run_id
            or artifact['workflow_run']['head_sha'] != run['head_sha']
            or not re.fullmatch(r'sha256:[0-9a-f]{64}', artifact.get('digest', ''))):
        raise ValueError('artifact contract')
    body = api(f'repos/{repo}/actions/artifacts/{artifact["id"]}/zip', binary=True)
    if len(body) != artifact['size_in_bytes'] or 'sha256:' + hashlib.sha256(body).hexdigest() != artifact['digest']:
        raise ValueError('archive digest')
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            if archive.namelist() != [filename] or archive.infolist()[0].file_size > MAX_BYTES:
                raise ValueError('zip contract')
            encrypted = root / 'result.enc'
            encrypted.write_bytes(archive.read(filename))
        plaintext = root / 'result.tar.gz'
        decrypt(encrypted, plaintext, key_file, 'result')
        summary = summarize_archive(plaintext)
    return summary | {'run_id': run_id, 'artifact_id': artifact['id'], 'artifact_sha256': artifact['digest'],
                      'source_sha': run['head_sha'], 'authenticated': True}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    args = parser.parse_args()
    try:
        if os.environ.get('GITHUB_ACTIONS') != 'true':
            raise ValueError('Actions only')
        result = inspect(args.run_id, args.key_file)
    except Exception:
        print('::error::Authenticated diagnostic inspection failed.')
        return 1
    print(json.dumps(result, sort_keys=True))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as stream:
            stream.write('```json\n' + json.dumps(result, indent=2, sort_keys=True) + '\n```\n')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
