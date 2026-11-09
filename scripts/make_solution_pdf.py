"""Create the submission PDF from measured run/evaluation data, never sample numbers."""
import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import reportlab


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--evaluation',required=True);p.add_argument('--out',required=True);args=p.parse_args()
    run=Path(args.run);evaluation=Path(args.evaluation);target=Path(args.out)
    if target.exists():raise SystemExit('PDF exists; choose a new version')
    target.parent.mkdir(parents=True,exist_ok=True)
    fonts=Path(reportlab.__file__).parent/'fonts'
    pdfmetrics.registerFont(TTFont('KukuBody',str(fonts/'Vera.ttf')))
    pdfmetrics.registerFont(TTFont('KukuBold',str(fonts/'VeraBd.ttf')))
    pdfmetrics.registerFontFamily('KukuBody',normal='KukuBody',bold='KukuBold',italic='KukuBody',boldItalic='KukuBold')
    manifest=json.loads((run/'run_manifest.json').read_text())
    metrics=json.loads((evaluation/'metrics.json').read_text())
    promos=json.loads((run/'promos.json').read_text())['recommendations']
    stats=manifest['statistics']; inf=manifest['inference']
    overlap_count=sum(w['code']=='dialogue_overlap' for w in stats['warnings'])
    overrun_count=sum(w['code']=='scene_overrun' for w in stats['warnings'])
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='TitleK',fontName='KukuBold',fontSize=27,leading=30,textColor=colors.HexColor('#142d39'),spaceAfter=7*mm))
    styles.add(ParagraphStyle(name='SubK',fontName='KukuBody',fontSize=11,leading=15,textColor=colors.HexColor('#42606a'),spaceAfter=5*mm))
    styles.add(ParagraphStyle(name='HeadK',fontName='KukuBold',fontSize=12,leading=16,textColor=colors.HexColor('#197b70'),spaceBefore=4*mm,spaceAfter=2*mm))
    styles.add(ParagraphStyle(name='BodyK',fontName='KukuBody',fontSize=9,leading=12,spaceAfter=2*mm,textColor=colors.HexColor('#263b45')))
    story=[]
    def para(text,style='BodyK'):story.append(Paragraph(text,styles[style]))
    def head(text):para(text,'HeadK')
    para('KUKU PROMO LAB','TitleK')
    para('Problem 1: Find the Promos | Private, evidence-linked editorial assistance','SubK')
    head('The decision that matters is the cut boundary')
    para('A dramatic event can lose its setup if a promo starts too late, or lose its tension if it ends after the answer. This prototype compares nearby continuous cuts and checks each using a fresh, restricted dialogue packet. It proposes editorial choices for review; it does not predict advertising performance.')
    head('How it works')
    para('<b>1. Code reads every episode.</b> Safe ZIP/folder ingestion preserves source hashes, scenes, exact block text, line spans and integer-millisecond script timing. A boundary is safe only when no speech track crosses it.')
    para('<b>2. A story-aware model chooses base events.</b> It reads the complete episode plus current/adjacent source metadata. A bounded menu of up to 48 source-valid windows constrains boundary and evidence IDs. The model supplies event choice, hook, unresolved question and provisional criteria.')
    para('<b>3. Code varies the boundaries.</b> Up to five cuts per shortlisted family: base, previous/next safe dialogue start, previous/next safe dialogue end, within ten seconds. Every move stays 20-60 seconds. Pruned moves remain visible.')
    para('<b>4. The same model reviews each cut independently.</b> Its fresh request sees exact included dialogue, neutral local voice IDs and relative timing. It sees no title, episode number, speaker names from labels, emotion/action/camera/sound, synopsis, analyst rationale, future material or other variants. Positive scores require local citations; hooks require a complete utterance inside the first three seconds.')
    para('<b>5. Code gates, compares and ranks.</b> Hook/cliffhanger >=2; comprehension >=3; coherence/boundary >=2; all six criteria known and cited; no essential missing context. Weighted score: 20% hook, 25% cliffhanger, 25% comprehension, 10% each emotion/coherence/boundary. Near-ties and exact criterion changes are retained. Interval/text similarity suppresses duplicates.')
    head('Scope, model and scaling')
    para('One continuous, dialogue-led cut per recommendation; up to ten eligible results, without padding. No video editing, montage, guessed word alignment or automatic visual certification. '
         f"This run used {escape(manifest['model']['model'])}; runtime: {escape(manifest['model']['runtime_identity'])}. "
         'A provider interface, strict JSON contracts, role-separated caches and genuine-response replay make inference swappable and inspectable.')
    para('Parsing and per-episode analysis scale with pack size; shortlisted review is bounded. Small-model latency and semantic reliability dominate. The current/adjacent metadata policy and 48-window menu can miss long-range or unsampled events. Full visual hooks and composites are future work.')
    story.append(PageBreak())
    para('WHAT ACTUALLY RAN','TitleK')
    para(f"Run {escape(manifest['run_id'])} | status: {escape(manifest['run_status'])} | inference origin: {escape(manifest['execution'])}",'SubK')
    data=[['Coverage','Source parsing','Final list'],[f"{stats['files_parsed']}/{stats['files_discovered']} episodes",f"{stats['scenes']} scenes / {stats['blocks']} blocks",f"{len(promos)} eligible / {metrics['valid_checked']} validated"]]
    table=Table(data,colWidths=[54*mm,66*mm,54*mm])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#197b70')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,-1),'KukuBody'),('FONTNAME',(0,0),(-1,0),'KukuBold'),('FONTSIZE',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8),('BACKGROUND',(0,1),(-1,-1),colors.HexColor('#edf5f4'))]))
    story.append(table);story.append(Spacer(1,3*mm))
    para(f"Analyzed episodes: {metrics['direct_scope_episodes']}; reviewed families: {stats['families_reviewed']}; successful variant reviews: {stats['variants_reviewed']}; families with at least three reviewed cuts: {metrics['three_or_more_reviewed_variant_families']}; failures: {stats['failures']}. Total script extent: {stats['total_extent_ms']:,} ms. Preserved warnings: {overlap_count} overlapping speech pairs and {overrun_count} internal scene overruns.")
    para(f"Measured pipeline elapsed time: {manifest['elapsed_s']/60:.1f} minutes. Actual model attempts: {inf['attempts']}; cache hits: {inf['cache_hits']}; prompt/completion tokens reported by provider: {inf['usage']['prompt_tokens']:,}/{inf['usage']['completion_tokens']:,}. No paid inference API was used; hardware/energy cost was not measured.")
    if promos:
        first=promos[0];seg=first['segments'][0]
        para(f"Actual top output: {escape(seg['source_episode'])}, {seg['start_timecode']} to {seg['end_timecode']}, {first['duration_ms']/1000:g}s. Its model score is {first['ranking']['score']:g}/100, not a human rating. Source evidence and all alternatives are in the private raw outputs.")
    else:para('The eligible list is empty; candidates and rejection reasons are retained. No recommendation was fabricated to fill a quota.')
    head('Comparison and honest quality checking')
    para(f"The seeded random baseline produced {metrics['random_valid_count']} structurally valid intervals. Direct selection used the same genuine analyst calls, model, source constraints and rubric without restricted review/search; it selected {metrics['direct_selected_count']} cuts across {metrics['direct_scope_episodes']} analyzed episodes. Search changed the chosen boundary in {metrics['winner_changed_from_base']} families; {metrics['near_tie_families']} families were near-ties. These are internal diagnostics, not evidence of improved audience preference.")
    para('Independent source revalidation checks every exported interval, provenance, timing, speech safety, criteria, CSV consistency and checksums. Synthetic tests cover archive attacks, exact precision, overlap, invented IDs, out-of-cut evidence, metadata canaries, truncation, missing services, altered input and offline replay. Blind A/B packets and blank worksheets are prepared for two authorized reviewers; no human votes or audience outcomes have been collected.')
    head('Failures, AI tools and remaining work')
    para('Codex helped implement/debug the supplied design; Qwen supplies the judgments. Early invalid timing/citations led to source-ID schemas. The first full GPU run rejected all 47 cuts, over-demanding identities/backstory. A balanced rubric clarified local roles and explicit stakes; genuine clear/ambiguous synthetic controls checked acceptance and rejection before the revised run. Final source inspection still found weak one-word hooks, already answered questions and an invented bell. Original outputs remain intact. Valid citations do not prove meaning: human editorial approval and video review are required.')
    head('Run again and submit responsibly')
    para('Install the pinned dependencies, start the loopback model, run <font name="KukuBody">kuku doctor</font>, then <font name="KukuBody">kuku run --input PACK --out NEW_RUN --config CONFIG</font>. Use <font name="KukuBody">kuku serve</font> for local upload/review/export and <font name="KukuBody">kuku validate</font> to re-read the source. The README provides exact setup and replay steps. Generic code is packaged separately; real scripts, transcripts, logs and source-rich outputs must remain within the intended assignment review channel. Publishing and the optional own-voice video are the submitter actions.')
    def footer(canvas,doc):
        canvas.setStrokeColor(colors.HexColor('#cfdddf'));canvas.line(18*mm,17*mm,192*mm,17*mm)
        canvas.setFont('KukuBody',8);canvas.setFillColor(colors.HexColor('#657b83'));canvas.drawString(18*mm,12*mm,'Kuku Promo Lab | Assignment review copy | Script-only, human review pending');canvas.drawRightString(192*mm,12*mm,str(doc.page))
    SimpleDocTemplate(str(target),pagesize=(210*mm,297*mm),leftMargin=18*mm,rightMargin=18*mm,topMargin=17*mm,bottomMargin=23*mm,title='Kuku Promo Lab - Solution',author='Assignment submitter').build(story,onFirstPage=footer,onLaterPages=footer)
    print(target.resolve())


if __name__=='__main__':main()
