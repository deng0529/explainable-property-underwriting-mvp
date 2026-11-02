"""Read-only current-result resolution for explanatory diagrams.

Uses the same current evidence/confirmation/ER compatibility checks as Decision,
but permits a partially completed workflow. Never invokes stage services.
"""
from underwriting.assessment.belief_service import current_input
from underwriting.assessment.er_service import current_confirmed, input_belief_hash
from underwriting.assessment.brb_service import current_er_inputs, er_result_hash, rule_configuration_hash
from underwriting.assessment.evidence_service import document_hash
from underwriting.configuration.decision_model import effective_er_model_hash
from underwriting.models.er import ENGINE_VERSION as ER_VERSION
from underwriting.models.brb import ENGINE_VERSION as BRB_VERSION


def empty_overview(model, state=None):
    return {'attributes': {a['id']: {'state': state or 'Not assessed', 'values': None}
                           for a in model['attributes'] if a['enabled']},
            'groups': {key: {'state': state or 'Not calculated', 'result': None}
                       for key in model['er']['groups']},
            'overall': {'state': state or 'Not calculated', 'result': None}}


def load_overview(case, store, model):
    view = empty_overview(model)
    digest = document_hash(case)
    proposal = None
    try:
        _, _, _, reviewed_hash, _ = current_input(case, store)
        proposal = store.latest_beliefs(case, digest, reviewed_hash)
    except ValueError:
        pass  # Absent/unreviewed evidence is a valid incomplete workspace.
    if proposal:
        events = {e['attribute_id']: e for e in store.belief_reviews(proposal['run_id'])}
        proposals = {a['attribute_id']: a for a in proposal['result']['assessments']}
        for identifier, row in view['attributes'].items():
            event = events.get(identifier)
            if (event and event.get('confirmed') and event['run_id'] == proposal['run_id']
                    and event['source_hash'] == digest and event['reviewed_hash'] == proposal['reviewed_hash']):
                row.update(state='Human confirmed', values=event['values'])
            elif identifier in proposals:
                row.update(state='AI proposal — unconfirmed', values={key: proposals[identifier][key] for key in ('low', 'high', 'unknown')})
    elif store.latest_overview_stage(case, 'beliefs'):
        for row in view['attributes'].values():
            row['state'] = 'Stale'

    er = None
    try:
        current = current_confirmed(case, store, model)
        er = store.latest_er_compatible(case, digest, input_belief_hash(*current[:4]),
                                        effective_er_model_hash(model), ER_VERSION)
        if er:
            current_er_inputs(case, er, store, model)
    except ValueError:
        er = None
    if er:
        for identifier, row in view['groups'].items():
            result = er['groups'].get(identifier)
            if result:
                row.update(state='Current saved result', result=result)
        brb = store.latest_brb(case, digest, er['run_id'], er_result_hash(er),
                               rule_configuration_hash(model), BRB_VERSION)
        if brb and brb.get('status') == 'completed':
            view['overall'].update(state='Current saved result', result=brb['result'])
    elif store.latest_overview_stage(case, 'er'):
        for row in view['groups'].values():
            row['state'] = 'Stale'
    if not view['overall']['result'] and store.latest_overview_stage(case, 'brb'):
        view['overall']['state'] = 'Stale'
    # Recheck after reading the chain: concurrent reviews must not produce a
    # diagram joining an old group result to newly confirmed inputs.
    if er:
        try:
            current_er_inputs(case, er, store, model)
        except ValueError:
            for row in view['groups'].values():
                row.update(state='Stale', result=None)
            view['overall'].update(state='Stale', result=None)
    if proposal:
        try:
            _, _, _, after_hash, _ = current_input(case, store)
            latest = store.latest_beliefs(case, digest, after_hash)
            if after_hash != proposal['reviewed_hash'] or not latest or latest['run_id'] != proposal['run_id']:
                raise ValueError('Changed reviewed inputs.')
        except ValueError:
            for row in view['attributes'].values():
                row.update(state='Stale', values=None)
            for row in view['groups'].values():
                row.update(state='Stale', result=None)
            view['overall'].update(state='Stale', result=None)
    return view
