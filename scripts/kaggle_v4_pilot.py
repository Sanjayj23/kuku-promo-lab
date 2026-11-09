"""Private, bounded Kaggle pilot. No full traversal action exists in this helper."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request

import kaggle_runtime as pinned

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'private/v4-pilot'
RUNTIME=Path('/kaggle/temp/kuku-v4-runtime')
MODELS={
    '4b': {'alias':'qwen3-4b','repository':'Qwen/Qwen3-4B-GGUF',
           'revision':'bc640142c66e1fdd12af0bd68f40445458f3869b',
           'file':'Qwen3-4B-Q4_K_M.gguf',
           'sha256':'7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5'},
    '8b': {'alias':'qwen3-8b','repository':'Qwen/Qwen3-8B-GGUF',
           'revision':pinned.MODEL_REVISION,'file':pinned.MODEL_NAME,'sha256':pinned.MODEL_SHA256}}


def setup(model):
    gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],text=True)
    if int(gpu.splitlines()[0].split(',')[1])<14000:raise RuntimeError('Pilot needs >=14 GB GPU memory')
    RUNTIME.mkdir(parents=True,exist_ok=True);PRIVATE.mkdir(parents=True,exist_ok=True)
    pinned.RUNTIME=RUNTIME
    core={'attrs','jsonschema','jsonschema-specifications','referencing','rpds-py','typing_extensions'}
    pins=[s for s in (ROOT/'requirements.lock').read_text().splitlines() if s.split('==')[0] in core]
    if not (RUNTIME/'site-packages/jsonschema').exists():
        pinned.command([sys.executable,'-m','pip','install','--target',RUNTIME/'site-packages',*pins,'--no-deps'],'v4-dependencies.log')
    release=RUNTIME/'official-cuda-release';release.mkdir(exist_ok=True)
    for name,expected in pinned.RELEASE_ASSETS.items():
        archive=RUNTIME/name
        if not archive.exists() or pinned.sha256(archive)!=expected:
            pinned.command(['wget','--continue','--tries=3','--progress=dot:giga','-O',archive,
                f'https://github.com/ggml-org/llama.cpp/releases/download/{pinned.LLAMA_TAG}/{name}'],'v4-'+name+'.log')
        if pinned.sha256(archive)!=expected:raise RuntimeError('Pinned CUDA runtime checksum mismatch')
        with tarfile.open(archive) as bundle:bundle.extractall(release,filter='data')
    libraries=sorted({str(p.parent) for p in release.rglob('*.so*') if p.is_file()})
    env=dict(os.environ,CUDA_VISIBLE_DEVICES='0',LD_LIBRARY_PATH=os.pathsep.join(libraries+['/usr/lib64-nvidia','/usr/local/nvidia/lib64',os.environ.get('LD_LIBRARY_PATH','')]))
    servers=[p for p in release.rglob('llama-server') if p.is_file()]
    if len(servers)!=1:raise RuntimeError('Expected one pinned llama-server')
    server=servers[0]
    version=subprocess.check_output([server,'--version'],env=env,stderr=subprocess.STDOUT,text=True)
    if 'b393d73' not in version:raise RuntimeError('Runtime identity mismatch')
    spec=MODELS[model];weights=RUNTIME/spec['file']
    if not weights.exists() or pinned.sha256(weights)!=spec['sha256']:
        pinned.command(['wget','--continue','--tries=3','--progress=dot:giga','-O',weights,
            f"https://huggingface.co/{spec['repository']}/resolve/{spec['revision']}/{spec['file']}?download=true"],f'v4-model-{model}.log')
    if pinned.sha256(weights)!=spec['sha256']:raise RuntimeError('Checkpoint checksum mismatch')
    stop_server()
    args=[str(server),'-m',str(weights),'--alias',spec['alias'],'--host','127.0.0.1','--port','8080',
          '--ctx-size','16384','--parallel','1','--gpu-layers','99','--flash-attn','on',
          '--cache-type-k','q8_0','--cache-type-v','q8_0','--reasoning','off','--no-context-shift',
          '--cache-ram','0','--ctx-checkpoints','0','--no-webui','-lv','2']
    with (PRIVATE/f'server-{model}.log').open('a') as stream:
        process=subprocess.Popen(args,env=env,stdout=stream,stderr=subprocess.STDOUT)
    (PRIVATE/'server.json').write_text(json.dumps({'pid':process.pid,'weights':str(weights),'args':args},indent=2)+'\n')
    for _ in range(120):
        if process.poll() is not None:raise RuntimeError('GPU server exited; inspect private server log')
        try:
            with urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=2) as r:
                if r.status==200:break
        except OSError:pass
        time.sleep(1)
    else:raise RuntimeError('GPU server startup timeout')
    memory=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True)
    if int(memory.splitlines()[0])<1500:
        stop_server();raise RuntimeError('GPU allocation too small to substantiate expected model offload')
    config=json.loads((ROOT/'config/v4.json').read_text())
    config.update(model=spec['alias'],model_sha256=spec['sha256'],max_attempts=72,
        runtime_identity=f'llama.cpp {pinned.LLAMA_TAG} {pinned.LLAMA_COMMIT}; {gpu.splitlines()[0]}; ctx=16384; q8_0 KV; reasoning off; CUDA device0; no context shift',
        cache_dir=str(PRIVATE/f'cache-{model}'))
    (PRIVATE/f'config-{model}.json').write_text(json.dumps(config,indent=2)+'\n')
    (PRIVATE/f'runtime-{model}.json').write_text(json.dumps({'gpu':gpu,'gpu_memory_used_mib':memory,
        'checkpoint':spec,'runtime_version':version,'server_command':args,
        'official_runtime_archives':pinned.RELEASE_ASSETS,
        'helper_sha256':pinned.sha256(__file__),'source_policy':'Private notebook; loopback inference only'},indent=2)+'\n')
    print('V4 private GPU ready:',model,'checkpoint',spec['sha256'],flush=True)


def stop_server():
    state=PRIVATE/'server.json'
    if not state.exists():return
    data=json.loads(state.read_text());pid=data['pid'];proc=Path(f'/proc/{pid}/cmdline')
    if proc.exists():
        command=proc.read_bytes().replace(b'\0',b' ').decode()
        if data['weights'] not in command or str(RUNTIME) not in command:
            raise RuntimeError('Refusing to stop a process outside this isolated pilot')
        os.kill(pid,signal.SIGTERM)
        for _ in range(60):
            if not proc.exists():break
            if Path(f'/proc/{pid}/stat').read_text().split()[2]=='Z':break
            time.sleep(.5)
        else:raise RuntimeError('Previous pilot server did not exit')


def controls(model,out):
    env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(RUNTIME/'site-packages'),str(ROOT/'src')]))
    args=[sys.executable,'scripts/run_semantic_model_comparison.py','--config',str(PRIVATE/f'config-{model}.json'),
          '--out',out,'--case-ids','offer_not_acceptance,explicit_acceptance,wrong_actor_premise,ring_is_not_bell']
    subprocess.run(args,env=env,check=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['setup','controls','stop'])
    p.add_argument('--model',choices=['4b','8b'],default='4b');p.add_argument('--out')
    a=p.parse_args()
    if a.action=='setup':setup(a.model)
    elif a.action=='stop':stop_server()
    else:
        if not a.out:p.error('--out required for controls')
        controls(a.model,a.out)
