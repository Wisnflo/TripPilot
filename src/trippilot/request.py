"""Preserve the user's source request; a translation must not erase it."""


def request_text(task):
    original=task.get('nature_language') or ''
    english=task.get('nature_language_en') or ''
    if original and english and original!=english:
        return original+'\nProvided translation (original request is authoritative):\n'+english
    return original or english
