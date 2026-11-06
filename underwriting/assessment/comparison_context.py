"""One frozen full-document/reviewed-evidence input for both comparison methods."""
import copy
import hashlib
import json
from underwriting.configuration.settings import get_config
from underwriting.assessment.evidence_schema import attribute_definitions,TEXT_EXTRACTION_VERSION
from underwriting.assessment.belief_service import current_input,content_hash,reviewed_snapshot_hash
from underwriting.intake.service import source_context


def text_hash(pages):
    return hashlib.sha256(json.dumps(pages,ensure_ascii=False).encode()).hexdigest()


def checked_pages(source,digest):
    pages=source.get('pages')
    if (source.get('source_hash')!=digest or source.get('extraction_version')!=TEXT_EXTRACTION_VERSION
        or not isinstance(pages,list) or not pages or any(not isinstance(p,str) for p in pages)
        or source.get('text_hash')!=text_hash(pages)):
        raise ValueError('Saved full-document text provenance/hash is invalid.')
    return copy.deepcopy(pages)


def snapshot_hash(snapshot):
    return content_hash({key:snapshot[key] for key in ('input_version','case','source','reviewed_material','attribute_configuration','assessment_guidance')})


def freeze_context(settings,case,store,context=None):
    digest,evidence,reviewed,review_hash,versions=copy.deepcopy(context if context is not None else current_input(case,store))
    if (evidence.get('case_id'),evidence.get('source_version'),evidence.get('source_hash'))!=(case['case_id'],case['submission_version'],digest):
        raise ValueError('Document and reviewed evidence must match company, submission and document versions.')
    definitions=copy.deepcopy(attribute_definitions())
    expected={a['id'] for a in definitions if a.get('enabled',True)}
    if len(reviewed['attributes'])!=len(expected) or {a['attribute_id'] for a in reviewed['attributes']}!=expected:
        raise ValueError('Review evidence for every enabled attribute before comparison.')
    source=store.source(digest,TEXT_EXTRACTION_VERSION)
    if source:
        pages=checked_pages(source,digest)
    else:
        # Legacy source caches can be absent. Reuse an already saved comparison
        # snapshot before considering a single local extraction.
        saved=store.latest_comparison_direct(case,digest,review_hash) or store.latest_scores(case,digest,review_hash)
        previous=(saved or {}).get('input_snapshot',{}).get('source')
        if previous:pages=checked_pages(previous,digest)
        else:
            actual,pages=source_context(case)
            if actual!=digest:raise ValueError('Document changed before comparison. Reload evidence first.')
    source={'source_hash':digest,'extraction_version':TEXT_EXTRACTION_VERSION,'text_hash':text_hash(pages),'pages':pages,
            'page_labelled_text':'\n\n'.join(f'[PAGE {i}]\n{page}' for i,page in enumerate(pages,1))}
    material={k:v for k,v in reviewed.items() if k!='evidence_run_id'}
    snapshot={'input_version':get_config()['belief_comparison']['input_version'],
        'case':{'case_id':case['case_id'],'source_version':case['submission_version'],'source_hash':digest},
        'source':source,'reviewed_evidence':reviewed,'reviewed_material':material,
        'attribute_configuration':definitions,
        'assessment_guidance':{'belief_granularity':get_config()['belief_assessment']['increment'],
            'belief_granularity_scope':'Direct allocations only; score and Unknown judgements are continuous.',
            'distribution_tolerance':get_config()['belief_comparison']['distribution_tolerance'],
            'score_scale':copy.deepcopy(get_config()['belief_comparison']['score_scale'])},
        'upstream':{'evidence_run_id':evidence['run_id'],'reviewed_hash':review_hash,'evidence_review_versions':versions,
                    'reviewed_snapshot_hash':reviewed_snapshot_hash(reviewed)}}
    snapshot['input_hash']=snapshot_hash(snapshot)
    return snapshot


def current_matches(case,store,snapshot):
    digest,evidence,reviewed,_,_=current_input(case,store)
    return ((evidence.get('case_id'),evidence.get('source_version'),evidence.get('source_hash'),digest)==(case['case_id'],case['submission_version'],snapshot['source']['source_hash'],snapshot['source']['source_hash'])
            and reviewed_snapshot_hash(reviewed)==snapshot['upstream']['reviewed_snapshot_hash']
            and attribute_definitions()==snapshot['attribute_configuration'])


def prompt_context(snapshot):
    value=copy.deepcopy(snapshot)
    value.pop('reviewed_material')
    value['source'].pop('pages')  # Include the full labelled text once, not twice.
    return json.dumps(value,ensure_ascii=False,indent=2)
