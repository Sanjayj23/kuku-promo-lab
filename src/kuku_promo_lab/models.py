"""Loopback-only stateless inference, genuine manual import and offline replay."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from jsonschema import Draft202012Validator

from .schemas import canonical, digest


class InferenceError(RuntimeError):
    def __init__(self, message, raw_envelope=None):
        super().__init__(message)
        self.raw_envelope = raw_envelope


def decode_response(raw_text):
    """Lossless set canonicalization; never repair text, IDs, scores or boundaries."""
    value=json.loads(raw_text)
    changes=[]
    set_fields={'evidence_ids','required_context','visual_flags','missing_context','uncertainty_flags'}
    def walk(item,path='$'):
        if isinstance(item,dict):
            for key,child in item.items():
                field_path=f'{path}.{key}'
                if key in set_fields and isinstance(child,list) and all(isinstance(x,str) for x in child):
                    unique=list(dict.fromkeys(child))
                    if len(unique)!=len(child):
                        item[key]=unique
                        changes.append({'path':field_path,'operation':'remove_exact_duplicate_strings',
                                        'removed_count':len(child)-len(unique)})
                else:walk(child,field_path)
        elif isinstance(item,list):
            for i,child in enumerate(item):walk(child,f'{path}[{i}]')
    walk(value)
    return value,changes


def check_schema(value, schema):
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        # Repair instructions must not echo prior model claims or hallucinated context.
        raise ValueError('; '.join(f'{list(e.path)}: violates {e.validator}' for e in errors[:8]))


def request_key(role, payload, schema, prompt, config):
    settings = {k: config[k] for k in ['model', 'model_sha256', 'runtime_identity', 'temperature', 'top_p', 'top_k_sampling', 'max_tokens']}
    return digest({'role': role, 'payload': payload, 'schema': schema, 'prompt': prompt, 'settings': settings})


class ModelClient:
    def __init__(self, config, run_dir, replay_dir=None, prior_dir=None):
        self.config = config
        self.logs = Path(run_dir) / 'model_calls'
        self.logs.mkdir(parents=True, exist_ok=True)
        self.cache = Path(config['cache_dir'])
        self.replay_dir = Path(replay_dir) if replay_dir else None
        self.prior_dir = Path(prior_dir) if prior_dir else None
        self.historical_reuses = 0
        if not self.replay_dir:
            self.cache.mkdir(parents=True, exist_ok=True)
        self.attempts, self.cache_hits, self.replays = 0, 0, 0
        self.reported_tokens = {'prompt_tokens': 0, 'completion_tokens': 0}
        self.usage_reported_calls = 0
        self.latency_s = 0
        self.records = []

    @property
    def usage(self):
        return {
            'tokens': self.reported_tokens if self.attempts and self.usage_reported_calls == self.attempts else 'unavailable',
            'reported_calls': self.usage_reported_calls,
            'reported_subtotal': self.reported_tokens if self.usage_reported_calls else None,
        }

    def generate(self, role, payload, schema, validate=None):
        prompt = (Path(__file__).parent / 'prompts' / f'{role}.md').read_text()
        prior = self.config.get('prior_review')
        historical = prior and role in {'analyst', 'reviewer'}
        settings = prior['config'] if historical else self.config
        key = request_key(role, payload, schema, prompt, settings)
        request = {'role': role, 'payload': payload, 'schema': schema, 'prompt': prompt, 'cache_key': key}
        if len(canonical(payload)) > self.config['max_input_chars']:
            raise InferenceError('Input exceeds configured context character limit; no truncation performed')
        cached = self.cache / role / f'{key}.json'
        source = None
        saved_dir = self.replay_dir or (self.prior_dir if historical else None)
        if saved_dir:
            matches = sorted((saved_dir/'model_calls').glob(f'{key}-*.json'))
            for p in reversed(matches):
                r = json.loads(p.read_text())
                if r.get('validated'):
                    source = r
                    break
            if source is None:
                raise InferenceError(f'No genuine saved response for replay key {key}')
            if self.replay_dir:
                self.replays += 1
            else:
                self.historical_reuses += 1
        elif cached.exists() and self.config['provider'] != 'none':
            source = json.loads(cached.read_text())
            self.cache_hits += 1
        if source is not None:
            parsed,normalizations = decode_response(source['raw_text'])
            check_schema(parsed, schema)
            if validate:
                validate(parsed)
            record = dict(source, request=request, origin='replay' if self.replay_dir else 'historical' if saved_dir else 'cache',
                          original_origin=source.get('original_origin', source.get('origin')),
                          normalizations=normalizations,normalization_policy='exact_duplicate_string_sets_v1')
            self._save(key, 0, record)
            return parsed
        errors = []
        for attempt in range(1, self.config['repair_attempts']+2):
            if self.attempts >= self.config['max_attempts']:
                raise InferenceError('Global model attempt budget exhausted')
            self.attempts += 1
            start = time.perf_counter()
            record = {'request': request, 'attempt': attempt, 'origin': self.config['provider'],
                      'validated': False, 'raw_text': '', 'usage': {'tokens': 'unavailable'}, 'model': self.config['model']}
            try:
                if self.config['provider'] == 'local':
                    raw, usage, model, finish, http_request = self._local(payload, schema, prompt, errors)
                    record.update(raw_text=raw, raw_usage=usage, model=model, finish_reason=finish, http_request=http_request)
                    if isinstance(usage, dict) and all(
                        type(usage.get(name)) is int and usage[name] >= 0 for name in self.reported_tokens
                    ):
                        record['usage'] = {'tokens': {name: usage[name] for name in self.reported_tokens}}
                        self.usage_reported_calls += 1
                        for name in self.reported_tokens:
                            self.reported_tokens[name] += usage[name]
                    if finish != 'stop':
                        raise ValueError(f'Incomplete model response: finish_reason={finish}')
                elif self.config['provider'] == 'manual':
                    folder = Path(self.config['manual_dir'])
                    folder.mkdir(parents=True, exist_ok=True)
                    target = folder/f'{key}.request.json'
                    target.write_text(json.dumps(request, indent=2, ensure_ascii=False))
                    response_path = folder/f'{key}.response.json'
                    if not response_path.exists():
                        raise InferenceError(f'Manual request awaiting real response: {target}')
                    envelope = json.loads(response_path.read_text())
                    if not all(envelope.get(k) for k in ['model', 'provenance', 'raw_text']):
                        raise ValueError('Manual response requires model, provenance and raw_text')
                    record.update(raw_text=envelope['raw_text'], model=envelope['model'], provenance=envelope['provenance'])
                else:
                    raise InferenceError('Semantic inference unavailable; no heuristic results are model reviews')
                parsed,normalizations = decode_response(record['raw_text'])
                record['normalizations']=normalizations
                record['normalization_policy']='exact_duplicate_string_sets_v1'
                check_schema(parsed, schema)
                if validate:
                    validate(parsed)
                record['validated'] = True
                record['raw_schema_valid']=not normalizations
            except (ValueError, TypeError, InferenceError, OSError, urllib.error.URLError) as exc:
                record['error'] = str(exc)
                if isinstance(exc, InferenceError) and exc.raw_envelope is not None:
                    record['raw_envelope'] = exc.raw_envelope
                errors = [str(exc)[:1600]]
            record['elapsed_s'] = round(time.perf_counter()-start, 6)
            self.latency_s += record['elapsed_s']
            self._save(key, attempt, record)
            if record['validated']:
                cached.parent.mkdir(parents=True, exist_ok=True)
                if not cached.exists():
                    cached.write_text(json.dumps(record, ensure_ascii=False, indent=2))
                return parsed
            if self.config['provider'] in {'manual', 'none'}:
                break
        raise InferenceError(f'{role} failed after {attempt} attempt(s): {errors}')

    def _save(self, key, attempt, record):
        target = self.logs/f'{key}-{attempt}.json'
        # Repeated identical packets can legitimately reuse a result within a run.
        if not target.exists():
            target.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        self.records.append({'key': key, 'role': record['request']['role'], 'origin': record['origin'],
                             'validated': record['validated'], 'elapsed_s': record.get('elapsed_s', 0),
                             'normalizations':len(record.get('normalizations',[]))})

    def _local(self, payload, schema, prompt, errors):
        endpoint = self.config['endpoint'].rstrip('/')
        url = urllib.parse.urlparse(endpoint)
        if url.scheme != 'http' or url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.username or url.password:
            raise InferenceError('Only local loopback HTTP inference is supported; source upload is disabled')
        user = canonical(payload)
        if errors:
            user += '\nYour prior response failed validation. Return a new independent response satisfying: '+canonical(errors)
        body = {'model': self.config['model'], 'messages': [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': user}],
                'temperature': self.config['temperature'], 'top_p': self.config['top_p'], 'top_k': self.config['top_k_sampling'],
                'max_tokens': self.config['max_tokens'], 'stream': False,
                'chat_template_kwargs': {'enable_thinking': False},
                'response_format': {'type': 'json_schema', 'json_schema': {'name': 'response', 'strict': True, 'schema': schema}}}
        req = urllib.request.Request(endpoint+'/chat/completions', json.dumps(body, ensure_ascii=False).encode(), {'Content-Type': 'application/json'})
        # No proxy, redirects, auth, tools or persisted conversation state.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise InferenceError('Inference redirects are prohibited')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(req, timeout=self.config['request_timeout_s']) as response:
                raw_envelope = response.read(4*1024*1024).decode('utf-8', errors='replace')
        except urllib.error.HTTPError as exc:
            detail=exc.read(4000).decode('utf-8',errors='replace')
            raise InferenceError(f'Local inference HTTP {exc.code}: {detail}') from exc
        try:
            result = json.loads(raw_envelope)
            choice = result['choices'][0]
            return choice['message']['content'], result.get('usage'), result.get('model', 'unknown'), choice.get('finish_reason'), body
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise InferenceError('Malformed inference service envelope', raw_envelope) from exc
