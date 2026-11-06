"""Read-only System overview; database reads happen only in its open view."""
import streamlit as st
from underwriting.configuration.decision_model import get_model
from underwriting.configuration.settings import get_config
from underwriting.assessment.overview import load_overview, empty_overview
from underwriting.persistence.overview import OverviewStore
from underwriting.persistence.intake import StoreError
from underwriting.ui.intake import settings_for_ui
from underwriting.ui.overview_diagrams import hierarchy_diagram, workflow_configuration
from underwriting.ui.workflow_svg import workflow_svg


def render_overview(case, prefix):
    model = get_model()
    hierarchy, workflow = st.tabs(['Assessment hierarchy', 'MVP workflow'],
                                  key=prefix + ':overview:section', on_change='rerun')
    if hierarchy.open:
        with hierarchy:
            st.caption(f"{case['company_name']} · {case['case_id']} · Submission v{case['submission_version']}")
            settings = settings_for_ui()
            try:
                if settings.missing(paid=False):
                    raise StoreError('Saved values unavailable: missing ' + ', '.join(settings.missing(paid=False)))
                with st.spinner('Reading saved assessment results…'):
                    view = load_overview(case, OverviewStore(settings), model)
            except (StoreError, OSError) as error:
                view = empty_overview(model, 'Unavailable')
                st.warning(str(error))
            except (ValueError, KeyError, TypeError):
                view = empty_overview(model, 'Unavailable')
                st.warning('Saved assessment data could not be resolved safely. Review the saved stages; no calculation or write was made.')
            with st.container(horizontal=True, horizontal_alignment='center'):
                with st.container(width=1620):
                    st.graphviz_chart(hierarchy_diagram(model, view), width='stretch',
                                      alt='Three-layer assessment pyramid: attributes feed ER groups, then overall BRB; arrows point upward.')
            with st.expander('How to read the hierarchy'):
                st.caption('ER (evidential reasoning) combines the configured attribute belief inputs into Fire and Security distributions. Contribution weights show relative importance, not additive shares of the final result. BRB matches these distributions to rules and combines activated rule conclusions using the shared ER engine.')
                st.caption('Lower-level Unknown is unassigned belief. The BRB interface maps it to an explicit insufficient-information state. Any material residual unassigned BRB output is shown separately. Edge labels are local ER weights; missing results use configured normalised weights. Stale values are withheld. Concern labels are descriptive, not insurance acceptance decisions.')
    if workflow.open:
        with workflow:
            st.subheader('Property underwriting MVP workflow')
            config = workflow_configuration()
            with st.container(horizontal=True, horizontal_alignment='center'):
                with st.container(width=1635):
                    st.image(workflow_svg(config, model, get_config()['screening']), width='stretch',
                             alt='Explicit snake workflow with connected arrows, screening branches, original PDF source and human reviews.')
            with st.expander('Workflow responsibilities and storage'):
                st.caption('Each stage is triggered explicitly. Matching saved results are loaded from Turso; GPT generation occurs only when needed or when deliberately regenerated.')
                st.markdown('**GPT:** extraction, belief proposals and review narrative. **Python:** screening rules, ER/BRB calculations and PDF formatting. **Human:** reviews, confirmations and final workflow action. **Turso:** persistent records and reusable results.')
                st.caption('Turso stores: ' + '; '.join(config['storage']) + '.')
                st.caption('A screening pass permits further assessment; it does not accept insurance. Clarification requires deliberate review / re-screening. Downloading a saved PDF does not call GPT.')
