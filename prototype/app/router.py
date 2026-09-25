"""Conservative, reproducible routing; unrecognised intent goes to the interpreter."""
from .catalog import tokenize
from .recommender import parse_prompt
from .metadata import LANGUAGE_NAMES

FILLER = set('a an the some songs song music tracks track in and with please recommend me'.split())
CONTROLS = set('recent new latest modern current old older classic throwback spanish espanol english discovery discover newartist variety popular familiar hits well known'.split())


def choose_strategy(seed_count: int, prompt: str) -> tuple[str, str]:
    if not seed_count:
        return 'ai', 'Your prompt starts the discovery because no songs were selected.'
    if not prompt.strip():
        return 'recommendation', 'Your selected songs provide the playlist context.'
    signals = parse_prompt(prompt)
    represented = set(CONTROLS) | LANGUAGE_NAMES
    for term in signals['preferred_genres'] + signals['preferred_tags']:
        represented.update(tokenize(term))
    if tokenize(prompt) - represented - FILLER:
        return 'hybrid', 'Your prompt needs interpretation alongside the selected songs.'
    return 'recommendation', 'The catalogue can directly interpret these musical preferences.'
