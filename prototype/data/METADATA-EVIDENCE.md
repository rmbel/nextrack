# Metadata enrichment rules — 17 September 2026

## Implemented evidence levels

- **Language:** FMA's explicit per-track `language_code`, FMA's explicit per-track instrumental flag, or the lyric-language codes of MusicBrainz works linked to the recording. Artist nationality, album packaging language, song-title language and genre do not establish lyric language. Multilingual works retain every supported language; translated, karaoke and medley relationship exceptions are not automatically inherited.
- **Energy:** FMA publishes Echo Nest audio-analysis features. Their numeric energy in [0, 1] is rescaled as `1 + 4 × energy` for the existing recommender, with the raw value preserved. This is a provider audio-analysis estimate, not listening evidence collected by NextTrack.
- **Community energy labels:** explicit labels such as `energetic` or `calm` can supply an ordinal estimate, separately marked `community-label-estimate`. Conflicting low/high labels leave energy unknown. Genres, including dance and jazz, never imply energy.
- **Mood:** only explicit per-recording MusicBrainz tags or per-track FMA tags are normalised through a small documented vocabulary. These remain source-reported descriptors, not verified listener emotions. Positive MusicBrainz votes are required; synthetic FMA tag presence counts are not presented as votes.
- **Legacy baseline:** the original builder assigned language, mood and energy from `GENRE_PROFILES`. Runtime normalisation withdraws these unsupported attributes, including genre-derived `instrumental` subgenres. The original JSON remains available for historical comparisons.

## Reproduction and scope

`python3 prototype/scripts/enrich_metadata.py --artists 0` rebuilds `metadata-evidence.json` from local cached MusicBrainz responses. `--artists N` adds at most N live artist browse calls using `work-rels tags artist-credits`, the configured client identity and shared rate limiter. The 17 September enrichment obtained 30 artist browse responses without failures. Cached cross-provider language transfer requires exact normalised title and full artist credit, no recording disambiguation, and agreement among available linked-work languages. It does not transfer audio energy or subjective recording tags.

The locally generated `metadata-enrichment-report.json` describes raw input coverage before runtime cleaning and the 50,000-track cap. Use the running catalogue's `metadata_coverage` result for final app coverage; the raw report must not be represented as the final catalogue count. `assembled_at` timestamps describe local evidence assembly, not a fresh validation of every cached source record.

The FMA source is a 2017 research dataset and does not resolve requests for current releases. Many songs still have unknown language, mood or energy. Unknowns remain explicit. Mood labels and audio-analysis energy are different evidence types and their counts must be reported separately.

## Primary sources

- [MusicBrainz: How to Use Works](https://musicbrainz.org/doc/How_to_Use_Works): the work language field describes lyrical content; a work without lyrics uses the no-lyrics code.
- [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API): relationship includes and recording/work data.
- [MusicBrainz Folksonomy Tagging](https://musicbrainz.org/doc/Folksonomy_Tagging): arbitrary community labels and positive vote totals; tags can include opinions and are not acoustic measurements.
- [FMA dataset authors and source repository](https://github.com/mdeff/fma): per-track metadata/tags and published Echo Nest audio features; metadata CC BY 4.0. Defferrard, Benzi, Vandergheynst and Bresson (2017), *FMA: A Dataset For Music Analysis*, ISMIR.

## Verification

Thirteen focused unit tests cover provenance, unsupported inference removal, source-language normalisation, multilingual/instrumental exceptions, conflict handling, bounded numeric energy and per-track FMA tags. These tests exercise metadata handling; they do not establish subjective recommendation enjoyment.
