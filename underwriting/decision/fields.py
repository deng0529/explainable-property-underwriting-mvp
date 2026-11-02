"""Validated saved-field access shared by Decision and report rendering."""
from underwriting.configuration.settings import get_config


def submission_field(snapshot, identifier):
    submission = snapshot.get('submission')
    data = submission.get('extraction_and_screening') if isinstance(submission,dict) else None
    rows = data.get('basic_information') if isinstance(data,dict) else None
    if not isinstance(rows,list):
        raise ValueError('Saved Submission basic information was not loaded. Reload Decision; this is not a missing document field.')
    aliases=get_config()['intake']['field_aliases']
    names={identifier,aliases.get(identifier,identifier)}
    matches=[row for row in rows if isinstance(row,dict) and row.get('field_id') in names]
    if len(matches)!=1:
        raise ValueError('Saved Submission field mapping is missing or ambiguous for '+identifier.replace('_',' ')+'. Reload the correct saved submission; no Not provided value has been inferred.')
    field=matches[0]
    if field.get('status') not in {'found','not_provided','conflicting','not_applicable'}:
        raise ValueError('Saved Submission field has an invalid extraction status: '+identifier.replace('_',' '))
    if field['status']=='found':
        value=field.get('value')
        if value is None or not isinstance(value,(str,int,float,list)):
            raise ValueError('Saved found field has an invalid value: '+identifier.replace('_',' '))
        return '; '.join(str(item) for item in value) if isinstance(value,list) else str(value)
    return {'not_provided':'Not provided','conflicting':'Conflicting information - review required','not_applicable':'Not applicable (explicitly reported)'}[field['status']]
