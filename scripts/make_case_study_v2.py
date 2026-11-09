"""Generate the public-safe Kuku Promo Lab case study from aggregate evidence."""
import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph

W, H = A4
PAD = 43
INK = '#102630'
PAPER = '#F8F6EF'
NAVY = '#102832'
MUTED = '#4E6569'
TEAL = '#145F59'
MINT = '#BCEBDD'
CORAL = '#A4483A'
HAIR = '#D5DEDA'

def make(target, evidence):
    import reportlab
    fonts = Path(reportlab.__file__).parent/'fonts'
    for name, filename in [('Text','Vera.ttf'), ('Bold','VeraBd.ttf')]:
        pdfmetrics.registerFont(TTFont(name, str(fonts/filename)))
    display = 'Times-Roman'
    for path in (Path('/System/Library/Fonts/Supplemental/Georgia.ttf'),
                 Path('/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf')):
        if path.is_file():
            pdfmetrics.registerFont(TTFont('Display', str(path)))
            display = 'Display'
            break
    c = Canvas(str(target), pagesize=A4, invariant=1, pageCompression=1)
    c.setTitle('Kuku Promo Lab | The cut is the product')
    c.setSubject('Problem 1: Find the Promos')
    c.setAuthor('Kuku Promo Lab submission')

    def box(x, y, w, h, color, radius=0):
        c.setFillColor(HexColor(color))
        if radius:
            c.roundRect(x, H-y-h, w, h, radius, fill=1, stroke=0)
        else:
            c.rect(x, H-y-h, w, h, fill=1, stroke=0)

    def line(x, y, x2, y2, color=HAIR, weight=0.65):
        c.setStrokeColor(HexColor(color))
        c.setLineWidth(weight)
        c.line(x,H-y,x2,H-y2)

    def text(value, x, y, size=10, font='Text', color=INK):
        c.setFont(font,size)
        c.setFillColor(HexColor(color))
        c.drawString(x,H-y,value)

    def para(value, x, y, width, size=10, leading=14, color=INK, font='Text'):
        style = ParagraphStyle('body', fontName=font, fontSize=size, leading=leading,
                               textColor=HexColor(color), alignment=TA_LEFT)
        p = Paragraph(escape(value),style)
        _, height = p.wrap(width,H)
        p.drawOn(c,x,H-y-height)
        return y+height

    def label(value,x,y,color=TEAL):
        c.saveState()
        t = c.beginText(x,H-y)
        t.setFont('Bold',8)
        t.setCharSpace(1.05)
        t.setFillColor(HexColor(color))
        t.textOut(value)
        c.drawText(t)
        c.restoreState()

    def footer(n):
        line(PAD,800,W-PAD,800)
        label('KUKU PROMO LAB  /  SCRIPT-LEVEL REVIEW',PAD,817,MUTED)
        text(f'0{n} / 02',W-PAD-42,817,8,'Bold',TEAL)

    box(0,0,W,H,PAPER)
    box(0,0,W,271,NAVY)
    label('KUKU PROMO LAB',PAD,43,MINT)
    label('FIND THE PROMOS / PROBLEM 01',W-PAD-190,43,MINT)
    line(PAD,56,W-PAD,56,'#456069')
    text('The cut is',PAD,121,56,display,PAPER)
    text('the product.',PAD,179,56,display,PAPER)
    para('Find 20-60 second promos that establish a stake quickly, make sense alone and stop at an unresolved beat.',
         PAD,203,444,12,17,PAPER)
    label('01  /  COUNTERFACTUAL CUT SEARCH',PAD,302)
    text('Same scene. Two decisions.',PAD,332,25,display)
    para('A synthetic example shows what a few seconds can change.',PAD,346,W-2*PAD,10,13,MUTED)
    box(PAD,373,W-2*PAD,172,'#EAF0EB',10)
    x0,x1 = 144,W-PAD-26
    span = x1-x0
    def t(seconds):
        return x0+span*seconds/32
    for seconds in (0,8,16,24,32):
        line(t(seconds),401,t(seconds),510,'#CCD8D3')
        text(f'{seconds}s',t(seconds)-6,395,8,'Text',MUTED)
    text('A  32s',PAD+16,425,10,'Bold')
    box(t(0),410,span,19,'#C2D0CB',4)
    box(t(0),410,t(3)-t(0),19,'#E9BAAF',4)
    box(t(30),410,t(32)-t(30),19,'#D68F80',3)
    text('Answer stays in the cut',t(8),424,9,'Bold',INK)
    text('B  26s',PAD+16,477,10,'Bold')
    box(t(3),462,t(29)-t(3),19,'#78B6A6',4)
    box(t(3),462,t(6)-t(3),19,'#16665D',4)
    text('Ends at the choice',t(12),476,9,'Bold',INK)
    para('A opens before the specific threat. B brings it into the first 3 seconds and ends before the answer.',
         PAD+16,508,W-2*PAD-32,9.1,12,MUTED)
    label('02  /  FROM SCRIPT TO REVIEW QUEUE',PAD,574)
    cards = [
        ('01','Parse exact timecodes','Code preserves source IDs and tests each boundary for speech overlap.'),
        ('02','Propose and shift','The analyst reads the episode. Code compares up to five nearby safe cuts per event.'),
        ('03','Read each cut in isolation','The opener check sees only complete lines within 3s. An ending check challenges the proposed question.'),
        ('04','Rank and hand off','Code verifies quotes and timestamps. It labels eligible cuts, flags uncertain ones and exports alternatives for review.'),
    ]
    cw = (W-2*PAD-23)/2
    for i,(num,title,copy) in enumerate(cards):
        x = PAD+(i%2)*(cw+23)
        y = 592+(i//2)*86
        line(x,y,x+cw,y)
        text(num,x,y+20,9,'Bold',TEAL)
        text(title,x+24,y+20,11,'Bold')
        para(copy,x+24,y+28,cw-24,9.4,13,MUTED)
    footer(1)
    c.showPage()

    box(0,0,W,H,PAPER)
    label('KUKU PROMO LAB  /  EVALUATION',PAD,43)
    text('Evidence at the boundary.',PAD,91,37,display)
    para('One real 50-episode pack. Source-level validation and an editorial failure audit.',
         PAD,109,W-2*PAD,10.4,15,MUTED)
    stats = [('50 / 50','EPISODES ANALYZED'),('3,727','SOURCE BLOCKS'),('157','INTERVALS CHECKED')]
    for i,(v,l) in enumerate(stats):
        x=PAD+i*(W-2*PAD)/3
        line(x,145,x+(W-2*PAD)/3-17,145)
        text(v,x,185,32,display)
        label(l,x,204)
    box(PAD,221,W-2*PAD,102,NAVY,9)
    label('MEASURED INFERENCE',PAD+15,242,MINT)
    para('V1 cold 47.7 min on T4 under the earlier rubric. V1 warm 18.1 min with 50 cached analyses and 47 fresh reviews.',
         PAD+15,252,W-2*PAD-30,9.8,13.4,PAPER)
    para(evidence['v2_line'],PAD+15,286,W-2*PAD-30,9.3,12.4,PAPER)
    label('03  /  WHERE V1 PICKED BADLY',PAD,352)
    rows = [
        ('One-word opener','Later stakes inflated a vague reply. V2 reads only the opening slice.'),
        ('Answered question','A decision had already been made. V2 checks the whole cut for answers.'),
        ('Invented object','The invented object still passes the V2 semantic check. Quote validity alone is insufficient.'),
        ('Wrong role','V2 flags one role error, but another persists. A selected question still mismatches its ending.'),
        ('Score ties','Ten cuts occupied three score patterns. V2 exposes ties. Human posting order remains blank.'),
    ]
    for i,(name,copy) in enumerate(rows):
        y=372+i*36
        line(PAD,y,W-PAD,y)
        text(name,PAD,y+16,9.1,'Bold')
        para(copy,PAD+118,y+5,W-2*PAD-118,9,12,MUTED)
    label('04  /  WHAT STILL NEEDS JUDGMENT',PAD,580)
    y = para('No demonstrated advantage over the direct baseline. Seven differing blind pairs and one identical control are prepared. Human ratings and posting decisions are pending.',
         PAD,592,W-2*PAD,9.6,13,INK)
    y = para(evidence['validation_line'],PAD,y+8,W-2*PAD,9.4,13,MUTED)
    y = para('Scale by analyzing episodes independently, capping candidate families and reusing exact saved calls. Bounded search can miss strong cuts. Local servers are configurable. The evidence is screenplay timing, and visual hooks require footage review.',
         PAD,y+8,W-2*PAD,9.4,13,MUTED)
    y = para('AI tools used: Codex for design, code and QA. Historical Qwen 8B reviews plus fresh Qwen 4B checks through llama.cpp. Public code uses synthetic scripts. Authorized reviewers receive confidential results separately.',
         PAD,y+8,W-2*PAD,9.4,13,MUTED)
    if y > 788:
        raise ValueError(f'Page 2 content overflow at y={y}')
    footer(2)
    c.save()

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    make(args.out,json.loads(args.evidence.read_text()))
