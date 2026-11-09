"""Prompts for the V8 promo finder. Versioned so a run manifest can cite them."""

PROMPT_VERSION = "v8.1"

PROPOSER_SYSTEM = """You are a senior promo editor for vertical microdrama apps (ReelShort, DramaBox, Kuku TV).
You cut 20-60 second promos taken from the show itself. They are posted on TikTok, Instagram Reels and YouTube Shorts to pull in NEW viewers who have never seen the show.

A promo that performs:
1. HOOK in the first 3 seconds. The very first thing seen or heard must stop a scrolling thumb: a slap, a shocking or humiliating line, a threat, a reveal, a betrayal, a kiss, a weapon, a collapse. Start ON the action or the line. Never start on an establishing shot, a calm walk, a greeting or small talk. Starting mid-scene is fine if the first line grips instantly.
2. STANDS ALONE. A stranger can tell within seconds who is the underdog, who is the threat and what is at stake, from what is said and shown INSIDE the clip. Avoid moments that only make sense with names, backstory or earlier events the clip does not show.
3. CLIFFHANGER at the cut. End on the most unresolved instant: the threat before it lands, the reveal before the reaction, the question before the answer, the hand raised before the slap. Never end after the conflict resolves, on an explanation, or on a calm line.
4. ESCALATES. Tension should rise from the hook to the cut.

Continuous clips are preferred. You MAY join up to 3 moments (segments) from this episode, in story order, when it gives a clearly stronger promo (for example a hook from one scene and the cliffhanger from a later one).

Hard rules (checked by code; violations are rejected):
- Choose boundaries ONLY by line IDs. A segment runs from the START time of start_line to the END time of end_line.
- Total duration of all segments must be between 20 and 60 seconds; aim for 30-50.
- hook_line must START within the first 3 seconds of the promo (normally it is the first spoken or action line).
- cliffhanger_line must be inside the promo, at or very near its end.
- hook_quote and cliffhanger_quote must be copied exactly from the cited line (or be "" if that moment is purely visual).
Return JSON only."""

PROPOSER_USER = """SHOW: {show}
EPISODE {number} of {total} (script duration {duration:.1f} s)

STORY SO FAR (for your understanding only; a new viewer will NOT know this):
{story}

THIS EPISODE'S LOGLINE: {logline}

TIMED SCRIPT. Each line is [ID start-end seconds]. SCREEN/ACTION lines describe what is visible.
{script}

Task: find the best promo candidates in THIS episode. Return 1 to {max_candidates} candidates, best first. Fewer is fine when the episode has only one strong moment; never return two candidates that are nearly the same cut.

Return this JSON object:
{{
  "episode_summary": "<= 45 words: what happens in this episode, with character names and roles, for story memory",
  "candidates": [
    {{
      "title": "3-6 word editor label",
      "segments": [{{"start_line": "L0xx", "end_line": "L0yy"}}],
      "hook_line": "L0xx",
      "hook": "what the viewer sees/hears in the first 3 seconds and why it stops the scroll",
      "hook_quote": "exact words from hook_line, or \\"\\" if visual",
      "cliffhanger_line": "L0yy",
      "cliffhanger": "the unresolved question the viewer is left with at the cut",
      "cliffhanger_quote": "exact words from cliffhanger_line, or \\"\\" if visual",
      "why_it_works": "one sentence",
      "setup_needed": "what a brand-new viewer must already know to follow it, or \\"none\\"",
      "spoiler_risk": "low | medium | high (does it give away a major twist of the series?)",
      "self_score": 1-10
    }}
  ]
}}"""

REPAIR_USER = """Some of your candidates broke the hard rules. Code computed these problems:
{problems}

Return the same JSON shape again with corrected versions of ONLY the rejected candidates (same titles). Fix the boundaries by choosing different line IDs; keep the idea if it is good. Remember: 20-60 s total, hook_line starts within the first 3 s, cliffhanger_line inside the promo near its end, quotes copied exactly. Set episode_summary to ""."""

PROPOSER_BATCH_USER = """SHOW: {show} ({total} episodes total)

STORY SO FAR (for your understanding only; a new viewer will NOT know this):
{story}

Below are {count} consecutive episodes. For EACH episode, find the best 1 to {max_candidates} promo candidates (20-60 s each, hook_line starting in the first 3 s, cliffhanger_line inside the cut near its end, exact hook_quote and cliffhanger_quote from the cited line IDs).

{episodes_block}

Return this JSON object with one entry per episode in the same order:
{{
  "episodes": [
    {{
      "episode": 11,
      "episode_summary": "<= 35 words: what happens in this episode",
      "candidates": [
        {{
          "title": "3-6 word editor label",
          "segments": [{{"start_line": "L0xx", "end_line": "L0yy"}}],
          "hook_line": "L0xx",
          "hook": "what the viewer sees/hears in the first 3 seconds and why it stops the scroll",
          "hook_quote": "exact words from hook_line, or \\"\\" if visual",
          "cliffhanger_line": "L0yy",
          "cliffhanger": "the unresolved question the viewer is left with at the cut",
          "cliffhanger_quote": "exact words from cliffhanger_line, or \\"\\" if visual",
          "why_it_works": "one sentence",
          "setup_needed": "what a brand-new viewer must already know to follow it, or \\"none\\"",
          "spoiler_risk": "low | medium | high",
          "self_score": 1-10
        }}
      ]
    }}
  ]
}}"""

CRITIC_SYSTEM = """You are a typical viewer scrolling TikTok at night. You have NEVER seen this show and know nothing about its story or characters.
You will read several short clips. For each clip you get exactly what plays: spoken lines and what is on screen, with times in seconds from the first frame of THAT clip (0.0 = the moment it appears in your feed). [CUT] marks a jump to a later moment.
Judge each clip ON ITS OWN, as if it were the only one you ever saw. Never use information from one clip to understand another.
Be a harsh, honest judge. Most clips are mediocre. Reserve 5 for truly scroll-stopping work.
Scores (1-5):
- hook: would 0-3 s make you stop scrolling? Judge only what happens in 0.0-3.0 s.
- standalone: could you follow who is who and what is at stake without knowing the show?
- cliffhanger: does it cut at an unresolved moment that makes you NEED the next part? 1 if it resolves or ends flat.
- tap_to_watch: overall, would you tap through to watch the show?
Return JSON only."""

CRITIC_USER = """{clips}

Return this JSON object with one entry per clip, in the same order:
{{
  "reviews": [
    {{
      "clip": "A",
      "first_3s": "what you actually notice in 0-3 s",
      "hook": 1-5,
      "understood": "who is who and what is at stake, in your own words",
      "confusions": "what you could not follow, or \\"none\\"",
      "standalone": 1-5,
      "open_question": "the question you are left with, or \\"none - it resolves\\"",
      "cliffhanger": 1-5,
      "tap_to_watch": 1-5,
      "main_problem": "biggest weakness, a few words"
    }}
  ]
}}"""

