import json
import tempfile
import unittest
from pathlib import Path

from collector.errors import ConfigError
from collector.geo import distance_m
from collector.overrides import StampPoint, load_overrides, parse_overrides
from collector.records import parse_list_mountain, parse_poi
from collector.tests.support import REAL_OVERRIDES, real_list, real_pois


def doc(items):
    return json.dumps({"schemaVersion": 1, "items": items}, ensure_ascii=False)


POINT = {"lat": 37.5, "lng": 127.0, "name": "정상"}


class RealOverridesTest(unittest.TestCase):
    def test_real_file(self):
        overrides = load_overrides(REAL_OVERRIDES)
        self.assertEqual(len(overrides), 19)
        self.assertEqual(sum(1 for o in overrides.values() if o.stamp), 17)
        self.assertEqual({k for k, o in overrides.items() if o.keep_source}, {"m-0000000039", "m-0000000072"})
        self.assertEqual(len(overrides["m-0000000065"].stamp), 2)  # 운장대 + 삼장봉

    def test_real_points_come_from_the_cited_pois(self):
        raw = json.loads(REAL_OVERRIDES.read_text(encoding="utf-8"))["items"]
        pois = {p.poi_id: p for p in (parse_poi(row) for row in real_pois())}
        mountains = {f"m-{m.frtrl_id}": m for m in (parse_list_mountain(row) for row in real_list())}
        for entry_id, entry in raw.items():
            self.assertEqual(entry["name"], mountains[entry_id].source_name)
            if entry.get("keepSource"):
                continue
            cited = [pois[i] for i in entry["evidence"]["poiIds"]]
            for poi in cited:
                self.assertEqual(f"m-{poi.frtrl_id}", entry_id)
            for point in entry["stamp"]:
                nearest = min(distance_m(point["lat"], point["lng"], p.lat, p.lng) for p in cited)
                self.assertLess(nearest, 30, (entry_id, point["name"]))
            m = mountains[entry_id]
            moved = distance_m(m.lat, m.lng, entry["stamp"][0]["lat"], entry["stamp"][0]["lng"])
            self.assertGreater(moved, 200, entry_id)  # only clear errors are overridden
            self.assertEqual(entry["farFromSource"], moved > 2000, entry_id)


class ParseOverridesTest(unittest.TestCase):
    def test_valid_entries(self):
        result = parse_overrides(doc({"m-1": {"stamp": [POINT, {**POINT, "name": "대체"}], "farFromSource": True}, "m-2": {"keepSource": True}}))
        self.assertEqual(result["m-1"].stamp, (StampPoint(37.5, 127.0, "정상"), StampPoint(37.5, 127.0, "대체")))
        self.assertTrue(result["m-1"].far_from_source)
        self.assertTrue(result["m-2"].keep_source)

    def test_broken_files_stop_the_run(self):
        cases = {
            "not json": "{",
            "no items": json.dumps({"schemaVersion": 1}),
            "items list": json.dumps({"items": []}),
            "bad id": doc({"../x": {"stamp": [POINT]}}),
            "not object": doc({"m-1": []}),
            "no stamp": doc({"m-1": {}}),
            "empty stamp": doc({"m-1": {"stamp": []}}),
            "too many": doc({"m-1": {"stamp": [POINT] * 6}}),
            "outside": doc({"m-1": {"stamp": [{**POINT, "lat": 127.0, "lng": 37.5}]}}),
            "string lat": doc({"m-1": {"stamp": [{**POINT, "lat": "37.5"}]}}),
            "bool lat": doc({"m-1": {"stamp": [{**POINT, "lat": True}]}}),
            "no name": doc({"m-1": {"stamp": [{"lat": 37.5, "lng": 127.0}]}}),
            "point list": doc({"m-1": {"stamp": [[37.5, 127.0]]}}),
            "keep with stamp": doc({"m-1": {"keepSource": True, "stamp": [POINT]}}),
            "flag text": doc({"m-1": {"stamp": [POINT], "farFromSource": "yes"}}),
            "name number": doc({"m-1": {"stamp": [POINT], "name": 3}}),
        }
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(ConfigError):
                parse_overrides(text)

    def test_missing_file_means_none_and_unreadable_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_overrides(Path(tmp) / "none.json"), {})
            with self.assertRaises(ConfigError):
                load_overrides(Path(tmp))  # a directory cannot be read


if __name__ == "__main__":
    unittest.main()
