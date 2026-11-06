"""Deterministic A4 report from saved records, using the existing PyMuPDF dependency."""
import json
from pathlib import Path
import pymupdf as fitz
from underwriting.decision.schema import ACTIONS

TEMPLATE_VERSION = "concise-decision-v4"


class Report:
    def __init__(self):
        self.document = fitz.open()
        candidates = [Path('C:/Windows/Fonts/arial.ttf'), Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
        self.font_path = next((path for path in candidates if path.is_file()), None)
        self.font = fitz.Font(fontfile=str(self.font_path)) if self.font_path else fitz.Font('helv')
        self.page = None
        self.y = 0
        self.new_page()

    def new_page(self):
        self.page = self.document.new_page(width=595.276, height=841.89)
        if self.font_path:
            self.page.insert_font(fontname='review', fontfile=str(self.font_path))
        self.font_name = 'review' if self.font_path else 'helv'
        self.page.insert_text((38, 26), 'PROPERTY UNDERWRITING | Synthetic research prototype', fontsize=8, fontname=self.font_name, color=(.32,.36,.42))
        self.page.draw_line((38,34),(557,34),color=(.8,.82,.85),width=.5)
        self.y = 53

    def lines(self, value, width, size=10):
        text = '—' if value is None else str(value)
        lines = []
        for paragraph in text.split('\n'):
            current = ''
            for word in paragraph.split():
                candidate = (current + ' ' + word).strip()
                if self.font.text_length(candidate, fontsize=size) <= width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = ''
                for char in word:
                    if self.font.text_length(current + char, fontsize=size) > width and current:
                        lines.append(current)
                        current = ''
                    current += char
            lines.append(current)
        return lines or ['']

    def heading(self, text):
        if self.y > 742:
            self.new_page()
        self.y += 8
        for line in self.lines(text, 519, 12):
            self.page.insert_text((38,self.y+12),line,fontsize=12,fontname=self.font_name,color=(.12,.2,.3))
            self.y += 16
        self.y += 5

    def paragraph(self, value, size=10):
        for line in self.lines(value, 519, size):
            if self.y + size*1.45 > 787:
                self.new_page()
            self.page.insert_text((38,self.y+size),line,fontsize=size,fontname=self.font_name,color=(.12,.14,.18))
            self.y += size*1.45
        self.y += 5

    def table(self, headers, rows, ratios=None):
        if not rows:
            self.paragraph('No items recorded.')
            return
        widths = [519 * ratio / sum(ratios or [1]*len(headers)) for ratio in (ratios or [1]*len(headers))]
        size, step, padding = 10, 14, 4
        def draw_cells(cells, background):
            height = max(len(cell) for cell in cells) * step + 2*padding
            self.page.draw_rect(fitz.Rect(38,self.y,557,self.y+height),fill=background,color=None)
            x = 38
            for cell, width in zip(cells, widths):
                for index, line in enumerate(cell):
                    self.page.insert_text((x+padding,self.y+padding+size+index*step),line,fontsize=size,fontname=self.font_name,color=(.12,.14,.18))
                x += width
            self.page.draw_line((38,self.y+height),(557,self.y+height),color=(.84,.86,.88),width=.4)
            self.y += height
        header_lines = [self.lines(value,width-2*padding,size) for value,width in zip(headers,widths)]
        header_height = max(map(len,header_lines))*step+2*padding
        if self.y + header_height + 35 > 787:
            self.new_page()
        draw_cells(header_lines,(.91,.93,.96))
        for index, row in enumerate(rows):
            cells = [self.lines(value,width-2*padding,size) for value,width in zip(row,widths)]
            if max(map(len,cells))*step+2*padding <= 160 and self.y+max(map(len,cells))*step+2*padding > 787:
                self.new_page()
                draw_cells(header_lines,(.91,.93,.96))
            while any(cells):
                capacity = int((787-self.y-2*padding)/step)
                if capacity < 1:
                    self.new_page()
                    draw_cells(header_lines,(.91,.93,.96))
                    capacity = int((787-self.y-2*padding)/step)
                chunks = [cell[:capacity] or [''] for cell in cells]
                draw_cells(chunks,(.98,.98,.985) if index%2 else (1,1,1))
                cells = [cell[capacity:] for cell in cells]
        self.y += 8

    def finish(self):
        for index, page in enumerate(self.document):
            page.insert_text((38,816),f'Research review - not a quote or policy | Page {index+1} of {len(self.document)}',fontsize=8,fontname=self.font_name,color=(.38,.4,.44))
        self.document.set_metadata({'title':'Underwriting research review','author':'Property underwriting prototype','producer':'Local deterministic report template v1'})
        result = self.document.tobytes(garbage=4, deflate=True, no_new_id=True)
        self.document.close()
        return result


def render_report(run, decision=None, *, historical=False, current_screening=None, pending=False):
    """Concise saved-record review: readable A4, one page where possible, max two."""
    from underwriting.intake.screening import resolve_screening
    from underwriting.ui.er_presentation import describe_er_result
    from underwriting.decision.schema import validate_screening_narrative
    if not pending and run['status'] != 'succeeded':
        raise ValueError('Only a successful saved review can be reported.')
    if decision and decision['review_run_id'] != run['review_run_id']:
        raise ValueError('Human decision belongs to another saved review.')
    snapshot, review = run['input_snapshot'], run['review']
    submission = snapshot['submission']
    screening = resolve_screening(dict(submission['metadata'],result=submission['extraction_and_screening'],
                                     screening_override=submission.get('screening_resolution',{}).get('override')))
    if not historical and submission['outcome'] != screening['outcome']:
        raise ValueError('Historical review has inconsistent screening. Load current results before exporting.')
    if not historical and not pending:
        validate_screening_narrative(review,screening['outcome'])
    shortened = False
    def short(value, words=30, characters=240):
        nonlocal shortened
        text = ' '.join(str(value or '').split())
        parts = text.split()
        if len(parts)>words or len(text)>characters:
            shortened = True
            fragment=' '.join(parts[:words])
            if len(fragment)>characters:
                fragment=fragment[:characters].rsplit(' ',1)[0] or fragment[:characters]
            return fragment.rstrip()+'…'
        return text or '—'
    def unique(rows, field):
        seen, result = set(), []
        for row in rows:
            text=' '.join(row[field].casefold().split())
            if text not in seen:
                seen.add(text)
                result.append(row)
        return result
    from underwriting.decision.fields import submission_field
    def field_value(identifier):
        return submission_field(snapshot,identifier)
    pdf=Report()
    pdf.heading(snapshot['company'].get('company_name') or field_value('company_name') or 'Company review')
    if historical:
        pdf.paragraph('HISTORICAL REVIEW - NOT CURRENT',11)
        pdf.paragraph('The AI review, assessment results and any human action below belong to the earlier saved snapshot. They are not updated recommendations.',10)
        if current_screening:
            pdf.paragraph('Current screening: '+current_screening['outcome'],10)
    pdf.paragraph('Case '+run['company_id']+' | '+(decision['created_at'] if decision else run['completed_at'])[:10],10)
    pdf.paragraph('Current AI review pending - Human decision pending' if pending else 'Saved human review' if decision else 'Draft review - Human decision pending',10)
    business=field_value('business_description')
    premises=field_value('site_address')
    pdf.paragraph('Business: '+short(business,20,145)+' | Premises: '+short(premises,15,120),10)
    pdf.paragraph(('Screening recorded in historical snapshot: '+submission['outcome']) if historical else 'Screening: '+screening['outcome'],10)
    if screening.get('override'):
        pdf.paragraph('Explicit human override ('+str(screening['override'].get('created_at','date not recorded'))+'): '+short(screening['override']['reason'],25),10)
    if screening['outcome'] != 'Proceed to assessment':
        for reason in screening['reasons'][:3]:
            pdf.paragraph(reason['check_id']+': '+short(reason['reason'],22),10)
        if len(screening['reasons'])>3:
            pdf.paragraph('Further check-specific reasons remain in the app.',10)
    if not pending:
        pdf.paragraph(short(review['review_summary'],80,500),10)
    pdf.heading('Assessment results')
    if snapshot['beliefs'].get('input_method')=='direct_ai':pdf.paragraph('Input source: Direct AI beliefs, aggregated through Fire/Security ER and overall BRB. Not human-confirmed attribute beliefs.',10)
    rows=[]
    for key, result in snapshot['er']['groups'].items():
        description=describe_er_result(result,snapshot['er']['decision_model_snapshot'])
        rows.append([key.title(),f"{result['assigned_beliefs']['low']:.2f}",f"{result['assigned_beliefs']['high']:.2f}",f"{result['unassigned_belief']:.2f}",description['label']])
    result=snapshot['brb']['result']
    beliefs=result['state_beliefs']
    maximum=max(beliefs.values())
    tolerance=snapshot['brb']['decision_model_snapshot']['tolerances'].get('assessment_tie',1e-9)
    winners=[key for key,value in beliefs.items() if maximum-value<=tolerance]
    description='Mixed assessment' if len(winners)>1 else {'low':'Low concern','high':'High concern','unknown':'Insufficient information'}[winners[0]]
    rows.append(['Overall',*[f"{beliefs[grade]:.2f}" for grade in ('low','high','unknown')],description])
    pdf.table(['Assessment','Low','High','Unknown','Descriptive result'],rows,[20,13,13,15,39])
    residual=result['residual_unassigned']
    if residual>snapshot['brb']['decision_model_snapshot']['tolerances']['distribution_sum']:
        pdf.paragraph('Overall Unknown is an explicit state. Separate residual unassigned belief: '+str(residual),10)
    pdf.heading('Key concerns')
    concerns=unique(review['key_concerns'],'finding')
    missing=unique(review['missing_information'],'information_or_document')
    requests={' '.join(row['information_or_document'].casefold().split()) for row in missing}
    def tokens(text):
        return {word.strip('.,:;?!').casefold() for word in text.split()} - {'the','a','an','to','of','with','for','and','in','by'}
    concern_rows=[]
    for row in concerns[:5]:
        follow_up=row['suggested_follow_up']
        if ' '.join(follow_up.casefold().split()) in requests or any(len(tokens(follow_up)&tokens(request))>=3 and len(tokens(follow_up)&tokens(request))/max(1,min(len(tokens(follow_up)),len(tokens(request))))>=.75 for request in requests):
            follow_up='See essential missing information below.'
        concern_rows.append([short(row['finding'],14,125),short(row['significance'],14,125),short(follow_up,14,125)])
    if pending:
        pdf.paragraph('Concerns await a current AI review; historical findings are excluded.',10)
    else:
        pdf.table(['Concern','Why it matters','Required follow-up'],concern_rows,[34,33,33])
    if len(concerns)>5:
        pdf.paragraph(f'{len(concerns)-5} additional concern items remain in the app.',10)
    pdf.heading('Essential missing information')
    if missing:
        for row in missing[:5]:
            pdf.paragraph('• '+short(row['information_or_document'],18,140),10)
        if len(missing)>5:
            pdf.paragraph(f'{len(missing)-5} additional information requests remain in the app.',10)
    else:
        pdf.paragraph('Awaiting the current AI review; refer to saved Submission information for outstanding clarifications.' if pending else 'No additional essential requests recorded in this review.',10)
    pdf.heading('Historical conclusion and next action' if historical else 'Conclusion and next action')
    if pending:
        pdf.paragraph('No current AI action recommendation is recorded.',10)
    else:
        pdf.paragraph(short(review.get('report_conclusion') or review['suggested_action']['rationale'],60,360),10)
        pdf.paragraph('AI suggestion: '+ACTIONS[review['suggested_action']['action']],10)
    pdf.paragraph('Saved human action: '+ACTIONS[decision['selected_action']] if decision else 'Human decision pending',10)
    if shortened:
        pdf.paragraph('Supported content is excerpted for this concise report; full details remain in the app.',9)
    if len(pdf.document)>2:
        pdf.document.close()
        raise ValueError('Concise review exceeds two readable pages. Group the structured concern/request content before export; no GPT request was made.')
    return pdf.finish()


def render_pending_report(snapshot, report_date):
    """A pending-status report from current saved inputs, not a fabricated AI run."""
    display = {'status':'report_pending','input_snapshot':snapshot,
               'company_id':snapshot['company']['case_id'], 'completed_at':report_date,
               'review':{'key_concerns':[], 'missing_information':[]}, 'review_run_id':None}
    return render_report(display,pending=True)
