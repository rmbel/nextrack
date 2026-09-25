"""Regressions for cross-provider identity and collaboration-aware diversity."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from prototype.app.catalog_quality import artist_keys, artist_names, prepare_catalog, recording_key


def song(identifier, title="Song", artist="Artist", **fields):
    return {"id": identifier, "title": title, "artist": artist, "year": 2020,
            "genre": "pop", "subgenres": [], "tags": [], "language": "unknown",
            "energy": None, **fields}


class CatalogQualityTests(unittest.TestCase):
    def test_tbt_reordered_collaborators_share_canonical_and_aliases(self):
        original = song("itunes:1515742359", "Tbt", "Dani J & DN-Tato")
        duplicate = song("itunes:1515412715", "TBT", "DN-Tato & Dani J")
        result = prepare_catalog([original, duplicate])
        self.assertEqual([t["id"] for t in result.tracks], [original["id"]])
        self.assertEqual(result.aliases[duplicate["id"]], original["id"])
        self.assertEqual(result.aliases[original["id"]], original["id"])
        self.assertEqual(result.duplicate_groups[original["id"]], [original["id"], duplicate["id"]])

    def test_golden_localized_audrey_nuna_credit_is_the_same_identity(self):
        english = song("golden-original", "Golden", "HUNTR/X, EJAE, AUDREY NUNA, REI AMI & KPop Demon Hunters Cast")
        localized = song("golden-localized", "Golden", "HUNTR/X, EJAE, オードリー・ヌナ, REI AMI & KPop Demon Hunters Cast")
        result = prepare_catalog([english, localized])
        self.assertEqual(result.aliases[localized["id"]], english["id"])
        self.assertIn("audrey nuna", artist_names(localized))

    def test_same_title_different_performers_stay_distinct(self):
        result = prepare_catalog([song("dani", "TBT", "Dani J & DN-Tato"),
                                  song("yatra", "TBT", "Sebastián Yatra, Rauw Alejandro & Manuel Turizo")])
        self.assertEqual(len(result.tracks), 2)

    def test_live_remix_acoustic_and_named_mix_versions_are_preserved(self):
        titles = ["Golden", "Golden (Live)", "Golden (Remix)", "Golden (Acoustic)",
                  "Golden (David Guetta Remix)", "Golden (2019 Mix)", "Golden (Instrumental)"]
        result = prepare_catalog([song(str(index), title) for index, title in enumerate(titles)])
        self.assertEqual(len(result.tracks), len(titles))

    def test_plain_title_with_provider_live_disambiguation_stays_distinct(self):
        tracks = [song("studio", "Song"), song("live", "Song", disambiguation="live, London 2020"),
                  song("remix", "Song", recording_disambiguation="extended remix")]
        self.assertEqual(len(prepare_catalog(tracks).tracks), 3)

    def test_feature_credit_position_and_version_punctuation_are_normalized(self):
        title_feature = song("first", "LOYAL (feat. Drake & Bad Bunny) [Remix]", "PARTYNEXTDOOR")
        artist_feature = song("second", "Loyal - Remix", "PARTYNEXTDOOR, Drake & Bad Bunny")
        self.assertEqual(recording_key(title_feature), recording_key(artist_feature))
        self.assertEqual(prepare_catalog([title_feature, artist_feature]).aliases["second"], "first")

    def test_provider_ids_bridge_alternate_local_ids_and_preserve_first_id(self):
        first = song("original-local-id", source="musicbrainz", source_id="recording-123")
        second = song("musicbrainz:recording-123", title="Different provider spelling")
        third = song("imported-id", title="Another localized title", provider_ids={"musicbrainz": "recording-123"})
        result = prepare_catalog([first, second, third])
        self.assertEqual(len(result.tracks), 1)
        self.assertEqual(set(result.aliases.values()), {"original-local-id"})

    def test_id_namespaces_do_not_collide(self):
        result = prepare_catalog([song("local-a", "First", source="itunes", source_id="123"),
                                  song("local-b", "Second", source="musicbrainz", source_id="123")])
        self.assertEqual(len(result.tracks), 2)

    def test_bridge_record_merges_existing_groups_transitively(self):
        first = song("first", "Original", source="itunes", source_id="123")
        second = song("second", "Localized", source="musicbrainz", source_id="abc")
        bridge = song("bridge", "Localized", provider_ids={"itunes": "123", "musicbrainz": "abc"})
        result = prepare_catalog([first, second, bridge])
        self.assertEqual(result.aliases, {"first": "first", "second": "first", "bridge": "first"})

    def test_unknown_empty_metadata_does_not_collapse_unrelated_records(self):
        self.assertEqual(len(prepare_catalog([song("a", "", ""), song("b", "", "")]).tracks), 2)

    def test_structured_credits_override_ambiguous_punctuation_and_add_provider_ids(self):
        track = song("a", artist="Earth, Wind & Fire & Guest", artist_credits=[
            {"name": "Earth, Wind & Fire", "id": "band-123", "source": "musicbrainz"},
            {"name": "Guest", "id": "guest-456", "source": "musicbrainz"},
        ])
        self.assertEqual(artist_names(track), frozenset({"earth wind fire", "guest"}))
        self.assertIn("artist-id:musicbrainz:band-123", artist_keys(track))
        self.assertTrue(artist_keys(track) & artist_keys(song("b", artist="Earth, Wind & Fire")))

    def test_raw_musicbrainz_credit_uses_canonical_artist_name(self):
        track = song("a", artist="Localized", **{"artist-credit": [
            {"name": "オードリー・ヌナ", "artist": {"name": "AUDREY NUNA", "id": "audrey-mbid"}}]})
        self.assertEqual(artist_names(track), frozenset({"audrey nuna"}))
        self.assertIn("artist-id:musicbrainz:audrey-mbid", artist_keys(track))

    def test_compound_band_names_stay_whole_alone_and_in_collaborations(self):
        for band, normalized in (("Earth, Wind & Fire", "earth wind fire"),
                                 ("Florence + The Machine", "florence the machine"),
                                 ("Tyler, The Creator", "tyler the creator")):
            with self.subTest(band=band):
                self.assertEqual(artist_names(song("a", artist=band)), frozenset({normalized}))
                self.assertEqual(artist_names(song("b", artist=band + " & Guest")), frozenset({normalized, "guest"}))

    def test_constituent_overlap_finds_same_artist_across_collaborations(self):
        solo = song("solo", artist="Drake")
        collaboration = song("collab", artist="PARTYNEXTDOOR & Drake")
        featured = song("featured", "Song (feat. Drake)", "Another Artist")
        self.assertTrue(artist_keys(solo) & artist_keys(collaboration))
        self.assertTrue(artist_keys(solo) & artist_keys(featured))

    def test_ambiguous_frio_pair_is_not_merged_by_artist_subset_or_accent(self):
        first = song("itunes:693361492", "Frio, frio (Cold, cold)", "Bebu Silvetti & Plácido Domingo")
        second = song("itunes:934991402", "Frío, frío", "Plácido Domingo, Bebu Silvetti, Miami Symphonic Strings & Alfredo Oliva")
        self.assertEqual(len(prepare_catalog([first, second]).tracks), 2)

    def test_reviewed_nonmusic_quarantine_is_narrow_and_excludes_seed_alias(self):
        excluded = song("white-noise-therapy-white-noise-3-hour-long", "White Noise 3 Hour Long", "White Noise Therapy")
        music = song("disclosure-white-noise", "White Noise", "Disclosure & AlunaGeorge")
        result = prepare_catalog([excluded, music])
        self.assertEqual([track["id"] for track in result.tracks], [music["id"]])
        self.assertEqual(result.quarantined[0]["id"], excluded["id"])
        self.assertNotIn(excluded["id"], result.aliases)

    def test_missing_metadata_is_filled_with_provenance_without_mutating_sources(self):
        first = song("old", language="unknown", metadata_quality={"language": "unknown"})
        later = song("new", language="portuguese", languages=["portuguese"], tags=["chill"], energy=2,
                     metadata_quality={"language": "work-language"},
                     metadata_evidence={"recording_url": "https://musicbrainz.org/recording/abc"})
        originals = deepcopy([first, later])
        result = prepare_catalog([first, later])
        canonical = result.tracks[0]
        self.assertEqual(canonical["id"], "old")
        self.assertEqual(canonical["language"], "portuguese")
        self.assertEqual(canonical["languages"], ["portuguese"])
        self.assertEqual(canonical["metadata_quality"]["language"], "work-language")
        self.assertEqual(canonical["metadata_inherited"][0]["from_track_id"], "new")
        self.assertEqual(canonical["metadata_inherited"][0]["metadata_evidence"], later["metadata_evidence"])
        self.assertEqual([first, later], originals)

    def test_present_metadata_is_not_overwritten_by_duplicate(self):
        first = song("old", language="english", energy=5, tags=["energetic"])
        later = song("new", language="spanish", languages=["spanish"], energy=1, tags=["calm"])
        canonical = prepare_catalog([first, later]).tracks[0]
        self.assertEqual(canonical["language"], "english")
        self.assertNotIn("languages", canonical)
        self.assertEqual(canonical["energy"], 5)
        self.assertEqual(canonical["tags"], ["energetic"])

    def test_real_reported_golden_tbt_and_frio_regressions(self):
        fixture = Path(__file__).parent / "fixtures" / "identity-regressions.json"
        rows = json.loads(fixture.read_text())
        result = prepare_catalog(rows)
        self.assertEqual(result.aliases["huntr-x-ejae-rei-ami-kpop-demon-hunters-cast-golden"],
                         "huntr-x-ejae-audrey-nuna-rei-ami-kpop-demon-hunters-cast-golden")
        self.assertEqual(result.aliases["itunes:1515412715"], "itunes:1515742359")
        self.assertNotEqual(result.aliases["itunes:693361492"], result.aliases["itunes:934991402"])
        self.assertNotIn("white-noise-therapy-white-noise-3-hour-long", result.aliases)


if __name__ == "__main__":
    unittest.main()
