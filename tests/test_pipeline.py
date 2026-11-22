"""End-to-end tests for pipeline/build_feed.py with fake versions of every API.

Run: python -m unittest discover -s tests
"""

import gzip
import json
import re
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pipeline"))
import build_feed as bf  # noqa: E402

TODAY = "2026-11-06"

# A small fake universe: key -> facts
# services use TMDB provider ids: 8 Netflix, 1796 Netflix w/ ads, 15 Hulu, 1899 Max, 582 Paramount+ Amazon Channel
U = {
    "m1": dict(title="Great Movie", runtime=135, prov=[8], imdb=("tt001", 8.6, 900000), rt=95, mc=88, release="2024-03-01", genres=["Drama"],
               keywords=["based on true story", "boxing", "underdog"]),  # sport + underdog -> Sports
    "m2": dict(title="Tiny Indie", prov=[15], imdb=("tt002", 9.6, 120), rt=100, mc=None, release="2025-01-10", genres=["Drama"]),
    "m3": dict(title="Fresh Release", prov=[1796], imdb=("tt003", 7.1, 40000), rt=70, mc=65, release="2026-10-30", genres=["Comedy"]),
    "m4": dict(title="Channel Only", prov=[582], imdb=("tt004", 8.0, 50000), rt=90, mc=80, release="2023-05-05", genres=["Action"]),
    "m7": dict(title="Starz Via Amazon", prov=[1794], imdb=("tt007", 7.2, 30000), rt=80, mc=70, release="2024-10-25", genres=["Thriller"]),
    "m6": dict(title="Free Classic", prov=[], ads=[73], imdb=("tt006", 7.6, 80000), rt=91, mc=80, release="1995-05-05", genres=["Western"]),
    "m5": dict(title="Theater Hit", prov=[], imdb=("tt005", 7.9, 200000), rt=88, mc=75, release="2026-10-01", genres=["Action"]),
    "t1": dict(title="Big Show", prov=[8, 1899], imdb=("tt101", 8.9, 500000), rt=None, mc=None, mdb_rt=91, mdb_mc=80, release="2020-01-01",
               seasons=["2020-01-01", "2026-10-20", "2027-03-01"], genres=["Sci-Fi & Fantasy"]),
    "t2": dict(title="Cozy Show", prov=[15], imdb=("tt102", 7.4, 15000), rt=82, mc=70, release="2022-02-02",
               seasons=["2022-02-02"], genres=["Comedy"], keywords=["sitcom", "passport", "transport"]),
    "t3": dict(title="Wednesdayish", prov=[8], imdb=("tt103", 8.1, 300000), rt=None, mc=72, mdb_rt=85, mdb_mc=60, release="2022-11-23",
               seasons=["2022-11-23"], genres=["Comedy"]),
}
WATCHERS = {"t1": 1500, "m1": 40, "m5": 300, "t2": 5}
TMDB_TRENDING = {"movie": [3, 1], "tv": [2, 1]}  # TMDB daily trending, in order


