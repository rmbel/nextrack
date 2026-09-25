"""Read server settings from environment or the private local .env file."""
import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[1] / '.env'
ALLOWED_SETTINGS = {'OPENAI_API_KEY', 'OPENAI_MODEL', 'OPENAI_TIMEOUT_SECONDS', 'SPOTIFY_CLIENT_ID', 'SPOTIFY_REDIRECT_URI',
                    'NEXTTRACK_ONLINE_CATALOG', 'MUSICBRAINZ_CONTACT', 'MUSICBRAINZ_USER_AGENT'}


def server_settings():
    values = {}
    if ENV_PATH.is_file():
        for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
            key, separator, value = line.strip().partition('=')
            if separator and key.strip() in ALLOWED_SETTINGS:
                values[key.strip()] = value.strip()
    # Explicit environment values (including empty values) always take precedence.
    for key in ALLOWED_SETTINGS:
        if key in os.environ:
            values[key] = os.environ[key]
    return values
