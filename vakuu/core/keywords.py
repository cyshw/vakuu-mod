"""Keyword glossary pulled from the compact state for the prompt."""

def pull_keywords(value, glossary=None):
    """Replace every card/orb/potion keyword list with names, collecting each definition once.

    The same keyword text (Exhaust, Channel, orb rules...) was repeated on every card in hand and deck.
    """
    glossary = {} if glossary is None else glossary
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == 'keywords' and isinstance(item, list):
                value[key] = [k.get('name') for k in item if isinstance(k, dict)]
                glossary.update({k.get('name'): k.get('description') for k in item if isinstance(k, dict) and k.get('name')})
            else: pull_keywords(item, glossary)
    elif isinstance(value, list):
        for item in value: pull_keywords(item, glossary)
    return glossary
