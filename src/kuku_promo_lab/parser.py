"""Line-accounting parser for the documented timestamped screenplay format."""
from __future__ import annotations

import re
from decimal import Decimal

from .schemas import Block, Episode, Scene


class ParseError(ValueError):
    pass


def milliseconds(raw):
    raw = raw.strip().strip('()')
    if not re.fullmatch(r'(?:\d+:)?\d{1,3}:\d{2}(?:\.\d{1,3})?', raw):
        raise ParseError(f"Invalid timestamp: {raw!r}")
    parts = raw.split(':')
    seconds = Decimal(parts[-1])
    minutes = int(parts[-2])
    hours = int(parts[0]) if len(parts) == 3 else 0
    if seconds >= 60 or (len(parts) == 3 and minutes >= 60):
        raise ParseError(f"Invalid clock component: {raw}")
    return int((hours * 3600 + minutes * 60 + seconds) * 1000)


def timecode(ms):
    if type(ms) is not int or ms < 0:
        raise ValueError('Nonnegative integer milliseconds required')
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, fraction = divmod(rem, 1000)
    return f'{h:02d}:{m:02d}:{s:02d}.{fraction:03d}'


HEADER = re.compile(r'^\s*\*\*\s*(.+?)\s*:\s*\*\*\s*$', re.I)
SCENE = re.compile(r'^\s*SCENE\s+(\d+)\s*$', re.I)
TIMES = re.compile(r'^\s*Start\s*:\s*\(?([\d:.]+)\)?\s*\|\s*End\s*:\s*\(?([\d:.]+)\)?\s*$', re.I)
SCENE_FIELD = re.compile(r'^Scene\s+(heading|start|end)\s*:\s*(.*)', re.I)
NON_DIALOGUE = {
    'CAMERA': 'camera', 'ACTION': 'action', 'SOUND FX': 'sound_fx', 'SFX': 'sound_fx',
    'LIGHTING': 'lighting', 'MUSIC': 'music', 'VFX': 'vfx', 'TRANSITION': 'transition',
    'TEXT': 'text', 'SUPER': 'super', 'TITLE': 'title', 'INSERT': 'insert',
}


def speaker_label(label):
    return ' '.join(re.sub(r'\([^)]*\)', '', label).split()).upper()


def infer_speakers(sources):
    """A quoted timed block establishes a speaker label for the compatible format."""
    speakers = set()
    for source in sources:
        lines = [line.strip() for line in source['text'].splitlines() if line.strip()]
        for i, line in enumerate(lines[:-2]):
            header = HEADER.fullmatch(line)
            if not header or not TIMES.fullmatch(lines[i+1]):
                continue
            label = header[1]
            name = speaker_label(label)
            if name and name not in NON_DIALOGUE and '(' in label and lines[i+2].startswith(('"', '“')):
                speakers.add(name)
    return speakers


