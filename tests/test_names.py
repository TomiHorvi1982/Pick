"""Song names tidied: what a video adds is taken off, and a catalogue's answer
is believed only when it names the artist and the title we already had."""
import names


def test_what_a_video_adds_to_a_title_is_taken_off():
    assert names.clean("Would? (Official HD Video)") == "Would?"
    assert names.clean("Monster - Album Version (Edited)") == "Monster"
    assert names.clean("Pushing me away with lyrics") == "Pushing me away"
    assert names.clean("Divokej Bill - UNISONO videoklip - HD") == "Divokej Bill - UNISONO"
    assert names.clean("Smooth Criminal [Official Music Video] (Remastered 2019)") == "Smooth Criminal"
    assert names.clean("(Sittin' On) The Dock of the Bay") == "The Dock of the Bay"
    assert names.clean("Lyrics") == "Lyrics"                      # a song may be called that
    assert names.clean("Blind (Official HD") == "Blind"


def test_the_uploader_is_told_from_the_band():
    assert names.tidy("BeginNewLife", "Slayer- Raining Blood (lyrics)") == {"artist": "Slayer", "title": "Raining Blood"}
    assert names.tidy("Tomas Klus", "Tomáš Klus - Navěky") == {"artist": "Tomas Klus", "title": "Navěky"}
    assert names.tidy("metallica", "one") == {"artist": "Metallica", "title": "One"}
    assert names.tidy("karel gott", "lady carneval") == {"artist": "Karel Gott", "title": "Lady carneval"}
    assert names.tidy("AC/DC", "Back In Black (Official 4K Video)") == {"artist": "AC/DC", "title": "Back In Black"}


def test_a_catalogues_answer_is_believed_only_when_it_is_this_song():
    found = [{"title": "Raining Blood", "artists": ["Slayer"], "album": "Reign in Blood (Expanded)"},
             {"title": "Raining Blood (Live)", "artists": ["Slayer"], "album": "Decade of Aggression"}]
    assert names.pick(found, "BeginNewLife", "Slayer- Raining Blood (lyrics)", "BeginNewLife - Slayer- Raining Blood (lyrics)") == \
        {"artist": "Slayer", "title": "Raining Blood", "album": "Reign in Blood (Expanded)"}
    # accents come back with the official name
    assert names.pick([{"title": "Dobrák od kosti", "artists": ["Chinaski"], "album": "Premium"}], "Chinaski", "Dobrak od kosti")["title"] == "Dobrák od kosti"
    # another song, another band: nothing
    assert names.pick([{"title": "One", "artists": ["U2"], "album": "Achtung Baby"}], "Metallica", "One") is None
    assert names.pick([{"title": "Someone", "artists": ["Metallica"], "album": "X"}], "Metallica", "One") is None
    assert names.pick([{"title": "One", "artists": ["Metallica"], "album": "...And Justice for All"}], "ElMango64", "Metallica - One")["artist"] == "Metallica"
    assert names.pick([], "A", "B") is None


def test_a_name_we_had_with_its_accents_is_not_traded_for_one_without():
    got = names.pick([{"title": "Naveky", "artists": ["Tomas Klus"], "album": "Hlavni uzaver splinu"}], "Tomas Klus", "Tomáš Klus - Navěky")
    assert (got["artist"], got["title"]) == ("Tomáš Klus", "Navěky")
    got = names.pick([{"title": "Srdce jako knize Rohan", "artists": ["Richard Müller"], "album": "A hoste"}], "y0bphx4", "Richard Müller - Srdce Jako Kníže Rohan")
    assert got["title"] == "Srdce Jako Kníže Rohan"


import json
import time

import server


def _stems(root, name, meta=None):
    d = root / "Stems" / name
    d.mkdir(parents=True)
    for stem in server.STEM_NAMES:
        (d / f"{stem}.mp3").write_bytes(b"x")
    if meta:
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_every_separated_song_gets_its_official_name_once(client, tmp_music_root, monkeypatch):
    asked = []
    answers = {"Slayer Raining Blood": {"artist": "Slayer", "title": "Raining Blood", "album": "Reign in Blood"}}

    def official(artist, title, name):
        asked.append(name)
        if "Broken" in name:
            raise OSError("no internet")
        return answers.get(f"{server._names.tidy(artist, title)['artist']} {server._names.tidy(artist, title)['title']}")
    monkeypatch.setattr(server, "_official_song", official)
    a = _stems(tmp_music_root, "BeginNewLife - Slayer- Raining Blood (lyrics)", {"artist": "BeginNewLife", "title": "Slayer- Raining Blood (lyrics)"})
    b = _stems(tmp_music_root, "gunit - Pushing me away with lyrics", {"artist": "gunit", "title": "Pushing me away with lyrics", "album": "X"})
    c = _stems(tmp_music_root, "Mine", {"artist": "Me", "title": "Mine (Demo)", "manual": True})
    e = _stems(tmp_music_root, "Broken - Song (Official Video)", {"artist": "Broken", "title": "Song (Official Video)"})
    assert client.get("/api/stems-names").json()["left"] == 3
    client.post("/api/stems-names/fix?more=false")
    for _ in range(300):
        st = client.get("/api/stems-names").json()
        if not st["running"]:
            break
        time.sleep(0.02)
    assert (st["official"], st["tidied"], st["failed"], st["left"]) == (1, 1, 1, 1)
    meta = lambda d: server._stems_meta(d)
    assert (meta(a)["artist"], meta(a)["title"], meta(a)["album"], meta(a)["source"]) == ("Slayer", "Raining Blood", "Reign in Blood", "official")
    assert (meta(b)["artist"], meta(b)["title"], meta(b)["album"]) == ("Gunit", "Pushing me away", "X")
    assert meta(c)["title"] == "Mine (Demo)"                                   # typed by hand: left alone
    assert meta(e)["title"] == "Song (Official Video)"                         # could not be asked: next time
    assert server._stems_meta_raw(a)["was"]["title"] == "Slayer- Raining Blood (lyrics)"
    # the second run asks only about what is left
    asked.clear()
    client.post("/api/stems-names/fix?more=false")
    for _ in range(300):
        if not client.get("/api/stems-names").json()["running"]:
            break
        time.sleep(0.02)
    assert asked == ["Broken - Song (Official Video)"]
    # and back
    assert client.delete("/api/stems-names").json()["restored"] == 2
    assert meta(a)["title"] == "Slayer- Raining Blood (lyrics)"


