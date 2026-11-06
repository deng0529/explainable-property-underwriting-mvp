"""Synthetic comparison contracts, never provider calls."""
def response(reviewed,method,score=2.0,unknown=.3):
    rows=[]
    for attribute in reviewed['attributes']:
        ids=[attribute['evidence_items'][0]['evidence_id']]
        item={'attribute_id':attribute['attribute_id'],
              'low_support':{'evidence_ids':ids,'explanation':'Reviewed favourable support.'},
              'high_support':{'evidence_ids':ids,'explanation':'Reviewed adverse support.'},
              'unresolved_information':[{'issue':'Mock material unresolved scope','materiality':'material','potential_assessment_impact':'Could change the assessment.'}],
              'evidence_review_flags':[]}
        if method=='direct':item.update(low=.2,high=.6,unknown=.2,allocation_explanation='Joint allocation across the reviewed mixed evidence and unresolved scope.')
        else:item.update(assessable=True,concern_score=score,unknown=unknown,score_explanation='Balance of assessable concern.',unknown_explanation='Material scope remains unresolved.')
        rows.append(item)
    return {'assessments':rows}
