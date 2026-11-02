"""Conservative application envelopes, not claims about provider context capacity."""
from underwriting.configuration.settings import get_config
from underwriting.configuration.access import require_owner, require_test_services


class InputSizeError(ValueError):
    pass


def limits_for(model):
    limits = get_config()['reliability']['input_limits']
    return limits['models'].get(model,limits['default'])


def check_document(pages, model):
    limits = limits_for(model)
    count, characters = len(pages), sum(len(page) for page in pages)
    if count > limits['max_pages'] or characters > limits['max_pdf_characters']:
        raise InputSizeError('The complete PDF exceeds the supported application input limit ('+str(count)+' pages / '+str(characters)+' characters). Supply a smaller complete source or have the owner review the configured model envelope. No evidence was truncated and no GPT request was made.')
    return {'pages':count,'pdf_characters':characters}


def check_request(system, user, model):
    size = len(system.encode('utf-8'))+len(user.encode('utf-8'))
    if size > limits_for(model)['max_request_utf8_bytes']:
        raise InputSizeError('The complete request exceeds the configured UTF-8 input limit. Review the source size or configured model envelope; no content was truncated and no GPT request was made.')
    return {'request_utf8_bytes':size,'model':model}


def generation_allowed(settings, injected=False):
    require_owner(settings)
    # Test fixtures can inject recorded responses. The actual provider boundary
    # always calls this with injected=False and cannot escape offline mode.
    if not injected:
        require_test_services(settings,generation=True)
