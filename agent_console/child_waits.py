"""Resolve a wait batch once; subsequent observations use durable child IDs."""


def select_children(rows, parent_id, selectors, *, ids_only=False):
    if not isinstance(selectors, list) or not 1 <= len(selectors) <= 256:
        raise ValueError('select between 1 and 256 direct children')
    selected, seen = [], set()
    for selector in selectors:
        if not isinstance(selector, str) or not 1 <= len(selector) <= 100:
            raise ValueError('child selectors must be nonempty session IDs or names')
        matches = [row for row in rows if row['id'] == selector or
                   (not ids_only and row['tmux_name'] == selector)]
        if len(matches) != 1:
            raise ValueError('child selector is unknown or ambiguous; use a unique durable child ID')
        child = matches[0]
        if child.get('parent_session_id') != parent_id:
            raise PermissionError('selected session is not a direct child of the requested parent')
        if child['id'] not in seen:
            selected.append(child)
            seen.add(child['id'])
    return selected
