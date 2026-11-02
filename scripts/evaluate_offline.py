"""Evaluate recorded synthetic contracts only. Never contacts any provider/store."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from underwriting.intake.schema import validate_output,derive_outcome


def evaluate(path=None):
    data=json.loads(Path(path or ROOT/'evals/fixtures/intake_contracts.json').read_text(encoding='utf-8'))
    rows=[]
    for case in data['cases']:
        try:
            response,issues=validate_output(case['response'],case['pages'],case['case_id'])
            outcome=derive_outcome(response['screening_checks'],issues)
            expected=case['expected_software_contract']
            passed=outcome==expected['outcome'] and len(issues)>=expected['minimum_validation_issues']
            rows.append({'case_id':case['case_id'],'software_contract_passed':passed,'outcome':outcome,'validation_issue_count':len(issues)})
        except (ValueError,TypeError,KeyError):
            rows.append({'case_id':case['case_id'],'software_contract_passed':False,'error':'Recorded fixture failed its software contract.'})
    reviewed=sum(case['expert_judgement']['status']=='reviewed' for case in data['cases'])
    return {'fixture_version':data['fixture_version'],'software_contracts':rows,
            'expert_judgement':{'reviewed_cases':reviewed,'accuracy':None,
             'notice':'Synthetic contract checks do not establish GPT quality, underwriting accuracy or expert approval.'}}


if __name__=='__main__':
    result=evaluate()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(0 if all(row['software_contract_passed'] for row in result['software_contracts']) else 1)