def parse(source, speakers=None, *, legacy=False):
    if speakers is None and not legacy:
        speakers = infer_speakers([source])
    lines = source['text'].splitlines(keepends=True)
    stripped = [x.strip() for x in lines]
    ep = None
    metadata, scenes, blocks, warnings, errors = {}, [], [], [], []
    current = None
    scene_fields = {}
    index = 0
    while index < len(lines):
        line = stripped[index]
        if not line:
            index += 1
            continue
        meta = re.fullmatch(r'(EPISODE\s+NUMBER|EPISODE\s+LOGLINE|CONTINUITY\s+NOTES|TITLE)\s*:\s*(.*)', line, re.I)
        if meta:
            key, value = meta.groups()
            key = re.sub(r'\s+', '_', key.lower())
            if key in metadata:
                errors.append(f'line {index+1}: duplicate {key}')
            metadata[key] = value
            if key == 'episode_number':
                number = re.fullmatch(r'(?:Episode\s*)?(\d+)', value, re.I)
                if not number:
                    errors.append(f'line {index+1}: invalid episode identifier')
                else:
                    ep = f'ep-{int(number[1]):03d}'
            index += 1
            continue
        if re.fullmatch(r'-+\s*SCRIPT\s+(START|END)\s*-+', line, re.I) or re.fullmatch(r'Actions\s+and\s+dialogue\s*:', line, re.I):
            index += 1
            continue
        scene_match = SCENE.fullmatch(line)
        if scene_match:
            sid = f'scene-{int(scene_match[1]):03d}'
            if any(s.scene_id == sid for s in scenes):
                errors.append(f'line {index+1}: duplicate scene identifier')
            current = Scene(sid, line_start=index+1)
            scenes.append(current)
            scene_fields = {}
            index += 1
            continue
        scene_meta = SCENE_FIELD.fullmatch(line)
        if scene_meta:
            if current is None:
                errors.append(f'line {index+1}: scene metadata without scene')
            else:
                key, value = scene_meta.groups()
                key = key.lower()
                if key in scene_fields:
                    errors.append(f'{ep or source["name"]} {current.scene_id} line {index+1}: '
                                  f'duplicate Scene {key}: {scene_fields[key]!r} and {value!r}')
                    index += 1
                    continue
                scene_fields[key] = value
                try:
                    setattr(current, 'heading' if key.lower() == 'heading' else key.lower()+'_ms',
                            value if key.lower() == 'heading' else milliseconds(value))
                except ParseError as exc:
                    errors.append(f'line {index+1}: {exc}')
            index += 1
            continue
        header = HEADER.fullmatch(line)
        if header:
            begin = index
            label = header[1].strip()
            index += 1
            while index < len(lines) and not stripped[index]:
                index += 1
            timing = TIMES.fullmatch(stripped[index]) if index < len(lines) else None
            if not timing or current is None:
                errors.append(f'line {begin+1}: typed block lacks timing or scene')
                continue
            index += 1
            text_begin = index
            while index < len(lines) and not HEADER.fullmatch(stripped[index]) and not SCENE.fullmatch(stripped[index]) and not SCENE_FIELD.fullmatch(stripped[index]) and not re.match(r'-+\s*SCRIPT\s+END', stripped[index], re.I):
                index += 1
            end = index
            while end > text_begin and not stripped[end-1]:
                end -= 1
            text = ''.join(lines[text_begin:end]).strip()
            try:
                start, finish = map(milliseconds, timing.groups())
                if finish <= start:
                    raise ParseError('Block end must exceed start')
                base = re.sub(r'\([^)]*\)', '', label).strip()
                name = speaker_label(label)
                kind = NON_DIALOGUE.get(name)
                if legacy and name not in {'CAMERA', 'ACTION', 'SOUND FX'}:
                    kind = None
                if kind is None:
                    if not re.search(r'\([^)]*\)', label):
                        raise ParseError(f'Unknown typed block {label!r}; dialogue requires a parenthetical tag')
                    kind = 'dialogue' if legacy or name in speakers else 'ambiguous_tag'
                if not text:
                    raise ParseError('Empty block text')
                tags = re.findall(r'\(([^)]*)\)', label)
                delivery = re.findall(r'\b(?:V\.O\.|O\.S\.)', label, re.I)
                emotion = ', '.join(re.sub(r'\b(?:V\.O\.|O\.S\.)', '', t, flags=re.I).strip(' ,') for t in tags).strip(' ,')
                bid = f'{ep or "unknown"}:b{len(blocks)+1:04d}'
                if kind == 'ambiguous_tag':
                    warnings.append({'code': kind, 'block_id': bid, 'scene_id': current.scene_id,
                                     'line': begin+1, 'label': label})
                blocks.append(Block(bid, current.scene_id, kind, label, base if kind == 'dialogue' else '',
                                    [x.upper() for x in delivery], emotion, start, finish, timing[1], timing[2],
                                    text, ''.join(lines[begin:end]), begin+1, end, len(blocks)+1))
            except ParseError as exc:
                errors.append(f'line {begin+1}: {exc}')
            continue
        errors.append(f'line {index+1}: unaccounted material: {line[:100]}')
        index += 1
    if not ep or not scenes or not blocks:
        errors.append('Missing episode identifier, scenes or timed blocks')
    declarations = [s.end_ms for s in scenes if s.end_ms is not None]
    if declarations and len(declarations) != len(scenes):
        errors.append('Partially declared scene extents')
    extent = max(declarations) if declarations else max((b.end_ms for b in blocks), default=0)
    for scene in scenes:
        timed = scene.start_ms is not None and scene.end_ms is not None
        if (scene.start_ms is None) != (scene.end_ms is None):
            errors.append(f'{scene.scene_id}: incomplete scene timing')
        if timed and scene.end_ms <= scene.start_ms:
            errors.append(f'{scene.scene_id}: reversed scene timing')
        for b in [b for b in blocks if b.scene_id == scene.scene_id]:
            if b.end_ms > extent:
                errors.append(f'{b.block_id}: block beyond declared episode bound')
            if timed and (b.start_ms < scene.start_ms or b.end_ms > scene.end_ms):
                warnings.append({'code': 'scene_overrun', 'block_id': b.block_id, 'scene_id': scene.scene_id})
    speech = sorted((b for b in blocks if b.kind == 'dialogue'), key=lambda b: (b.start_ms, b.end_ms, b.ordinal))
    for i, a in enumerate(speech):
        for b in speech[i+1:]:
            if b.start_ms >= a.end_ms:
                break
            warnings.append({'code': 'dialogue_overlap', 'block_ids': [a.block_id, b.block_id]})
    if errors:
        raise ParseError('; '.join(errors))
    return Episode(ep, source['name'], source['sha256'], metadata, scenes, blocks, extent,
                   'scene_declarations' if declarations else 'block_extent', warnings)
