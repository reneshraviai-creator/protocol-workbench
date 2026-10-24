"""Paginated PDF export of saved, immutable report evidence."""
import io
from html import escape
from engineering_reports import flatten

def render(doc):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
    fields=list(flatten(doc))
    if len(fields)>10000:raise ValueError('PDF exceeds 10,000 evidence fields; narrow the report or use JSON/CSV')
    styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='Evidence',fontName='Helvetica',fontSize=8.5,leading=12,spaceAfter=5,wordWrap='LTR',splitLongWords=True));styles['Title'].fontSize=22;styles['Title'].leading=27;styles['Title'].textColor=colors.HexColor('#173b4b')
    def text(v):return escape(str(v).encode('ascii','backslashreplace').decode()).replace('\n','<br/>')
    def p(v,style='Evidence'):return Paragraph(text(v),styles[style])
    out=io.BytesIO();pdf=SimpleDocTemplate(out,pagesize=A4,rightMargin=44,leftMargin=44,topMargin=55,bottomMargin=48,title=str(doc['title']),author='Protocol Workbench')
    story=[p(doc['title'],'Title'),p(doc.get('description','')),Spacer(1,12)]
    table=Table([[p('RESULT'),p(doc['summary']['status'])],[p('Report ID'),p(doc['id'])],[p('Evidence fingerprint'),p(doc['evidence_sha256'])],[p('Recorded source rows'),p(doc['summary']['records'])]],colWidths=[130,A4[0]-218])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e7f3ef')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOX',(0,0),(-1,-1),.5,colors.HexColor('#c6d4da')),('INNERGRID',(0,0),(-1,-1),.3,colors.HexColor('#dce4e8')),('LEFTPADDING',(0,0),(-1,-1),9),('RIGHTPADDING',(0,0),(-1,-1),9)]));story += [table,Spacer(1,14),p(doc['scope']),p('Evidence is reproduced below using JSON-pointer field paths. Non-ASCII characters are escaped as Unicode code points for portable rendering; JSON export preserves Unicode.'),Spacer(1,10),p('Recorded evidence','Heading2')]
    for path,value in fields:
        if isinstance(value,(dict,list)):
            import json
            value=json.dumps(value,ensure_ascii=False)
        story.append(p(path+' = '+str(value)))
    def footer(canvas,document):
        canvas.saveState();canvas.setFillColor(colors.HexColor('#536976'));canvas.setFont('Helvetica',8);canvas.drawString(44,A4[1]-28,'PROTOCOL WORKBENCH  |  ENGINEERING EVIDENCE');canvas.drawString(44,25,doc['id'][:16]+' - application observations, not production qualification');canvas.drawRightString(A4[0]-44,25,'Page '+str(document.page));canvas.restoreState()
    pdf.build(story,onFirstPage=footer,onLaterPages=footer);return out.getvalue()
