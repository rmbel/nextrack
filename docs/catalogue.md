# Catalogue scope and reconstruction

## Included public sample

The loader accepts **2,096 unique songs** from 2,098 source rows: 1,000 original prototype records,
1,000 MusicBrainz records and 98 FMA records. One duplicate is merged and the reviewed white-noise
record is excluded. Source IDs and URLs remain where provided. Metadata enrichment is restricted
to these IDs. [Third-party notices](../THIRD_PARTY_NOTICES.md) describe licences and adaptations.

The sample permits offline startup, seed search, recommendations and the public test suite. Online
MusicBrainz retrieval remains available after prompt interpretation. GPT and Spotify require the
operator's own configuration. The sample is not a representative evaluation corpus.

The deployed research snapshot contains 69,710 accepted songs, including a 50,000-song base and
19,710 new canonical recordings from 20,000 eligible Taste Profile source IDs. It is not included
in this public repository. Private feedback, raw user–song triplets, caches, deployment secrets
and restricted university materials are also excluded.

## Rebuild a larger catalogue

Read each provider's terms before downloading. These commands access external providers and may
be slow or rate-limited. Run from the repository root, with dependencies installed. Preserve the
original `catalog.json` if comparing against the saved seed identities.

```sh
mkdir -p prototype/.runtime
mv prototype/data/catalog-expansion.json prototype/.runtime/catalog-expansion-sample.json
prototype/.venv/bin/python prototype/scripts/import_catalog.py --provider itunes --target 6000
prototype/.venv/bin/python prototype/scripts/expand_catalog.py --provider musicbrainz --target 8336
prototype/.venv/bin/python prototype/scripts/expand_catalog.py --provider fma --target 53200
prototype/.venv/bin/python prototype/scripts/enrich_metadata.py --artists 30
```

The importer uses provider metadata, hashes and query reports. FMA's metadata archive is verified
against its documented SHA-1. `enrich_metadata.py` uses cached recording/work/tag evidence and up
to the selected number of artist browse calls. Unsupported language/mood/energy remain unknown.
The active loader cleans and caps the base at 50,000 accepted records. Stop and inspect import
reports on failures; reaching a raw source target alone does not prove the final accepted count.

These commands reproduce the pipeline, **not an identical historical snapshot**: live provider
results can change, and the public repository omits historical raw caches. Recheck accepted counts,
identities and metadata coverage before reporting a comparison. Rebuilding replaces tracked demonstration data; keep the rebuilt
research files local and review their licences before publishing any replacement. The original research count and
historical benchmark results must not be attributed to a newly rebuilt dataset without checking.

## Optional historical listener-ranked addition

The [Taste Profile subset](http://millionsongdataset.com/tasteprofile/) and
[tagtraum CD2](https://www.tagtraum.com/msd_genre_datasets.html) have separate research/non-commercial
conditions. Obtain the files from their publishers and keep them in the ignored directory
`prototype/data/source-cache/taste-profile/`:

| Required filename | Publisher source |
| --- | --- |
| `train_triplets.txt.zip` | Taste Profile subset download |
| `unique_tracks.txt` | MSD AdditionalFiles |
| `tracks_per_year.txt` | MSD AdditionalFiles |
| `sid_mismatches.txt` | Taste Profile documented matching-error list |
| `sid_matches_manually_accepted.txt` | Taste Profile accepted-pair list |
| `MSD-LICENSE.txt` | Official MSongsDB LICENSE |
| `msd_tagtraum_cd2.cls.zip` | tagtraum CD2 annotations |

See the [matching-error explanation](http://millionsongdataset.com/blog/12-2-12-fixing-matching-errors/),
[MSD dataset page](http://millionsongdataset.com/pages/getting-dataset/) and
[licence](https://github.com/tbertinmahieux/MSongsDB/blob/master/LICENSE).

```sh
prototype/.venv/bin/python prototype/scripts/rank_taste_profile.py --limit 20000
prototype/.venv/bin/python prototype/scripts/build_popular_catalog.py
prototype/.venv/bin/python prototype/scripts/export_runtime_catalog.py
NEXTTRACK_ONLINE_CATALOG=0 prototype/.venv/bin/python prototype/scripts/verify_runtime_catalog.py --output /tmp/nexttrack-equivalence.json
```

The ranker counts distinct users per song, validates expected source dimensions, rejects invalid
song/track mappings and retains accepted alternatives. The builder joins exact source IDs with
available year/genre data. Source counts are not summed across potentially overlapping recording
identities. User identifiers never enter runtime catalogue records.

The ranking describes a historical research cohort released in 2011; it is not Spotify popularity
or a current worldwide chart. The importer preserves that distinction in provenance.

## Compact deployment snapshot

The public sample can also be exported with `--allow-small-catalogue`; the full-data minimum
remains enforced unless that flag is explicitly supplied. Generated snapshots are ignored by Git.
A raw/compact comparison checks catalogue identity, vectors, search and sample recommendation
behaviour; it does not measure recommendation quality or live-provider availability.
