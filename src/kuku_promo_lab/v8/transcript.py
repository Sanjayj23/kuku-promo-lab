"""Turn parsed screenplay blocks into numbered, timed lines a model can cite.

The model never invents timestamps: it picks line IDs, and code converts them
back to exact script times. That keeps every boundary traceable to the source.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..parser import infer_speakers, parse, timecode


@dataclass
class Line:
    lid: str          # e.g. "L014", unique within the episode
    episode: int      # 1-based episode number
    kind: str         # dialogue | camera | action | sound_fx | ...
    speaker: str      # speaker name for dialogue, else ""
    emotion: str
    start_ms: int
    end_ms: int
    text: str

    @property
    def is_speech(self):
        return self.kind in ("dialogue", "ambiguous_tag")

    def render(self):
        t = f"{secs(self.start_ms)}-{secs(self.end_ms)}"
        text = " ".join(self.text.split())
        if self.is_speech:
            who = self.speaker or "VOICE"
            mood = f" ({self.emotion})" if self.emotion else ""
            return f"[{self.lid} {t}] {who}{mood}: {text}"
        label = {"camera": "SCREEN", "action": "ACTION", "sound_fx": "SFX"}.get(self.kind, self.kind.upper())
        return f"[{self.lid} {t}] {label}: {text}"


@dataclass
class EpisodeDoc:
    number: int
    episode_id: str
    source_name: str
    logline: str
    continuity: str
    duration_ms: int
    lines: list

    def by_id(self):
        return {ln.lid: ln for ln in self.lines}

    def brief(self):
        if self.logline:
            return self.logline
        speech = [ln for ln in self.lines if ln.is_speech]
        if not speech:
            return f"Episode {self.number} ({self.duration_ms / 1000:.0f}s)"
        return f"Opens: {speech[0].speaker}: {speech[0].text[:80]} / Ends: {speech[-1].speaker}: {speech[-1].text[:80]}"

    def render(self):
        return "\n".join(ln.render() for ln in self.lines)


def secs(ms):
    return f"{ms / 1000:.1f}"


def fmt_tc(ms):
    """Editor-friendly mm:ss.s timecode."""
    m, s = divmod(ms / 1000, 60)
    return f"{int(m):02d}:{s:04.1f}"


def load_episodes(files):
    """Parse every screenplay with the project's strict line-accounting parser."""
    speakers = infer_speakers(files)
    docs = []
    for f in files:
        ep = parse(f, speakers)
        number = int(ep.episode_id.split("-")[1])
        blocks = sorted(ep.blocks, key=lambda b: (b.start_ms, b.ordinal))
        lines = [Line(f"L{i + 1:03d}", number, b.kind, b.speaker, b.emotion, b.start_ms, b.end_ms, b.text)
                 for i, b in enumerate(blocks)]
        docs.append(EpisodeDoc(
            number, ep.episode_id, f["name"],
            ep.metadata.get("episode_logline", ""),
            ep.metadata.get("continuity_notes", ""),
            ep.extent_ms, lines,
        ))
    docs.sort(key=lambda d: d.number)
    return docs


__all__ = ["Line", "EpisodeDoc", "load_episodes", "fmt_tc", "secs", "timecode"]

