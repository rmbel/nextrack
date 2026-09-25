# NextTrack

Playlist recommendations from song references and a short prompt. A University of London
Computer Science final project by Rocío Belfiore.

- Select 0–50 unique reference songs; optionally describe the music you want.
- Receive up to 10 recommendations (the interface asks for five).
- GPT interprets complex requests into validated musical preferences. The application selects
  songs from verified catalogue records; GPT does not invent track IDs.
- MusicBrainz discovery adds up to 100 online candidates after interpretation, with a shared
  rate limit, persistent cache and saved-catalogue fallback.
- Optional Spotify sign-in and listening links; one 1–5 star playlist rating plus a comment.

The implementation is **hybrid-content-mmr/2.2**: FastAPI/Python backend and plain HTML/CSS/JavaScript
frontend. [Architecture](docs/architecture.md) · [Data and reconstruction](docs/catalogue.md) ·
[Testing](docs/testing.md) · [Deployment](docs/deployment.md).

## Run locally

Use Python 3.12. From the repository root:

```sh
python3.12 -m venv prototype/.venv
prototype/.venv/bin/python -m pip install -r prototype/requirements-dev.txt
cp prototype/.env.example prototype/.env
cd prototype
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8017 --reload --no-access-log
```

Open <http://127.0.0.1:8017/> on the computer running the server. Seed-only recommendations work
without credentials. A non-empty prompt or at least one seed is required. Prompt-only GPT requests
need `OPENAI_API_KEY`; optional Spotify login needs your own developer app configuration.
The example contains placeholders, never working credentials.

This checkout includes a **small demonstration catalogue**, not the deployed 69,710-song research
snapshot. The code and online MusicBrainz integration are the same. See [catalogue scope](docs/catalogue.md)
for sample counts, attribution, reconstruction commands and reproducibility limits.

## Test

From the repository root, with the environment created above:

```sh
NEXTTRACK_ONLINE_CATALOG=0 OPENAI_API_KEY='' prototype/.venv/bin/python -m unittest discover -s prototype/tests -v
cd prototype
npm ci
npx playwright install chrome
npm run test:ui
```

Tests use fixtures for external services. Two full-catalogue UI scenarios per viewport are opt-in;
[testing instructions](docs/testing.md) explain the sample and full-data suites. Test results do not
establish human listening satisfaction.

## Configuration

`prototype/.env` is read only for the GPT, Spotify and MusicBrainz settings listed in `.env.example`.
Redis, catalogue-snapshot and storage settings must be exported as process environment variables
(or set in the deployment provider). Do not commit `.env`, tokens, cookies, feedback or caches.

The project's existing [hosted preview](https://nextrack-1242.vercel.app/) is access-protected;
public source availability does not grant preview access. A new clone can run locally or be deployed
to a separate project with its own credentials.

## Data and rights

No audio files, lyrics, private ratings, listener-level source data or API credentials are included.
[Third-party notices](THIRD_PARTY_NOTICES.md) document source attribution and data licences.
No blanket software licence is granted by this repository; third-party material retains its own terms.