def test_an_artists_albums_say_what_there_already_is(client, tmp_music_root, monkeypatch):
    _stems(tmp_music_root, "Slayer - Raining Blood", {"artist": "Slayer", "title": "Raining Blood"})
    monkeypatch.setattr(server, "_all_songs_flat", lambda: {"entries": []})
    monkeypatch.setattr(server, "_pick_tabs", lambda: [])
    monkeypatch.setattr(server, "_discography", lambda name: {"artist": "Slayer", "thumbnail": None, "albums": [
        {"title": "Reign in Blood", "year": "1986", "browseId": "b1", "thumbnail": None, "tracks": [
            {"title": "Angel of Death", "videoId": "v1", "duration_seconds": 291, "artists": "Slayer"},
            {"title": "Raining Blood", "videoId": "v2", "duration_seconds": 257, "artists": "Slayer"}]}]})
    got = client.get("/api/artist/discography", params={"name": "slayer"}).json()
    angel, rain = got["albums"][0]["tracks"]
    assert (angel["have"]["stems"], rain["have"]["stems"], rain["have"]["name"]) == (False, True, "Slayer - Raining Blood")
    assert client.get("/api/artist/discography", params={"name": " "}).status_code == 400


def test_a_concert_recording_keeps_live_behind_its_title():
    assert names.tidy_live("Trapt", "Headstrong (Live)") == {"artist": "Trapt", "title": "Headstrong (Live)"}
    assert names.tidy_live("ToolArchive", "Tool - Sober Live (Pro Shot) Remastered")["title"] == "Sober (Live)"
    assert names.tidy_live("Metallica", "One (Official Video)")["title"] == "One"
    assert names.tidy_live("Metallica", "One", "Metallica - One (Live in Seattle)")["title"] == "One (Live)"
    assert names.tidy_live("Live", "I Alone", "Live - I Alone")["title"] == "I Alone"                    # the band Live is not a concert
    got = names.pick([{"title": "Enter Sandman", "artists": ["Metallica"], "album": "Metallica"}], "Metallica", "Enter Sandman (Live with the SFSO)")
    assert got["title"] == "Enter Sandman (Live)"


def test_a_tuning_written_in_note_names_becomes_strings():
    assert server._tuning_from_names("E A D G B E") == [64, 59, 55, 50, 45, 40]
    assert server._tuning_from_names("D A D G B E") == [64, 59, 55, 50, 45, 38]
    assert server._tuning_from_names("Eb Ab Db Gb Bb Eb") == [63, 58, 54, 49, 44, 39]
    assert server._tuning_from_names("C G C F A D") == [62, 57, 53, 48, 43, 36]
    assert server._tuning_from_names("B E A D G B E") == [64, 59, 55, 50, 45, 40, 35]
    assert server._tuning_from_names("Open G") == [] and server._tuning_from_names("") == []


def test_the_second_tab_site_is_asked_only_when_the_first_knows_nothing(tmp_music_root, monkeypatch):
    d = _stems(tmp_music_root, "Kabát - Šaman", {"artist": "Kabát", "title": "Šaman"})
    asked = []
    monkeypatch.setattr(server, "_match_song", lambda a, t: None)
    monkeypatch.setattr(server, "_ug_tuning", lambda a, t: asked.append(t) or [64, 59, 55, 50, 45, 40])
    server._tuning_lookup(d, "Kabát", "Šaman")
    assert server._stems_tuning(d) == [64, 59, 55, 50, 45, 40] and asked == ["Šaman"]
    server._tuning_lookup(d, "Kabát", "Šaman")
    assert asked == ["Šaman"]                                             # known: not asked again
    # a tab at the first place: the second is left in peace
    e = _stems(tmp_music_root, "Korn - Blind", {"artist": "Korn", "title": "Blind"})
    monkeypatch.setattr(server, "_match_song", lambda a, t: {"songId": 1, "tracks": [{"instrument": "Distortion Guitar", "tuning": [62, 57, 53, 48, 43, 38, 33]}]})
    server._tuning_lookup(e, "Korn", "Blind")
    assert server._stems_tuning(e) == [62, 57, 53, 48, 43, 38, 33] and asked == ["Šaman"]
