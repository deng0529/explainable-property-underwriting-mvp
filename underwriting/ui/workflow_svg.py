"""Escaped, responsive SVG with deterministic box-to-box workflow connectors.

Presentation only: rows and logical dependencies come from the diagram YAML.
No browser scripts, numerical inference, persistence or network access.
"""
from html import escape
from underwriting.ui.overview_diagrams import COLORS


def workflow_svg(config, model, screening):
    box_width, box_height = 194, 56
    columns = max(len(row) for row in config['layout_rows'])
    width = 40 + columns * box_width + (columns - 1) * 20
    height = 42 + (len(config['layout_rows']) - 1) * 86 + box_height + 50
    positions = {node: (20 + column * 214, 42 + row * 86)
                 for row, nodes in enumerate(config['layout_rows'])
                 for column, node in enumerate(nodes) if node is not None}
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img">',
           '<title>MVP workflow: explicit stages, screening branches and human review</title>',
           '<defs><marker id="arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L7,3.5 L0,7 Z" fill="#64748b"/></marker></defs>',
           '<rect width="100%" height="100%" fill="white"/>']

    def text(x, y, value, size=12, color='#52677e', weight='400'):
        return f'<text x="{x}" y="{y}" text-anchor="middle" font-family="Arial,sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(str(value))}</text>'

    for source, target, label in config['edges']:
        sx, sy = positions[source]
        tx, ty = positions[target]
        label_at = None
        dashed = False
        if (source, target) == ('pdf', 'evidence'):
            # Full source bypass runs above the main row, away from every box.
            points = [(sx + box_width / 2, sy), (sx + box_width / 2, 20),
                      (tx + box_width / 2, 20), (tx + box_width / 2, ty)]
            label_at = ((sx + tx + box_width) / 2, 13)
            dashed = True
        elif sy == ty:
            right = tx > sx
            points = [(sx + (box_width if right else 0), sy + box_height / 2),
                      (tx + (0 if right else box_width), ty + box_height / 2)]
            if label:
                label_at = ((sx + tx + box_width) / 2,
                            sy - 7 if label == 'Proceed' else sy + box_height + 14)
        elif sx == tx:
            points = [(sx + box_width / 2, sy + box_height), (tx + box_width / 2, ty)]
            if label:
                label_at = (tx + box_width / 2 + 58, (sy + box_height + ty) / 2 + 4)
        else:
            elbow = (sy + box_height + ty) / 2
            points = [(sx + box_width / 2, sy + box_height), (sx + box_width / 2, elbow),
                      (tx + box_width / 2, elbow), (tx + box_width / 2, ty)]
            if label:
                label_at = ((sx + tx + box_width) / 2, elbow - 5)
        path = 'M' + ' L'.join(f'{x:g},{y:g}' for x, y in points)
        dash = ' stroke-dasharray="5 4"' if dashed else ''
        svg.append(f'<g data-edge="{escape(source)}:{escape(target)}"><title>{escape(source + " → " + target + (": " + label if label else ""))}</title><path d="{path}" fill="none" stroke="#64748b" stroke-width="1.25" stroke-linejoin="round" marker-end="url(#arrow)"{dash}/></g>')
        if label_at:
            svg.append(text(*label_at, 'Outside' if label == 'Outside demo scope' else label, size=11))
    # Draw boxes after connectors so lines always terminate cleanly at boundaries.
    for identifier, definition in config['nodes'].items():
        x, y = positions[identifier]
        label = definition['label']
        if identifier == 'er':
            label = 'Python ER aggregation\n' + ' + '.join(g['label'] for g in model['er']['groups'].values()) + ' distributions'
        elif identifier == 'screening':
            label = f"Python screening\nApply {len(screening['checks'])} configured checks"
        lines = label.splitlines()
        svg.append(f'<g data-node="{escape(identifier)}"><rect x="{x}" y="{y}" width="{box_width}" height="{box_height}" rx="9" fill="{COLORS[definition["role"]]}" stroke="#b4c0cf" stroke-width="1"/>')
        first_y = y + box_height / 2 - (len(lines) - 1) * 8 + 4
        for index, line in enumerate(lines):
            svg.append(text(x + box_width / 2, first_y + index * 16, line,
                            size=13 if index == 0 else 12, color='#172b40', weight='600' if index == 0 else '400'))
        svg.append('</g>')
    svg.append('</svg>')
    return ''.join(svg)
