"""Typed decision-review output; reference validation is necessary, not fact checking."""
from typing import Literal
from pydantic import Field
from underwriting.intake.schema import Strict

Action = Literal['continue_underwriting_review', 'request_further_information', 'refer_to_senior_underwriter', 'outside_demo_scope']
ACTIONS = {'continue_underwriting_review': 'Continue underwriting review',
           'request_further_information': 'Request further information',
           'refer_to_senior_underwriter': 'Refer to senior underwriter',
           'outside_demo_scope': 'Outside demo scope'}

class Concern(Strict):
    concern_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    finding: str = Field(min_length=1)
    significance: str = Field(min_length=1)
    supporting_references: list[str] = Field(min_length=1)
    suggested_follow_up: str = Field(min_length=1)

class MissingInformation(Strict):
    item_id: str = Field(min_length=1)
    information_or_document: str = Field(min_length=1)
    request_type: Literal['document_not_supplied', 'clarification_needed', 'remedial_evidence_needed']
    reason: str = Field(min_length=1)
    supporting_references: list[str] = Field(min_length=1)

class SuggestedAction(Strict):
    action: Action
    rationale: str = Field(min_length=1)
    supporting_references: list[str] = Field(min_length=1)

class SecondaryAction(Strict):
    action: Action
    rationale: str = Field(min_length=1)

class LegacyDecisionReview(Strict):
    review_summary: str = Field(min_length=1)
    assessment_interpretation: str = Field(min_length=1)
    key_concerns: list[Concern]
    missing_information: list[MissingInformation]
    suggested_action: SuggestedAction
    secondary_actions: list[SecondaryAction]
    limitations: list[str] = Field(min_length=1)


class ScreenedDecisionReview(LegacyDecisionReview):
    screening_outcome: Literal['Proceed to assessment', 'Pause for clarification', 'Outside demo scope']
    key_concerns: list[Concern] = Field(max_length=5)
    missing_information: list[MissingInformation] = Field(max_length=5)


class DecisionReview(ScreenedDecisionReview):
    report_conclusion: str = Field(min_length=1)


def validate_screening_narrative(value, outcome):
    import re
    if value.get('screening_outcome') is not None and value['screening_outcome'] != outcome:
        raise ValueError('Review declared screening status conflicts with its authoritative current screening input.')
    text = ' '.join([value.get('review_summary',''),value.get('assessment_interpretation',''),value.get('suggested_action',{}).get('rationale','')])
    if outcome == 'Proceed to assessment' and re.search(r'\bscreen(?:ing)?\s+(?:(?:is|remains|still)\s+){0,2}(?:paused|on hold)\b',text,re.I):
        raise ValueError('Stored review narrative says screening is paused, but the authoritative current outcome is Proceed. Explicit regeneration is required.')


def review_output_schema(snapshot):
    """Constrain model references to actual bare IDs, not free-form citation prose."""
    identifiers = sorted(snapshot['reference_catalogue'])
    if not identifiers:
        raise ValueError('The decision snapshot has no saved reference IDs.')
    schema = DecisionReview.model_json_schema()
    # Paused/outside screening is reviewable data, not permission to continue.
    # Enforce routing in the request schema, before a paid response is generated.
    outcome = snapshot['submission']['outcome']
    actions = (['outside_demo_scope'] if outcome == 'Outside demo scope' else
               ['request_further_information', 'refer_to_senior_underwriter'] if outcome == 'Pause for clarification' else list(ACTIONS))
    for name in ('SuggestedAction', 'SecondaryAction'):
        schema['$defs'][name]['properties']['action']['enum'] = actions
    schema['properties']['screening_outcome']['enum'] = [outcome]
    for name in ('Concern', 'MissingInformation', 'SuggestedAction'):
        field = schema['$defs'][name]['properties']['supporting_references']
        field['description'] = ('Use exact bare IDs from reference_catalogue only. Each list entry is one ID. '
                                'Do not append descriptions, quotations or snapshot paths; do not combine IDs in one entry.')
        field['items']['enum'] = identifiers
    return schema


def validate_review(value, snapshot):
    result = (DecisionReview if 'report_conclusion' in value else ScreenedDecisionReview if 'screening_outcome' in value else LegacyDecisionReview).model_validate(value).model_dump()
    validate_screening_narrative(result,snapshot['submission']['outcome'])
    if 'screening_outcome' in value:
        if len(result['review_summary'].split()) > 80 or len(result['suggested_action']['rationale'].split()) > 60 or len(result.get('report_conclusion','').split())>60:
            raise ValueError('Decision summary/rationale exceeds its concise 80/60-word limit.')
    references = snapshot['reference_catalogue']
    for rows, identifier in ((result['key_concerns'], 'concern_id'), (result['missing_information'], 'item_id')):
        ids = [row[identifier] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError('Review concern/information identifiers must be unique.')
    for row in [*result['key_concerns'], *result['missing_information'], result['suggested_action']]:
        unknown = sorted(set(row['supporting_references']) - set(references))
        if unknown:
            raise ValueError('Unknown supporting reference IDs: ' + '; '.join(ref[:180] for ref in unknown[:5]) + '. References must be exact IDs from the saved reference catalogue, without descriptions or paths.')
    secondary = [row['action'] for row in result['secondary_actions']]
    if len(secondary) != len(set(secondary)) or result['suggested_action']['action'] in secondary:
        raise ValueError('Secondary actions must be distinct from each other and the primary action.')
    for rows, fields in ((result['key_concerns'], ('topic','finding')), (result['missing_information'], ('information_or_document',))):
        normalized = [tuple(' '.join(row[key].lower().split()) for key in fields) for row in rows]
        if len(normalized) != len(set(normalized)):
            raise ValueError('Combine duplicate concern or information-request rows.')
    outcome = snapshot['submission']['outcome']
    action = result['suggested_action']['action']
    if outcome == 'Outside demo scope' and action != 'outside_demo_scope':
        raise ValueError('Outside-scope screening cannot be routed to continuation within the demo.')
    if outcome in {'Pause for clarification', 'Outside demo scope'} and 'continue_underwriting_review' in [action,*secondary]:
        raise ValueError('Paused screening must be resolved before continuation.')
    return result
