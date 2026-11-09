"""Build a generic notebook and a separate private, exact-byte input transport."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--notebook', required=True)
    parser.add_argument('--transport', required=True)
    parser.add_argument('--dataset-handle', required=True, help='Private Kaggle dataset handle, owner/slug')
    args = parser.parse_args()
    source = Path(args.source).read_bytes()
    original = Path(args.input).read_bytes()
    manifest = {'encoding': 'base64-original-zip-v1', 'file': 'screenplays.zip.b64',
                'original_sha256': hashlib.sha256(original).hexdigest()}
    target = Path(args.transport)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise SystemExit('Transport already exists')
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('screenplays.zip.b64', base64.b64encode(original))
        archive.writestr('input-manifest.json', json.dumps(manifest, indent=2) + '\n')
    encoded = base64.b64encode(source).decode()
    source_sha = hashlib.sha256(source).hexdigest()
    bootstrap = f'''import base64, hashlib, io, os, zipfile
from pathlib import Path
PRIVATE_DATASET_HANDLE = {json.dumps(args.dataset_handle)}
SOURCE_ZIP_B64 = "{encoded}"
data = base64.b64decode(SOURCE_ZIP_B64, validate=True)
assert hashlib.sha256(data).hexdigest() == "{source_sha}"
root = Path('/kaggle/working/kuku_promo_lab')
root.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    for member in archive.infolist():
        destination = (root / member.filename).resolve()
        assert root.resolve() in destination.parents
    archive.extractall(root)
os.chdir(root)
print('Generic application source verified:', "{source_sha}")
'''
    def md(text):
        return {'cell_type': 'markdown', 'metadata': {}, 'source': text.splitlines(True)}
    def code(text):
        return {'cell_type': 'code', 'execution_count': None, 'metadata': {}, 'outputs': [], 'source': text.splitlines(True)}
    cells = [md('''# Kuku Promo Lab — private GPU execution

Keep this notebook, its outputs, and the attached input dataset **Private**.
The generic source ZIP embedded below contains no screenplay data. Attach the
    separate private transport ZIP using Upload Input. The configured dataset
    handle must match that private dataset. Enable GPU T4 and Internet.
Inference stays on loopback inside this Kaggle VM; public downloads are pinned.
Run cells in order. Errors stop the affected stage and preserve real logs.
'''), code(bootstrap)]
    stages = [('setup', 'Load pinned official CUDA runtime and verify model weights'),
              ('verify', 'Run automated tests and genuine model smoke check'),
              ('full', 'Analyze every attached episode and independently validate outputs'),
              ('replay', 'Replay saved genuine responses with zero inference and compare recommendations'),
              ('synthetic', 'Run a different-genre input with an empty cache and genuine inference'),
              ('bundle', 'Package private raw outputs for download')]
    for stage, title in stages:
        mount = ''
        if stage == 'full':
            mount = ("import hashlib, json, kagglehub\n"
                     "from importlib.metadata import version\n"
                     "private_dataset_path = kagglehub.dataset_download(PRIVATE_DATASET_HANDLE)\n"
                     "Path('private/input-mount-audit.json').write_text(json.dumps({'dataset':PRIVATE_DATASET_HANDLE,'path':private_dataset_path,'kagglehub_version':version('kagglehub'),'runtime_helper_sha256':hashlib.sha256(Path('scripts/kaggle_runtime.py').read_bytes()).hexdigest()},indent=2)+'\\n')\n")
        cells += [md('## ' + title + '\n'), code(
            "import subprocess, sys\n"
            + mount +
            f"subprocess.run([sys.executable, 'scripts/kaggle_runtime.py', '{stage}'], check=True)\n")]
    cells.append(md('''Download `/kaggle/working/Kuku_Private_GPU_Results.zip` from Output.
Human review and video verification remain pending. Automated scores do not
establish advertising performance. Preserve privacy when saving a version.
'''))
    for index, cell in enumerate(cells):
        cell['id'] = f'kuku-cell-{index:02d}'
    notebook = {'cells': cells, 'metadata': {'kernelspec': {'display_name': 'Python 3', 'language': 'python', 'name': 'python3'},
                'language_info': {'name': 'python'}, 'kaggle': {'isGpuEnabled': True, 'isInternetEnabled': True}},
                'nbformat': 4, 'nbformat_minor': 5}
    path = Path(args.notebook)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SystemExit('Notebook already exists')
    path.write_text(json.dumps(notebook, indent=1) + '\n')
    print(json.dumps({'notebook': str(path.resolve()), 'private_transport': str(target.resolve()),
                      'source_sha256': source_sha, 'original_zip_sha256': manifest['original_sha256']}, indent=2))


if __name__ == '__main__':
    main()