class FakeHttp:
    def __init__(self):
        self.calls = []
        self.omdb_calls = 0

    def get(self, url, headers=None, timeout=60):
        self.calls.append(url)
        u = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(u.query))
        host, path = u.netloc, u.path

        if host == "datasets.imdbws.com":
            lines = ["tconst\taverageRating\tnumVotes"] + [f"{f['imdb'][0]}\t{f['imdb'][1]}\t{f['imdb'][2]}" for f in U.values()]
            return gzip.compress(("\n".join(lines) + "\n").encode())
        if host == "www.netflix.com":
            rows = ["country_name\tcountry_iso2\tweek\tcategory\tweekly_rank\tshow_title\tseason_title\tcumulative_weeks_in_top_10",
                    "United States\tUS\t2026-11-01\tTV\t1\tWednesdayish\tWednesdayish: Season 2\t3",
                    "United States\tUS\t2026-11-01\tFilms\t1\tGreat Movie\t\t2",
                    "United States\tUS\t2026-11-01\tTV\t4\tWednesdayish\tWednesdayish: Season 1\t9",
                    "United States\tUS\t2026-10-25\tTV\t1\tOld Week Show\t\t1",
                    "Canada\tCA\t2026-11-01\tTV\t1\tSomething Else\t\t1"]
            return "\n".join(rows).encode()
        return json.dumps(self.json(host, path, q)).encode()

    def get_json(self, url, headers=None):
        return json.loads(self.get(url, headers))

    def json(self, host, path, q):
        if host == "api.trakt.tv":
            kind = "movie" if "movies" in path else "show"
            pre = "m" if kind == "movie" else "t"
            return [{"watchers": w, kind: {"title": U[k]["title"], "ids": {"tmdb": int(k[1:])}}}
                    for k, w in sorted(WATCHERS.items(), key=lambda x: -x[1]) if k.startswith(pre)]
        if host == "www.wikidata.org":
            self.wd_calls = getattr(self, "wd_calls", 0) + 1
            items = {  # Q-id: (IMDb id, Rotten Tomatoes ids, Metacritic ids); several addresses, a season page, junk
                "Q1": ("tt001", ["m/great_movie_2024", "m/great_movie"], ["movie/great-movie"]),
                "Q101": ("tt101", ["tv/big_show/s02"], ["tv/big-show"]),
                "Q102": ("tt102", ["https://example.com/x"], []),
            }
            if q["action"] == "query":
                asked = set(re.findall(r"P345=(tt\d+)", q["srsearch"]))
                return {"query": {"search": [{"title": k} for k, v in items.items() if v[0] in asked]}}
            claim = lambda vals: [{"mainsnak": {"datavalue": {"value": v}}} for v in vals]
            return {"entities": {k: {"claims": {"P345": claim([items[k][0]]), "P1258": claim(items[k][1]), "P1712": claim(items[k][2])}}
                                 for k in q["ids"].split("|")}}
        if host == "api.mdblist.com":
            self.mdb_calls = getattr(self, "mdb_calls", 0) + 1
            imdb_id = path.rsplit("/", 1)[-1]
            f = next(f for f in U.values() if f["imdb"][0] == imdb_id)
            return {"ratings": [{"source": "tomatoes", "value": f.get("mdb_rt")}, {"source": "metacritic", "value": f.get("mdb_mc")},
                                {"source": "imdb", "value": f["imdb"][1]}]}
        if host == "www.omdbapi.com":
            self.omdb_calls += 1
            f = next(f for f in U.values() if f["imdb"][0] == q["i"])
            ratings = [{"Source": "Rotten Tomatoes", "Value": f"{f['rt']}%"}] if f["rt"] is not None else []
            return {"Response": "True", "Ratings": ratings, "Metascore": str(f["mc"]) if f["mc"] is not None else "N/A",
                    "imdbRating": str(f["imdb"][1]), "imdbVotes": f"{f['imdb'][2]:,}"}
        assert host == "api.themoviedb.org", host
        p = path.removeprefix("/3")
        if p.startswith("/watch/providers/"):
            return {"results": [
                {"provider_id": 8, "provider_name": "Netflix"},
                {"provider_id": 1796, "provider_name": "Netflix Standard with Ads"},
                {"provider_id": 15, "provider_name": "Hulu"},
                {"provider_id": 1899, "provider_name": "HBO Max"},
                {"provider_id": 582, "provider_name": "Paramount+ Amazon Channel"},
                {"provider_id": 1794, "provider_name": "Starz Amazon Channel"},
            ]}
        if p.startswith("/trending/"):
            kind = p.split("/")[2]
            return {"results": [{"id": i} for i in TMDB_TRENDING[kind]], "total_pages": 1}
        if p.startswith("/discover/"):
            kind = p.split("/")[2]
            pre = "m" if kind == "movie" else "t"
            provs = {int(x) for x in q["with_watch_providers"].split("|")}
            free = "ads" in q.get("with_watch_monetization_types", "")
            ids = [int(k[1:]) for k, f in U.items() if k.startswith(pre) and not f.get("unlisted")
                   and provs & set(f.get("ads", []) if free else f["prov"])]
            return {"results": [{"id": i} for i in ids], "total_pages": 1}
        if p.startswith("/search/"):
            kind = p.split("/")[2]
            pre = "m" if kind == "movie" else "t"
            return {"results": [{"id": int(k[1:])} for k, f in U.items() if k.startswith(pre) and f["title"] == q["query"]]}
        kind, tid = p.strip("/").split("/")
        k = ("m" if kind == "movie" else "t") + tid
        f = U[k]
        d = {
            "id": int(tid), "overview": f"About {f['title']}.", "poster_path": f"/{k}.jpg", "popularity": 10.0,
            "genres": [{"id": i, "name": g} for i, g in enumerate(f["genres"])],
            "external_ids": {"imdb_id": f["imdb"][0]},
            "watch/providers": {"results": {"US": {"link": f"https://tmdb.example/{k}", "flatrate": [{"provider_id": x} for x in f["prov"]],
                                                                     "ads": [{"provider_id": x} for x in f.get("ads", [])]}}},
            "recommendations": {"results": [{"id": 1}]},
            "similar": {"results": [{"id": 3}, {"id": 1}, {"id": 999}]},
        }
        if k == "m1":  # a teaser, a fan video and two official trailers: the newest official trailer wins
            d["videos"] = {"results": [
                {"site": "YouTube", "type": "Teaser", "official": True, "key": "teaser1", "iso_639_1": "en", "published_at": "2024-01-01"},
                {"site": "YouTube", "type": "Trailer", "official": False, "key": "fan1", "iso_639_1": "en", "published_at": "2024-03-01"},
                {"site": "YouTube", "type": "Trailer", "official": True, "key": "old1", "iso_639_1": "en", "published_at": "2024-01-15"},
                {"site": "YouTube", "type": "Trailer", "official": True, "key": "main1", "iso_639_1": "en", "published_at": "2024-02-01"},
                {"site": "Vimeo", "type": "Trailer", "official": True, "key": "vimeo1", "iso_639_1": "en", "published_at": "2024-02-02"}]}
        people = [{"name": "Ann Lead", "order": 1, "profile_path": "/ann.jpg", "char": "Jo (voice)"},
                  {"name": "Bo Host", "order": 0, "profile_path": None, "char": "Self"},
                  {"name": "Cy Mom", "order": 2, "profile_path": "/cy.jpg", "char": "Self - Mother of Jo"},
                  {"name": "Di Old", "order": 3, "profile_path": "/di.jpg" if k != "t2" else None, "char": "Self (archive footage)"}]
        if k == "t2":  # Cozy Show: only 2 of its cast have photos -> no Cast section
            people = people[:3]
        if kind == "movie":
            d["credits"] = {"cast": [{**{k: v for k, v in p.items() if k != "char"}, "character": p["char"]} for p in people]}
        else:
            d["aggregate_credits"] = {"cast": [{**{k: v for k, v in p.items() if k != "char"}, "roles": [{"character": p["char"]}]} for p in people]}
        kws = [{"name": k} for k in f.get("keywords", [])]
        d["keywords"] = {"keywords": kws} if kind == "movie" else {"results": kws}
        if kind == "movie":
            d.update(title=f["title"], release_date=f["release"], runtime=f.get("runtime"))
        else:
            d.update(name=f["title"], first_air_date=f["release"],
                     seasons=[{"season_number": 0, "air_date": "2019-01-01"}] + [{"season_number": i + 1, "air_date": s} for i, s in enumerate(f["seasons"])])
        return d


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(REPO / "config.json", self.tmp / "config.json")
        bf.ROOT, bf.DATA, bf.CACHE = self.tmp, self.tmp / "data", self.tmp / "data" / "cache"
        self.fake = FakeHttp()
        bf.http = self.fake
        os.environ.update(TMDB_API_KEY="k", TRAKT_CLIENT_ID="c", OMDB_API_KEY="o", MDBLIST_API_KEY="m",
                          FEED_TODAY=TODAY, FEED_NOW=f"{TODAY}T12:00:00Z")

    def tearDown(self):
        shutil.rmtree(self.tmp)
        for k in ("FEED_TODAY", "FEED_NOW"):
            os.environ.pop(k, None)

    def test_full_refresh(self):
        feed = bf.run("auto")  # no previous feed -> full
        T, rows = feed["titles"], feed["rows"]

        # service resolution: ad tier matched by name, Amazon Channel variant excluded
        self.assertIn(1796, feed["provider_ids"]["netflix"])
        self.assertNotIn(582, feed["provider_ids"]["paramount"])
        self.assertEqual(T["m3"]["services"], ["netflix"])
        self.assertEqual(T["m1"]["trailer"], "main1")  # newest official YouTube trailer
        # top-billed cast first; "Self" roles blank, "(voice)" dropped; shows use the whole series' cast
        # cast lives in its own small files (data/cast/NN.json by TMDB id), loaded when a title opens
        cast01 = json.loads((self.tmp / "data" / "cast" / "01.json").read_text())
        # roles tidied ("(voice)", "Self - …", "Self (archive footage)"); people without a photo are kept
        want = [["Bo Host", "", None], ["Ann Lead", "Jo", "/ann.jpg"], ["Cy Mom", "Mother of Jo", "/cy.jpg"], ["Di Old", "", "/di.jpg"]]
        self.assertEqual(cast01["m1"], want)
        self.assertEqual(cast01["t1"], want)
        # fewer than 3 people with a photo: no Cast section, and the app is told so
        cast02 = json.loads((self.tmp / "data" / "cast" / "02.json").read_text())
        self.assertNotIn("t2", cast02)
        self.assertEqual((T["m1"].get("has_cast"), T["t2"].get("has_cast")), (1, None))
        # ...instead a short line of names (in feed.json, so it shows at once)
        self.assertEqual(T["t2"]["cast_list"], [["Bo Host", ""], ["Ann Lead", "Jo"], ["Cy Mom", "Mother of Jo"]])
        self.assertNotIn("cast_list", T["m1"])
        self.assertEqual(len(list((self.tmp / "data" / "cast").glob("*.json"))), 100)
        for f in bf.NOT_FOR_PHONES:  # not in the file every phone downloads
            self.assertNotIn(f, T["m1"])
        self.assertIsNone(T["t2"]["trailer"])  # none listed: no button in the app
        # similar titles: recommendations first, then TMDB similar, no repeats, only titles in the app, never itself
        self.assertEqual(T["t2"]["more"], ["t1", "t3"])
        self.assertEqual(T["t1"]["more"], ["t3"])
        self.assertNotIn("m4", T)  # only on an Amazon Channel
        self.assertNotIn("m5", T)  # trending but not on any service
        self.assertEqual(T["t1"]["services"], ["hbomax", "netflix"])
        self.assertEqual(T["t1"]["genres"], ["Science Fiction", "Fantasy"])
        # Sports comes from keywords; "transport"/"passport" must not match "sport"
        self.assertEqual(T["m1"]["genres"], ["Drama", "Sports"])
        self.assertNotIn("Sports", T["t2"]["genres"])
        # free-with-ads services (Tubi): found through their ads/free listings
        self.assertEqual(T["m6"]["services"], ["tubi"])
        # Starz through Amazon counts as Starz (same library); other Amazon Channels don't
        self.assertEqual(T["m7"]["services"], ["starz"])
        self.assertIn(1794, feed["provider_ids"]["starz"])
        # extra most-rated lists: documentaries for movies and TV, reality for TV only
        disc = [urllib.parse.urlparse(u) for u in self.fake.calls if "/discover/" in u]
        genre_calls = {(u.path.split("/")[-1], dict(urllib.parse.parse_qsl(u.query)).get("with_genres")) for u in disc}
        self.assertIn(("movie", "99"), genre_calls)
        self.assertIn(("tv", "99"), genre_calls)
        self.assertIn(("tv", "10764"), genre_calls)
        self.assertNotIn(("movie", "10764"), genre_calls)
        # best-known recent releases: most-rated among titles from the last 3 years
        recent = {(u.path.split("/")[-1], k) for u in disc for k in dict(urllib.parse.parse_qsl(u.query))
                  if dict(urllib.parse.parse_qsl(u.query)).get("sort_by") == "vote_count.desc" and k in ("primary_release_date.gte", "first_air_date.gte")}
        self.assertEqual(recent, {("movie", "primary_release_date.gte"), ("tv", "first_air_date.gte")})
        bands = {u.path.split("/")[-1] for u in disc if "primary_release_date.lte" in u.query or "first_air_date.lte" in u.query}
        self.assertEqual(bands, {"movie", "tv"})  # the 3-5 years ago band
        # length: movie running time, and seasons aired so far (not specials, not the announced 2027 one)
        self.assertEqual(T["t1"]["latest_air"], "2026-10-20")
        self.assertEqual((T["m1"]["runtime"], T["m1"]["season_count"]), (135, None))
        self.assertEqual((T["t1"]["runtime"], T["t1"]["season_count"]), (None, 2))
        self.assertIsNone(T["m3"]["runtime"])
        # exact Rotten Tomatoes / Metacritic pages from Wikidata
        self.assertEqual((T["m1"]["rt_id"], T["m1"]["mc_id"]), ("m/great_movie", "movie/great-movie"))  # shortest of several
        self.assertEqual((T["t1"]["rt_id"], T["t1"]["mc_id"]), ("tv/big_show", "tv/big-show"))  # season page -> show page
        self.assertEqual((T["t2"]["rt_id"], T["t2"]["mc_id"]), (None, None))  # not a page id: ignored
        self.assertIsNone(T["t3"]["rt_id"])  # Wikidata has nothing

        # audience: the 120-vote 9.6 indie must not beat the 900k-vote 8.6 movie
        self.assertLess(rows["audience"].index("m1"), rows["audience"].index("m2"))
        self.assertGreater(T["m1"]["scores"]["audience"], 80)
        # trending blends TMDB trending (70%) with Trakt watchers (30%), each scaled per type:
        # t2: Trakt 24.5 (5 watchers),      TMDB #1 of 2 -> 100 => 77.3
        # m3: no Trakt watchers,            TMDB #1 of 2 -> 100 => 70
        # t1: Trakt 100 (most TV watchers), TMDB #2 of 2 -> 50  => 65
        # m1: Trakt 65.1 (40 of max 300),   TMDB #2 of 2 -> 50  => 54.5
        self.assertEqual(rows["trending"], ["t2", "m3", "t1", "m1"])
        self.assertEqual(T["t1"]["scores"]["buzz"], 65.0)
        self.assertAlmostEqual(T["t2"]["scores"]["buzz"], 77.35, delta=0.1)
        self.assertAlmostEqual(T["m1"]["scores"]["buzz"], 54.5, delta=0.1)
        self.assertEqual(T["m3"]["scores"]["buzz"], 70.0)
        self.assertEqual(T["t1"]["trend_parts"], {"trakt": 100.0, "tmdb": 50.0})
        self.assertEqual(T["t2"]["tmdb_trend_rank"], 1)
        # critics: averages RT and Metacritic; titles with neither are absent
        self.assertEqual(T["m1"]["scores"]["critic"], 91.5)
        # TV critic scores come from MDBList when OMDb has none; OMDb wins when it has one
        self.assertEqual((T["t1"]["rt"], T["t1"]["mc"]), (91, 80))
        self.assertEqual((T["t3"]["rt"], T["t3"]["mc"]), (85, 72))
        self.assertIn("t1", rows["critics"])
        # new: recent movie release and a new TV season, newest first
        self.assertEqual(rows["new"], ["m3", "t1"])
        self.assertEqual(T["t1"]["new_date"], "2026-10-20")
        self.assertGreater(T["m3"]["scores"]["fresh"], T["t1"]["scores"]["fresh"])
        # netflix top 10: latest US week only, mapped to TMDB, TV before film at equal rank
        self.assertEqual(feed["netflix_week"], "2026-11-01")
        self.assertEqual([r["id"] for r in rows["netflix"]], ["t3", "m1"])
        self.assertEqual(T["t3"]["netflix_rank"], 1)
        # every title has the fields the app needs
        for t in T.values():
            for f in ("id", "type", "title", "services", "genres", "recs", "scores", "watchers"):
                self.assertIn(f, t)
        self.assertTrue((self.tmp / "data" / "feed.json").exists())

    def test_trending_refresh_reuses_data(self):
        bf.run("full")
        cast_before = (self.tmp / "data" / "cast" / "01.json").read_text()
        calls_before, omdb_before = len(self.fake.calls), self.fake.omdb_calls
        WATCHERS["t2"] = 900
        try:
            os.environ["FEED_NOW"] = f"{TODAY}T14:00:00Z"
            feed = bf.run("auto")  # 2 hours later -> trending only
        finally:
            WATCHERS["t2"] = 5
        new_calls = self.fake.calls[calls_before:]
        self.assertTrue(all("trakt" in u or "/trending/" in u for u in new_calls), new_calls)
        self.assertEqual(self.fake.omdb_calls, omdb_before)  # critic scores are cached
        self.assertEqual(feed["titles"]["m1"]["rt_id"], "m/great_movie")  # Wikidata pages are cached too
        # t2 now has 900 watchers (Trakt ~93) and is #1 on TMDB -> stays first, well ahead
        self.assertEqual(feed["rows"]["trending"][0], "t2")
        self.assertGreater(feed["titles"]["t2"]["scores"]["buzz"], 95)
        self.assertEqual(feed["full_refreshed_at"], f"{TODAY}T12:00:00Z")
        self.assertEqual(feed["trending_updated_at"], f"{TODAY}T14:00:00Z")
        self.assertEqual((self.tmp / "data" / "cast" / "01.json").read_text(), cast_before)  # cast kept between morning rebuilds

    def test_newly_added_to_service(self):
        bf.run("full")
        U["t2"]["prov"] = [15, 8]  # Cozy Show (known title) lands on Netflix two days later
        try:
            os.environ.update(FEED_TODAY="2026-11-08", FEED_NOW="2026-11-08T12:00:00Z")
            feed = bf.run("auto")
        finally:
            U["t2"]["prov"] = [15]
        self.assertEqual(feed["titles"]["t2"]["new_date"], "2026-11-08")
        self.assertEqual(feed["rows"]["new"][0], "t2")

    def test_titles_found_on_a_newly_added_service_are_not_new(self):
        ref = bf.parse_date("2026-11-15")
        seen = {"m9": {"_first": "2026-11-06", "netflix": "2026-11-06", "tubi": "2026-11-13"}}
        old_movie = {"id": "m9", "type": "movie", "release": "1954-04-26", "services": ["netflix", "tubi"]}
        added = {"netflix": bf.parse_date("2026-11-06"), "tubi": bf.parse_date("2026-11-12")}
        self.assertIsNone(bf.new_date_for(old_movie, seen, "2026-11-06", 30, ref, added))  # Tubi was just added
        seen["m9"]["tubi"] = "2026-11-16"  # really arrives on Tubi after its first few days
        self.assertEqual(bf.new_date_for(old_movie, seen, "2026-11-06", 30, bf.parse_date("2026-11-17"), added), "2026-11-16")
        cfg = {"services": [{"key": "netflix"}, {"key": "tubi", "added": "2026-11-12"}, {"key": "max"}]}
        got = bf.services_added({"baseline": "2026-11-06", "seen": seen}, cfg)
        self.assertEqual((str(got["netflix"]), str(got["tubi"]), str(got["max"])), ("2026-11-06", "2026-11-12", TODAY))

    def test_service_alarms(self):
        cfg = {"refresh": {"service_alarm_overlap": [["hulu", "disney"]]},
               "services": [{"key": "hulu", "name": "Hulu"}, {"key": "disney", "name": "Disney+"}]}
        def titles(hulu_only, disney_only, both):
            t = {}
            for i in range(hulu_only): t[f"m{i}"] = {"services": ["hulu"]}
            for i in range(disney_only): t[f"t{i}"] = {"services": ["disney"]}
            for i in range(both): t[f"b{i}"] = {"services": ["hulu", "disney"]}
            return t
        state = {}
        self.assertEqual(bf.check_services(titles(600, 500, 200), state, cfg, "2026-11-18"), [])  # first run: baseline
        self.assertEqual(bf.check_services(titles(580, 510, 210), state, cfg, "2026-11-19"), [])  # normal wobble
        # Hulu titles move under Disney+: Hulu drops by over a third, and far more titles are on both
        alerts = bf.check_services(titles(100, 900, 420), state, cfg, "2026-11-20")
        self.assertEqual(len(alerts), 2, alerts)
        self.assertIn("Hulu has 520 titles, down from 800 (since 2026-11-20)", alerts[0])
        self.assertIn("420 titles are now on both Hulu and Disney+ (was 200, since 2026-11-20)", alerts[1])
        # not dealt with: both repeat every morning, even after a week
        for day in ("2026-11-21", "2026-11-25", "2026-11-30"):
            self.assertEqual(len(bf.check_services(titles(100, 900, 430), state, cfg, day)), 2, day)
        # marked as expected in config.json: they stop, and the new levels are normal from then on
        cfg["refresh"]["service_alarm_accept"] = {"hulu": "2026-12-01", "hulu+disney": "2026-12-01"}
        self.assertEqual(bf.check_services(titles(100, 900, 430), state, cfg, "2026-12-01"), [])
        self.assertEqual(bf.check_services(titles(100, 900, 440), state, cfg, "2026-12-02"), [])
        # a later, new drop alerts again
        self.assertEqual(len(bf.check_services(titles(10, 900, 200), state, cfg, "2026-12-03")), 1)

    def test_first_public_release_date(self):
        rd = lambda *rows: {"results": [{"iso_3166_1": c, "release_dates": [{"type": ty, "release_date": f"{d}T00:00:00.000Z"}]} for c, ty, d in rows]}
        # Hamilton: digital in 2020, back in US cinemas in 2025 (TMDB's main date)
        self.assertEqual(bf.first_release("2025-09-05", rd(("US", 4, "2020-07-03"), ("US", 3, "2025-09-05"))), "2020-07-03")
        # a festival premiere a year before the public release doesn't count
        self.assertEqual(bf.first_release("2026-03-01", rd(("US", 1, "2025-01-20"), ("US", 4, "2026-03-01"))), "2026-03-01")
        # future dates are ignored; no extra dates: the main date
        self.assertEqual(bf.first_release("2026-03-01", rd(("GB", 3, "2027-01-01"))), "2026-03-01")
        self.assertEqual(bf.first_release("2026-03-01", None), "2026-03-01")

    def test_long_description_ends_at_a_full_sentence(self):
        short = bf.shorten_overview
        self.assertEqual(short("A short one."), "A short one.")
        text = "Steve lives in Washington, D.C. and adjusts. " + "x" * 30 + " Mr. Smith helps. " + "Word " * 100
        self.assertEqual(short(text, 100), "Steve lives in Washington, D.C. and adjusts. " + "x" * 30 + " Mr. Smith helps.")
        self.assertEqual(short('He said "go". Then ' + "More " * 100, 30), 'He said "go".')
        self.assertEqual(short("no sentence end at all " * 30, 40), "no sentence end at all no sentence…")

    def test_title_off_every_list_is_remembered_for_a_year(self):
        bf.run("full")
        U["m2"]["unlisted"] = True  # Tiny Indie drops off every list but stays on Hulu
        try:
            def day(d):
                os.environ.update(FEED_TODAY=d, FEED_NOW=f"{d}T12:00:00Z")
                return bf.run("full")
            feed = day("2026-11-08")
            self.assertEqual(feed["titles"]["m2"]["services"], ["hulu"])  # still there, services rechecked
            U["m2"]["prov"] = []  # really leaves its service: gone from the data
            self.assertNotIn("m2", day("2026-11-09")["titles"])
            U["m2"]["prov"] = [15]  # back on Hulu: back in the data
            self.assertIn("m2", day("2026-12-01")["titles"])
            self.assertIn("m2", day("2027-11-06")["titles"])  # a year after it was last on a list
            self.assertNotIn("m2", day("2027-11-07")["titles"])  # after that it's dropped
            del U["m2"]["unlisted"]
            self.assertIn("m2", day("2027-11-08")["titles"])  # on a list again: tracked again
        finally:
            U["m2"].pop("unlisted", None)
            U["m2"]["prov"] = [15]

    def test_recent_release_missing_scores_rechecked_sooner(self):
        U["m3"].update(rt=None, mc=None)  # Fresh Release (Oct 30): no reviews yet on day one
        try:
            bf.run("full")
            def omdb_ids(since):
                return [dict(urllib.parse.parse_qsl(urllib.parse.urlparse(u).query)).get("i")
                        for u in self.fake.calls[since:] if "omdbapi" in u]
            for day, expect in (("2026-11-08", False), ("2026-11-10", True)):
                before = len(self.fake.calls)
                os.environ.update(FEED_TODAY=day, FEED_NOW=f"{day}T12:00:00Z")
                U["m3"].update(rt=72, mc=66)  # reviews are in
                feed = bf.run("full")
                ids = omdb_ids(before)
                self.assertEqual("tt003" in ids, expect, (day, ids))  # 2 days: not yet; 4 days: rechecked
                self.assertNotIn("tt002", ids)  # older title missing a score waits for the monthly recheck
            self.assertEqual((feed["titles"]["m3"]["rt"], feed["titles"]["m3"]["mc"]), (72, 66))
        finally:
            U["m3"].update(rt=70, mc=65)

    def test_full_refresh_every_morning(self):
        due = lambda last, now: bf.full_refresh_due(last, now, 10)
        self.assertTrue(due(None, "2026-11-09T03:00:00Z"))
        self.assertFalse(due("2026-11-09T01:02:00Z", "2026-11-09T08:17:00Z"))  # manual rebuild at night; morning not yet
        self.assertTrue(due("2026-11-09T01:02:00Z", "2026-11-09T10:17:00Z"))   # ...the morning one still runs
        self.assertFalse(due("2026-11-09T10:20:00Z", "2026-11-09T22:17:00Z"))  # done for today
        self.assertTrue(due("2026-11-08T10:20:00Z", "2026-11-09T12:17:00Z"))   # morning run missed: next run catches up

    def test_scoring_helpers(self):
        cfg = json.loads((REPO / "config.json").read_text())["ranking"]
        self.assertIsNone(bf.audience_score(None, None, "movie", 6.8, cfg))
        self.assertEqual(bf.critic_score(None, None), None)
        self.assertEqual(bf.trakt_score(0, 1000), 0.0)
        self.assertEqual(bf.trakt_score(1000, 1000), 100.0)
        self.assertEqual(bf.tmdb_trend_score(1, 100), 100.0)
        self.assertEqual(bf.tmdb_trend_score(None, 100), 0.0)
        self.assertEqual(bf.buzz_score(100, 0), 50.0)
        self.assertEqual(bf.buzz_score(100, 0, 0.7), 30.0)
        # a general sports keyword is enough on its own
        for yes in ["formula 1", "sports", "Sports Documentary", "professional wrestling", "college football", "nba"]:
            self.assertTrue(bf.is_sports([yes]), yes)
        # one specific sport needs company; "football (soccer)" splits into its parts
        self.assertTrue(bf.is_sports(["football (soccer)", "coach"]))
        self.assertTrue(bf.is_sports(["boxing", "boxer"]))
        self.assertTrue(bf.is_sports(["wrestling", "sports entertainment"]))
        # incidental tags don't count
        for no in [["swimming"], ["wrestling"], ["baseball bat"], ["sports car"], ["passport", "transport"],
                   ["coach", "team"], ["life coach"], ["swimming pool", "summer"], ["training", "cricket"]]:
            self.assertFalse(bf.is_sports(no), no)
        import datetime as dt
        self.assertEqual(bf.fresh_score("2026-11-06", 30, dt.date(2026, 11, 6)), 100.0)
        self.assertEqual(bf.fresh_score("2026-09-01", 30, dt.date(2026, 11, 6)), 0.0)



