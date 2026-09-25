# Testing

## Backend

From the repository root:

```sh
NEXTTRACK_ONLINE_CATALOG=0 OPENAI_API_KEY='' prototype/.venv/bin/python -m unittest discover -s prototype/tests -v
```

Coverage includes API validation, routing, bounded GPT intent, evidence-aware metadata, identity
merging, ranking, online/cache/outage paths, rate limiting, Spotify PKCE/session handling and
feedback persistence. Network responses are fixtures. Identity regression records are isolated
in `prototype/tests/fixtures/identity-regressions.json` so the tests do not depend on full imports.

## Desktop and mobile browser tests

```sh
cd prototype
npm ci
npx playwright install chrome
npm run test:ui
```

Playwright starts a separate local server on port 8018 with GPT/live catalogue calls disabled.
Chrome is required. The suite covers prompt growth, keyboard song selection, compact rows,
Spotify links, errors, restart and star-rating payloads. Browser feedback submission is intercepted.
Online-discovery display tests use controlled fixtures, not a live upstream service.

Two research-data scenarios run only after reconstructing the full 69,710-song snapshot:

```sh
NEXTTRACK_FULL_CATALOG_TESTS=1 npm run test:ui
```

Without that flag, those two scenarios are explicitly skipped on both desktop and mobile.
Do not interpret a sample run as full-dataset, live-provider or listening validation.

## Catalogue export

For the public sample, from the repository root:

```sh
prototype/.venv/bin/python prototype/scripts/export_runtime_catalog.py --allow-small-catalogue
NEXTTRACK_ONLINE_CATALOG=0 prototype/.venv/bin/python prototype/scripts/verify_runtime_catalog.py --output /tmp/nexttrack-equivalence.json
```

The explicit small-catalogue switch preserves the original full-data guard by default. The
comparison runs raw and compact loading in isolated processes, checking identities, vectors,
searches and sample ranking results. Rebuild after changing metadata or identity rules.

## Evaluation limits

The research project's dated results were 179 backend and 24 desktop/mobile checks on 18 September
2026. Those are historical, separately recorded runs on the full dataset. The public release's
fresh results are recorded in [release verification](release-verification.md).
Earlier ranking comparisons used version 2.1, not the version 2.2 online retrieval path.
Agent assessments are not human feedback. Broader 2.2 comparison and human listening evaluation
remain separate final-report work. Private raw feedback and evaluator records are not published.
