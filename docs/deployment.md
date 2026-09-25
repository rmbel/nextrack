# Deploy a separate preview

Use your own Vercel project, OpenAI credential, Spotify developer app and Redis database.
The existing protected research preview is managed separately; publishing this repository does
not connect it to automatic production deployment.

1. Run the local setup and tests.
2. From the repository root, generate a runtime snapshot:
   `prototype/.venv/bin/python prototype/scripts/export_runtime_catalog.py --allow-small-catalogue`.
   Omit the small-catalogue switch for a rebuilt full research dataset.
3. In `prototype/`, run `npx vercel login` and `npx vercel link` for your selected project.
4. Set server-side Preview environment variables in Vercel:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Your OpenAI credential |
| `OPENAI_MODEL` | A structured-output Responses API model available to your account |
| `OPENAI_TIMEOUT_SECONDS` | `25` |
| `SPOTIFY_CLIENT_ID` | Your Spotify developer app ID |
| `SPOTIFY_REDIRECT_URI` | `https://YOUR-HOST/auth/spotify/callback` |
| `MUSICBRAINZ_CONTACT` | A maintained contact address or project URL |
| `NEXTTRACK_CATALOG_SNAPSHOT` | `deployment/data/catalog-runtime.json.gz` |
| `UPSTASH_REDIS_REST_URL` | Your Redis REST endpoint |
| `UPSTASH_REDIS_REST_TOKEN` | Your Redis credential |
| `NEXTTRACK_STORAGE_PREFIX` | A namespace for this installation |

Register the exact redirect URI in Spotify before login. A client secret is not used for PKCE.
On Vercel, shared Redis is required for sessions/ratings; a missing store produces an explicit
service-unavailable response. Local disk is not a durable hosted store.

5. From `prototype/`, build and deploy the allowlisted bundle:

```sh
python3 scripts/prepare_vercel_bundle.py
cd .vercel-package
npx vercel deploy --target preview
```

Inspect the printed file manifest before deployment. Keep deployment protection enabled while
validating. After adding an alias, update the Spotify callback/environment and deploy again.
Verify `/health`, search, a recommendation and login on the new origin. Read back any diagnostic
rating and remove only that known test record. Never upload local feedback, `.env` or raw caches.
