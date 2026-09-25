# Third-party data and assets

Source code and datasets have separate rights. No blanket licence for this project's code is
specified. Dependency licences remain with their respective authors.

## Included demonstration metadata

- **Original prototype records:** selected song titles, artists, years and genre metadata from
  curated seeds and historical Apple chart/search results. This file is retained for stable test
  identities. Legacy genre-derived mood/energy/language are withdrawn by active normalisation;
  they are not claimed as source-verified traits. No Apple artwork, audio, previews or lyrics
  are included. Four factual provider records are retained as identity-regression fixtures.
- **MusicBrainz contributors / MetaBrainz Foundation:** core metadata is
  [CC0](https://musicbrainz.org/doc/About/Data_License). Supplementary community tags/genres are
  [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/). These adapted fields retain
  that licence. Adaptations include schema conversion, genre/tag normalisation, recording identity
  matching and source-linked metadata enrichment. Recording URLs and identifiers are preserved.
- **FMA:** Michaël Defferrard, Kirell Benzi, Pierre Vandergheynst and Xavier Bresson,
  *FMA: A Dataset For Music Analysis* (2017), [source](https://github.com/mdeff/fma).
  Published metadata is [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
  The included metadata sample has adapted fields, genre normalisation and energy rescaling;
  source IDs/URLs and available provenance are preserved. Audio licences are independent; no
  audio is included.

## Optional research inputs, not distributed here

- The Echo Nest / Million Song Dataset: Thierry Bertin-Mahieux, Daniel P. W. Ellis, Brian Whitman
  and Paul Lamere, *The Million Song Dataset*, ISMIR 2011. See the
  [Taste Profile source](http://millionsongdataset.com/tasteprofile/) and
  [official terms](https://github.com/tbertinmahieux/MSongsDB/blob/master/LICENSE).
- Hendrik Schreiber, *Improving Genre Annotations for the Million Song Dataset*, ISMIR 2015,
  pp. 241–247. [tagtraum CD2](https://www.tagtraum.com/msd_genre_datasets.html) is for research only,
  strictly non-commercial. Obtain these datasets directly and follow their conditions.

The 20,000-song listener-ranked import and combined 69,710-song research snapshot are omitted.
The importer preserves per-source attribution; historical cohort popularity is not a current
worldwide or Spotify chart. Raw listener identifiers are never needed at application runtime.

## Assets and services

The Spotify symbol identifies links to Spotify; it is a Spotify trademark. The Echo Nest logo in
the linked credits page accompanies attribution for optional research data. Their presence grants
no trademark ownership. OpenAI and Spotify services require the operator's own configuration and
remain subject to their service terms.
