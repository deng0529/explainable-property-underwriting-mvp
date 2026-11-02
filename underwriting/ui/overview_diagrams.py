"""Pure Graphviz construction from declarative model/workflow and saved values."""
import textwrap
from graphviz import Digraph, escape
from underwriting.configuration.settings import read_yaml, ConfigError
from underwriting.paths import PROJECT_ROOT


def workflow_configuration():
    config = read_yaml(PROJECT_ROOT / 'config/system_overview.yaml')['workflow']
    nodes = config['nodes']
    if not isinstance(nodes, dict) or not nodes:
        raise ConfigError('system_overview.workflow.nodes must be a nonempty mapping.')
    for identifier, node in nodes.items():
        if not isinstance(node.get('label'), str) or node.get('role') not in COLORS:
            raise ConfigError(f'system_overview.workflow.nodes.{identifier}: invalid label/role.')
    for edge in config['edges']:
        if len(edge) != 3 or edge[0] not in nodes or edge[1] not in nodes or not isinstance(edge[2], str):
            raise ConfigError('system_overview.workflow.edges: invalid node reference/label.')
    rows = config.get('layout_rows')
    if not isinstance(rows, list) or not rows or any(not isinstance(row, list) or not row for row in rows):
        raise ConfigError('system_overview.workflow.layout_rows: nonempty rows are required.')
    placed = [node for row in rows for node in row if node is not None]
    if len(placed) != len(set(placed)) or set(placed) != set(nodes):
        raise ConfigError('system_overview.workflow.layout_rows: each configured node must appear exactly once.')
    return config


COLORS = {'source': '#f1f5f9', 'gpt': '#eef4ff', 'python': '#edf7f3',
          'human': '#fff7e8', 'route': '#f1f5f9', 'stop': '#fff0f0'}


def base_graph(name):
    graph = Digraph(name)
    graph.attr(rankdir='TB', bgcolor='#ffffff', nodesep='.20', ranksep='.52',
               pad='.12', splines='polyline')
    graph.attr('node', shape='box', style='rounded,filled', fillcolor='#f1f5f9',
               color='#b4c0cf', penwidth='.8', fontname='Arial', fontsize='14',
               fontcolor='#172b40', margin='.12,.10')
    graph.attr('edge', color='#8c9cad', penwidth='.8', arrowsize='.65',
               fontname='Arial', fontsize='12', fontcolor='#40566e')
    return graph


def literal(text):
    # Escape DOT backslash sequences as well as HTML-like labels. Graphviz
    # handles quote escaping when serializing the resulting literal string.
    return escape(str(text))


def wrapped(text, width=23):
    return '\n'.join(textwrap.wrap(str(text), width=width, break_long_words=False))


def distribution_lines(values, model, *, overall=False):
    keys = model['brb']['output']['states'] if overall else [*model['grades']['order'], 'unknown']
    parts = [f'{key.title()} {values[key] * 100:.1f}%' for key in keys]
    return '\n'.join(' | '.join(parts[index:index + 2]) for index in range(0, len(parts), 2))


def assessment_label(values, model):
    peak = max(values.values())
    winners = [key for key, value in values.items() if peak - value <= model['tolerances']['assessment_tie']]
    if len(winners) != 1:
        return 'Mixed assessment'
    return 'Insufficient information' if winners[0] == 'unknown' else model['grades']['definitions'].get(winners[0], {}).get('label', winners[0].title())


def value_label(title, row, model, *, kind='attribute'):
    values = row.get('values')
    result = row.get('result')
    if result:
        values = result['state_beliefs'] if kind == 'overall' else dict(result['assigned_beliefs'], unknown=result['unassigned_belief'])
    label = title + '\n'
    if values:
        label += distribution_lines(values, model, overall=kind == 'overall') + '\n' + wrapped(assessment_label(values, model))
        if kind == 'attribute':
            label += '\n' + wrapped(row['state'])
        if kind == 'overall' and result['residual_unassigned'] > model['tolerances']['distribution_sum']:
            residual = result['residual_unassigned'] * 100
            label += '\nResidual unassigned ' + ('<0.1%' if residual < .05 else f'{residual:.1f}%')
    else:
        label += row['state']
    return literal(label)


