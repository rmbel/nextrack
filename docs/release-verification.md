# Public release verification — 25 September 2026

Scope: the source release with 2,096 accepted demonstration songs, not the larger research snapshot.

| Check | Result |
| --- | --- |
| Backend regression suite | 179 passed, no skips |
| Desktop/mobile Playwright suite | 20 passed; 4 explicit full-catalogue checks skipped |
| Compact export | 2,096 songs; 1 alias; JSON/gzip round trip passed |
| Raw vs compact behaviour | Equivalent identities, vectors, searches and sampled rankings |
| Allowlisted deployment bundle | Assembled successfully with dummy project-link IDs |
| Publication review | No detected working credentials, private feedback or raw listener histories |

The local run used the application's existing Python environment and Chrome on macOS. GitHub
Actions defines a separate Python 3.12/Linux check; its live status is available in the repository's
Actions tab and is not inferred from these local results.

External service responses in regression tests are controlled fixtures. GPT service availability,
Spotify account access and subjective listening quality are outside this test run. Full-data UI
scenarios require `NEXTTRACK_FULL_CATALOG_TESTS=1` and the separately rebuilt research catalogue.

The public checkout uses the same application Python modules and recommendation algorithm as the
research workspace. Differences are sample data, public documentation/credits, portable regression
fixtures and the explicit optional small-catalogue export flag. No hosted deployment was changed
by publication.