class ArrivalStamp(unittest.TestCase):
    cfg = {"services": [{"key": "netflix", "provider_ids": [8]}], "refresh": {"genre_lists": []}}

    def test_unchanged_lists_use_today(self):
        sig = bf.lists_signature(self.cfg)
        cache = {"baseline": "2026-11-06", "seen": {"m1": {}}, "lists_sig": sig}
        self.assertEqual(bf.arrival_stamp(cache, sig, "2026-11-14"), "2026-11-14")

    def test_changed_lists_use_baseline(self):
        cache = {"baseline": "2026-11-06", "seen": {"m1": {}}, "lists_sig": "old"}
        sig = bf.lists_signature(self.cfg)
        self.assertEqual(bf.arrival_stamp(cache, sig, "2026-11-14"), "2026-11-06")
        self.assertEqual(cache["lists_sig"], sig)
        self.assertEqual(bf.arrival_stamp(cache, sig, "2026-11-15"), "2026-11-15")

    def test_first_ever_run_uses_today(self):
        cache = {"baseline": "2026-11-14", "seen": {}}
        self.assertEqual(bf.arrival_stamp(cache, "x", "2026-11-14"), "2026-11-14")

    def test_signature_changes_with_lists(self):
        other = {"services": self.cfg["services"], "refresh": {"genre_lists": [{"genres": "99"}]}}
        self.assertNotEqual(bf.lists_signature(self.cfg), bf.lists_signature(other))


if __name__ == "__main__":
    unittest.main()