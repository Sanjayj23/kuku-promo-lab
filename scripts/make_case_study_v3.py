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
    def value(key):
        return evidence[key]['value']
    for key in ('author_line', 'links'):
        if not value(key):
            print(f'WARNING: {key} missing')
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
    text('Three saved cuts. One event.',PAD,332,25,display)
    para(value('figure')['decision'],PAD,344,W-2*PAD,9.6,13,MUTED)
    box(PAD,373,W-2*PAD,172,'#EAF0EB',10)
    figure = value('figure')
    x0, x1 = 154, W-PAD-20
    span = x1-x0
    def t(seconds):
        return x0+span*seconds/figure['max_seconds']
    for fraction in (0, .25, .5, .75, 1):
        seconds = fraction*figure['max_seconds']
        line(t(seconds),399,t(seconds),505,'#CCD8D3')
        text(f'{seconds:g}s',t(seconds)-6,394,8,'Text',MUTED)
    for index, row in enumerate(figure['rows']):
        y = 409+index*31
        text(row['label'].replace('_',' '),PAD+12,y+12,8.4,'Bold')
        box(t(row['start']),y,t(row['end'])-t(row['start']),17,
            '#78B6A6' if row['label']=='next_end' else '#C2D0CB',3)
        text(f"{row['start']:g}-{row['end']:g}s",t(row['start'])+6,y+12,8,'Bold')
    opening = next(row['first'] for row in figure['rows'] if row['label']=='previous_start')
    closing = next(row['last'] for row in figure['rows'] if row['label']=='next_end')
    para(f"Opener {opening} | Ending {closing}",PAD+12,492,W-2*PAD-24,8.4,11,MUTED)
    para(figure['caption'],PAD+12,510,W-2*PAD-24,8.8,12,MUTED)
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
    para('Source validity, model judgments and human review are tracked separately.',
         PAD,109,W-2*PAD,10.4,15,MUTED)
    stats = [(str(value('episodes')),'EPISODES ANALYZED'),(str(value('blocks')),'SOURCE BLOCKS'),(str(value('intervals')),'INTERVALS CHECKED')]
    for i,(v,l) in enumerate(stats):
        x=PAD+i*(W-2*PAD)/3
        line(x,145,x+(W-2*PAD)/3-17,145)
        text(v,x,185,32,display)
        label(l,x,204)
    box(PAD,221,W-2*PAD,86,NAVY,9)
    label('MEASURED HISTORICAL INFERENCE',PAD+15,242,MINT)
    para(value('runtime_lines'),PAD+15,253,W-2*PAD-30,9.4,13,PAPER)
    label('ORIGINAL V2',PAD,330)
    label('GATE 3 / ZERO-CALL REGATE',PAD+255,330)
    para(value('before'),PAD,341,240,10.4,15,INK,'Bold')
    para(value('after'),PAD+255,341,240,10.4,15,INK,'Bold')
    para(value('families'),PAD,377,W-2*PAD,9.4,13,MUTED)
    label('WHERE IT FAILS / WHAT THE CHECKS DO',PAD,424)
    rows = [
        ('Short opener','An isolated verdict can still overrate a vague reply. Minimal cues now require review.'),
        ('Answered ending','Explicit answers exclude a cut; later speech after cited evidence adds a review flag.'),
        ('Invented object','The saved model still invents an object. Lexical evidence checks now flag it.'),
        ('Wrong role','Matching voice IDs alone cannot prove attribution. Unsupported roles require review.'),
        ('Score ties','The export now identifies when duration or ID alone chose the boundary.'),
    ]
    for index,(name,copy) in enumerate(rows):
        y=439+index*28
        line(PAD,y,W-PAD,y)
        text(name,PAD,y+16,9.1,'Bold')
        para(copy,PAD+110,y+4,W-2*PAD-110,9,12,MUTED)
    y = para(value('test_line'),PAD,593,W-2*PAD,9.3,12.6,INK)
    y = para(value('blind_line'),PAD,y+7,W-2*PAD,9.3,12.6,MUTED)
    y = para(value('limitations'),PAD,y+7,W-2*PAD,9.3,12.6,MUTED)
    y = para(value('ai_tools_line'),PAD,y+7,W-2*PAD,9.1,12.3,MUTED)
    if value('author_line'):
        y=para(value('author_line'),PAD,y+5,W-2*PAD,9,12,MUTED)
    for link in value('links'):
        y=para(f"{link['label']} - {link['url']}",PAD,y+4,W-2*PAD,8.5,11,MUTED)
    if y > 790:
        raise ValueError(f'Page 2 overflow at {y}')
    footer(2)
    c.save()

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--evidence',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    make(args.out,json.loads(args.evidence.read_text()))
