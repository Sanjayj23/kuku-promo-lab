"""Private Kaggle GPU execution of the unchanged reusable application.

This helper downloads public, pinned software. Screenplays are read only from
the separately attached private input transport, never from an external API.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'private'
RUNTIME = Path('/kaggle/temp/kuku-runtime')
MODEL_REVISION = '7c41481f57cb95916b40956ab2f0b139b296d974'
MODEL_NAME = 'Qwen3-8B-Q4_K_M.gguf'
MODEL_SHA256 = 'd98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785'
LLAMA_TAG = 'b12114'
LLAMA_COMMIT = 'b393d73bb0448ad101fe84cc77f3eee465fb947d'
RELEASE_ASSETS = {
    'llama-b12114-bin-ubuntu-cuda-12.8-x64.tar.gz': '30f7231a10dc51505f2d327dfa56b18f43347a130d7d98cf4a74ecea67e053ed',
    'cudart-llama-b12114-bin-ubuntu-cuda-12.8-x64.tar.gz': '15835a2ff9a906ba986e016af5e67fdd2e760149fd64d7b9979a71f2eb38a7ea',
}
PYTHON = Path(sys.executable)
CONFIG = PRIVATE / 'kaggle-config.json'
INPUT = PRIVATE / 'input/source.zip'
FULL = PRIVATE / 'runs/kaggle-full-01'
SYNTHETIC = PRIVATE / 'runs/kaggle-synthetic-01'


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def command(args, log_name, *, env=None, check=True):
    PRIVATE.mkdir(exist_ok=True)
    logs = PRIVATE / 'runtime-logs'
    logs.mkdir(exist_ok=True)
    print('Running:', ' '.join(map(str, args)), flush=True)
    process_env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(RUNTIME / 'site-packages'), str(ROOT / 'src')]))
    if env:
        process_env.update(env)
    log_path = logs / log_name
    attempt = 1
    while log_path.exists():
        attempt += 1
        log_path = logs / f'{Path(log_name).stem}-attempt-{attempt:02d}{Path(log_name).suffix}'
    with log_path.open('x') as output:
        process = subprocess.Popen(list(map(str, args)), cwd=ROOT, env=process_env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
        for line in process.stdout:
            output.write(line)
            output.flush()
            print(line, end='', flush=True)
        code = process.wait()
    if check and code:
        raise RuntimeError(f'Command failed ({code}); inspect {log_name}')
    return code


def setup():
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer is required')
    gpu = subprocess.check_output(['nvidia-smi', '--query-gpu=name,memory.total,compute_cap',
                                   '--format=csv,noheader,nounits'], text=True)
    print(gpu)
    first = gpu.splitlines()[0].split(',')
    if int(first[1].strip()) < 14000:
        raise RuntimeError('At least 14 GB GPU memory is required')
    RUNTIME.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(exist_ok=True)
    core_names = {'attrs', 'jsonschema', 'jsonschema-specifications', 'referencing', 'rpds-py', 'typing_extensions'}
    pins = [line for line in (ROOT / 'requirements.lock').read_text().splitlines()
            if line.split('==')[0] in core_names]
    # Kaggle's Python image can disable ensurepip. A separate target directory
    # isolates the pinned core packages without changing image-wide packages.
    command([PYTHON, '-m', 'pip', 'install', '--target', RUNTIME / 'site-packages',
             *pins, '--no-deps'], 'dependencies.log')
    release = RUNTIME / 'official-cuda-release'
    release.mkdir(exist_ok=True)
    for name, expected in RELEASE_ASSETS.items():
        archive_path = RUNTIME / name
        if not archive_path.exists() or sha256(archive_path) != expected:
            url = f'https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_TAG}/{name}'
            command(['wget', '--continue', '--tries=3', '--progress=dot:giga', '-O', archive_path, url], name + '.log')
        if sha256(archive_path) != expected:
            raise RuntimeError('Official runtime archive checksum mismatch')
        with tarfile.open(archive_path) as archive:
            archive.extractall(release, filter='data')
    libraries = sorted({str(p.parent) for p in release.rglob('*.so*') if p.is_file()})
    os.environ['LD_LIBRARY_PATH'] = os.pathsep.join(libraries + ['/usr/lib64-nvidia', '/usr/local/nvidia/lib64', os.environ.get('LD_LIBRARY_PATH', '')])
    servers = [p for p in release.rglob('llama-server') if p.is_file()]
    if len(servers) != 1:
        raise RuntimeError('Expected exactly one official llama-server binary')
    server = servers[0]
    version = subprocess.check_output([server, '--version'], stderr=subprocess.STDOUT, text=True)
    if 'b393d73' not in version:
        raise RuntimeError('Unexpected official runtime identity')
    print(version)
    model = RUNTIME / MODEL_NAME
    if not model.exists() or sha256(model) != MODEL_SHA256:
        url = f'https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/{MODEL_REVISION}/{MODEL_NAME}?download=true'
        command(['wget', '--continue', '--tries=3', '--progress=dot:giga', '-O', model, url], 'download.log')
    if sha256(model) != MODEL_SHA256 or model.open('rb').read(4) != b'GGUF':
        raise RuntimeError('Model checksum or GGUF header mismatch')
    args = [str(server), '-m', str(model), '--alias', 'qwen3-8b', '--host', '127.0.0.1',
            '--port', '8080', '--ctx-size', '24576', '--parallel', '1', '--gpu-layers', '99',
            '--flash-attn', 'on', '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
            '--reasoning', 'off', '--no-context-shift', '--cache-ram', '0',
            '--ctx-checkpoints', '0', '--no-webui', '-lv', '4']
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='0')
    log = (PRIVATE / 'runtime-logs/server.log').open('a')
    process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env)
    (PRIVATE / 'server-pid.txt').write_text(str(process.pid))
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError('GPU server exited; inspect runtime-logs/server.log')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2) as response:
                if response.status == 200:
                    break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('GPU server did not become ready')
    memory = subprocess.check_output(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'], text=True)
    if int(memory.splitlines()[0]) < 3000:
        process.terminate()
        raise RuntimeError('GPU memory allocation did not support expected model offload; no CPU fallback claimed as GPU')
    config = json.loads((ROOT / 'config/default.json').read_text())
    config.update(model='qwen3-8b', model_sha256=MODEL_SHA256,
                  runtime_identity=f'llama.cpp {LLAMA_TAG} {LLAMA_COMMIT}, official CUDA 12.8 release, {first[0].strip()}, ctx=24576, q8_0 KV, reasoning off, no context shift',
                  max_families=12, max_base_windows=48,
                  cache_dir=str(PRIVATE / 'cache-8b'), manual_dir=str(PRIVATE / 'manual'))
    CONFIG.write_text(json.dumps(config, indent=2) + '\n')
    (PRIVATE / 'runtime-metadata.json').write_text(json.dumps({
        'gpu': gpu, 'model_revision': MODEL_REVISION, 'model_sha256': MODEL_SHA256,
        'llama_commit': LLAMA_COMMIT, 'server_command': args, 'visible_devices': '0',
        'official_runtime_assets_sha256': RELEASE_ASSETS, 'runtime_version': version,
        'gpu_memory_used_mib': memory, 'library_path': os.environ['LD_LIBRARY_PATH'],
        'runtime_helper_sha256': sha256(__file__),
        'source_policy': 'Private Kaggle dataset; inference loopback inside this VM only.'}, indent=2) + '\n')
    print('GPU server ready. Screenplays have not yet been read.')


def decode_input():
    manifests = list(Path('/kaggle/input').rglob('input-manifest.json'))
    if len(manifests) != 1:
        raise RuntimeError('Attach exactly one private input transport dataset')
    manifest = json.loads(manifests[0].read_text())
    if manifest.get('encoding') != 'base64-original-zip-v1' or manifest.get('file') != 'screenplays.zip.b64':
        raise RuntimeError('Unexpected input transport manifest')
    original = base64.b64decode((manifests[0].parent / manifest['file']).read_bytes(), validate=True)
    if hashlib.sha256(original).hexdigest() != manifest['original_sha256']:
        raise RuntimeError('Original ZIP transport checksum mismatch')
    INPUT.parent.mkdir(parents=True, exist_ok=True)
    INPUT.write_bytes(original)
    print('Original private ZIP decoded and checksum verified.')


def cli(args, log_name, check=True):
    return command([PYTHON, '-m', 'kuku_promo_lab.cli', *args], log_name, check=check)


def verify():
    command([PYTHON, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], 'tests.log')
    cli(['doctor', '--config', CONFIG, '--out', PRIVATE / 'doctor'], 'doctor.log')


def full():
    decode_input()
    code = cli(['run', '--config', CONFIG, '--input', INPUT, '--out', FULL], 'full-pack.log', check=False)
    if not (FULL / 'run_manifest.json').exists():
        raise RuntimeError('Full run did not finalize; raw diagnostic logs retained')
    cli(['validate', '--input', INPUT, '--run', FULL], 'full-validation.json')
    cli(['evaluate', '--run', FULL, '--out', PRIVATE / 'evaluation/full'], 'full-evaluation.log')
    if code:
        print('Run finalized with explicit failures; inspect coverage and failures before calling it complete.')


def replay():
    target = PRIVATE / 'runs/kaggle-replay-01'
    cli(['replay', '--input', INPUT, '--run', FULL, '--out', target], 'replay.log', check=False)
    original = json.loads((FULL / 'promos.json').read_text())
    replayed = json.loads((target / 'promos.json').read_text())
    manifest = json.loads((target / 'run_manifest.json').read_text())
    same = original['recommendations'] == replayed['recommendations']
    result = {'recommendations_identical': same, 'inference_attempts': manifest['inference']['attempts'],
              'replays': manifest['inference']['replays'], 'run_status': manifest['run_status']}
    (PRIVATE / 'replay-comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    if not same or result['inference_attempts'] != 0:
        raise RuntimeError('Offline replay comparison failed')
    cli(['validate', '--input', INPUT, '--run', target], 'replay-validation.json')


def synthetic():
    config = json.loads(CONFIG.read_text())
    config['cache_dir'] = str(PRIVATE / 'new-input-fresh-cache')
    path = PRIVATE / 'synthetic-config.json'
    path.write_text(json.dumps(config, indent=2) + '\n')
    fixtures = ROOT / 'tests/fixtures/synthetic'
    cli(['run', '--config', path, '--input', fixtures, '--out', SYNTHETIC], 'synthetic.log')
    cli(['validate', '--input', fixtures, '--run', SYNTHETIC], 'synthetic-validation.json')
    manifest = json.loads((SYNTHETIC / 'run_manifest.json').read_text())
    if manifest['inference']['cache_hits'] or not manifest['inference']['attempts']:
        raise RuntimeError('New-input check did not use fresh genuine inference')
    cli(['evaluate', '--run', SYNTHETIC, '--out', PRIVATE / 'evaluation/synthetic'], 'synthetic-evaluation.log')


def bundle():
    target = Path('/kaggle/working/Kuku_Private_GPU_Results.zip')
    if target.exists():
        raise RuntimeError('Results ZIP exists; preserve it and choose a new name')
    files = [p for p in PRIVATE.rglob('*') if p.is_file() and 'input' not in p.relative_to(PRIVATE).parts]
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, Path('private') / path.relative_to(PRIVATE))
    print(json.dumps({'results': str(target), 'sha256': sha256(target), 'files': len(files)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=['setup', 'verify', 'full', 'replay', 'synthetic', 'bundle'])
    globals()[parser.parse_args().stage]()
