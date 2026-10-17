"""Bounded wire views over the existing authoritative work graph."""


def overview(snapshot, *, selected='', query='', include_hidden=False, history_limit=0,
             history_offset=0, show='active', filters=None):
    filters = filters or {}
    nodes = snapshot['nodes']
    by_id = {node['id']: node for node in nodes}
    members = {}
    for node in nodes:
        members.setdefault(node['root_id'], []).append(node)
    selected_session = next((s for s in snapshot['sessions']
                             if selected in (s['id'], s['tmux_name'])), None)
    selected_id = snapshot['aliases'].get(selected_session['id'], selected_session['id']) if selected_session else selected
    selected_root = by_id.get(selected_id, {}).get('root_id')
    visible = lambda node: include_hidden or not node['hidden'] or node['mechanical'] == 'running' or node['waiting']
    query = query.lower().strip()

    def matches(node):
        text = ' '.join(str(node.get(key) or '') for key in ('title', 'task', 'repository', 'project_name'))
        return (not query or query in text.lower()) and all(
            not value or node.get(key) == value for key, value in filters.items())

    active, historical, matched = [], [], []
    current_attention = historical_attention = 0
    for group in snapshot['groups']:
        items = members.get(group['root_id'], [])
        eligible = [node for node in items if visible(node)]
        running = any(node['mechanical'] == 'running' or node['waiting'] for node in eligible)
        attention = sum(bool(node['needs_attention']) for node in eligible)
        if running:
            current_attention += attention
        else:
            historical_attention += attention
        hits = [node for node in eligible if matches(node)]
        if not hits or show == 'attention' and not attention:
            continue
        matched.extend(node['id'] for node in hits)
        (active if running else historical).append(group)
    chosen = active + historical[history_offset:history_offset + history_limit]
    roots = {group['root_id'] for group in chosen}
    if selected_root and selected_root not in roots:
        chosen += [dict(group, context_only=True) for group in snapshot['groups'] if group['root_id'] == selected_root]
        roots.add(selected_root)
    keep = {node['id'] for node in nodes if node['root_id'] in roots}
    sessions = [dict(session) for session in snapshot['sessions']
                if snapshot['aliases'].get(session['id'], session['id']) in keep]
    result = []
    for node in nodes:
        if node['id'] not in keep:
            continue
        item = dict(node)
        if node['root_id'] != selected_root:
            item['task'] = item['task'][:300]
            item['attempts'] = []
        result.append(item)
    return {**snapshot, 'nodes': result, 'sessions': sessions, 'groups': chosen,
            'aliases': {key: value for key, value in snapshot['aliases'].items() if value in keep},
            'page': {'history_total': len(historical), 'history_offset': history_offset,
                     'history_loaded': min(history_limit, max(0, len(historical) - history_offset)),
                     'has_more_history': history_offset + history_limit < len(historical),
                     'active_attention': current_attention, 'historical_attention': historical_attention,
                     'running_session_ids': [session['id'] for session in snapshot['sessions'] if session['running']],
                     'matched_ids': matched if query else []},
            'filters': {key: sorted({str(node[key]) for node in nodes if node.get(key)})
                        for key in ('project_id', 'profile', 'tool')},
            'project_names': {node['project_id']: node['project_name'] for node in nodes if node.get('project_id')}}