def hierarchy_diagram(model, view):
    graph = base_graph('assessment_hierarchy')
    graph.attr(nodesep='.14', ranksep='.30', pad='.06')
    graph.attr('node', fontsize='12', margin='.07,.05')
    graph.attr('edge', fontsize='10', arrowsize='.55')
    groups = model['er']['groups']
    attributes = {a['id']: a for a in model['attributes'] if a['enabled']}
    graph.node('overall', value_label('Overall property concern assessment\nBelief rule base (BRB)', view['overall'], model, kind='overall'), fillcolor='#e8effa')
    for identifier, definition in groups.items():
        graph.node('group_' + identifier, value_label(definition['label'] + ' concern assessment\nEvidence aggregation (ER)', view['groups'][identifier], model, kind='group'), fillcolor='#edf7f3')
    ordered = list(dict.fromkeys(member for g in groups.values() for member in g['members'] if member in attributes))
    ordered += [a for a in attributes if a not in ordered]
    for identifier in ordered:
        graph.node('attribute_' + identifier, value_label(wrapped(identifier + ' ' + attributes[identifier]['label'], width=26), view['attributes'][identifier], model))
    for identifier, definition in groups.items():
        total = sum(definition['importance_weights'].values())
        saved = view['groups'][identifier].get('result')
        for member in definition['members']:
            target = 'group_' + member[3:] if member.startswith('er:') else 'attribute_' + member
            weight = saved['effective_weights'][member] if saved else definition['importance_weights'][member] / total
            graph.edge('group_' + identifier, target, label=f'Contribution weight\n{weight:.2f}', dir='back')
    for definition in model['brb']['inputs'].values():
        graph.edge('overall', 'group_' + definition['reference'][3:], dir='back')
    group_nodes = ['group_' + g for g in groups]
    attribute_nodes = ['attribute_' + a for a in ordered]

    def centre(parent, children, rank_nodes, anchor):
        if not children:
            return
        midpoint = len(children) // 2
        if len(children) % 2:
            target = children[midpoint]
        else:
            target = anchor
            graph.node(target, label='', shape='point', width='0', height='0', style='invis')
            rank_nodes.insert(rank_nodes.index(children[midpoint]), target)
        graph.edge(parent, target, style='invis', weight='1000')

    # Invisible zero-size anchors align parents over the middle of their
    # configured children, rather than letting a long label pull the tree aside.
    for identifier, definition in groups.items():
        children = ['attribute_' + member for member in definition['members'] if member in attributes]
        centre('group_' + identifier, children, attribute_nodes, 'centre_' + identifier)
    centre('overall', group_nodes.copy(), group_nodes, 'centre_overall')
    if len(groups) == 2:
        # Reserve a balanced central gap between the two group boxes; the
        # zero-width default otherwise lets DOT pin the root near one parent.
        graph.node('centre_overall', width='3', height='0', shape='box', style='invis')
    ranks = [('Layer 3\nOverall assessment', ['overall']),
             ('Layer 2\nGroup assessments', group_nodes),
             ('Layer 1\nBasic attributes', attribute_nodes)]
    for index, (label, nodes) in enumerate(ranks):
        with graph.subgraph() as rank:
            rank.attr(rank='same')
            rank.node(f'layer{index}', literal(label), shape='plaintext', style='', fontsize='11', fontcolor='#52677e')
            for node in nodes:
                rank.node(node)
            rank.edge(f'layer{index}', nodes[0], style='invis')
            for left, right in zip(nodes, nodes[1:]):
                rank.edge(left, right, style='invis')
    graph.edge('layer0', 'layer1', style='invis', weight='1000')
    graph.edge('layer1', 'layer2', style='invis', weight='1000')
    return graph
