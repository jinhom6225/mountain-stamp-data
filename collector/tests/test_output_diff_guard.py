import json
import tempfile
import unittest
from pathlib import Path

from collector.diff import compare
from collector.errors import RunAborted
from collector.geo import distance_m
from collector.guard import check_drop, check_invalid
from collector.output import document, dumps, load_previous, write

ITEM = {"id": "m-1", "label": "가리산", "name": "가리산", "heightM": 1051.0, "stamp": [{"lat": 37.87135, "lng": 127.95649, "name": "정상"}], "reason": "a"}


class GeoTest(unittest.TestCase):
    def test_known_distance(self):
        self.assertAlmostEqual(distance_m(37.0, 127.0, 37.001, 127.0), 111.2, delta=0.2)
        self.assertEqual(distance_m(37.0, 127.0, 37.0, 127.0), 0.0)


class OutputTest(unittest.TestCase):
    def test_document_and_round_trip(self):
        data = document("2026-11-09T19:10:00Z", [{"id": "ktc", "count": 1}], [ITEM])
        self.assertEqual(list(data), ["schemaVersion", "generatedAt", "sources", "items"])
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            size = write(repo, data)
            text = (repo / "data" / "mountains.json").read_text(encoding="utf-8")
            self.assertEqual(size, len(text.encode("utf-8")))
            self.assertIn("가리산", text)  # not \\u escaped
            self.assertTrue(text.endswith("\n"))
            previous = load_previous(repo)
        self.assertEqual((previous.items, previous.sources["ktc"]["count"], previous.warning), ({"m-1": ITEM}, 1, None))
        self.assertEqual(json.loads(dumps(data)), data)

    def test_missing_and_broken_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.assertEqual(load_previous(repo).items, {})
            (repo / "data").mkdir()
            for text in ("{", "[]", '{"items": 3}', '{"items": [{"id": 1}]}', '{"items": [], "sources": [1]}'):
                (repo / "data" / "mountains.json").write_text(text, encoding="utf-8")
                previous = load_previous(repo)
                self.assertEqual(previous.items, {})
                self.assertIn("unreadable", previous.warning)


class DiffTest(unittest.TestCase):
    def test_kinds(self):
        moved = dict(ITEM, id="m-2", stamp=[{"lat": 37.87200, "lng": 127.95649, "name": "정상"}])
        nudged = dict(ITEM, id="m-3", stamp=[{"lat": 37.87150, "lng": 127.95649, "name": "정상"}])
        height = dict(ITEM, id="m-4", heightM=1050.0)
        info = dict(ITEM, id="m-5", reason="b")
        points = dict(ITEM, id="m-6", stamp=ITEM["stamp"] * 2)
        previous = {i: dict(ITEM, id=i) for i in ("m-2", "m-3", "m-4", "m-5", "m-6", "m-9")}
        diff = compare(previous, [ITEM, moved, nudged, height, info, points])
        self.assertEqual([c.id for c in diff.new], ["m-1"])
        self.assertEqual([(c.id, c.detail) for c in diff.moved], [("m-2", "72 m"), ("m-6", "points 1 -> 2")])
        self.assertEqual([c.id for c in diff.height], ["m-4"])
        self.assertEqual([c.id for c in diff.updated], ["m-3", "m-5"])  # 17 m: below the 30 m report line
        self.assertEqual([c.id for c in diff.removed], ["m-9"])
        message = diff.commit_message(5, 1)
        self.assertTrue(message.startswith("chore(data): update mountains (+1 new, ~2 moved, ~1 height, ~2 info, -1 removed)\n\n"))
        self.assertIn("moved: 가리산 (m-2) 72 m", message)
        self.assertTrue(message.endswith("mountains: 5, stamp overrides: 1\n"))
        self.assertEqual(diff.counts_text(), "새 1 · 지점 이동 2 · 높이 바뀜 1 · 정보 바뀜 2 · 사라짐 1")

    def test_no_changes_and_long_lists(self):
        diff = compare({"m-1": ITEM}, [ITEM])
        self.assertFalse(diff.has_changes)
        self.assertTrue(diff.commit_message(1, 0).startswith("chore(data): refresh mountain list (no mountain changes)"))
        many = compare({}, [dict(ITEM, id=f"m-{i}") for i in range(12)])
        self.assertIn("new: ... and 2 more", many.commit_message(12, 0))

    def test_missing_stamp_counts_as_moved(self):
        diff = compare({"m-1": dict(ITEM, stamp=[])}, [ITEM])
        self.assertEqual([c.id for c in diff.moved], ["m-1"])


class GuardTest(unittest.TestCase):
    def test_invalid_ratio(self):
        check_invalid(100, 20)
        for raw, invalid in ((100, 21), (0, 0)):
            with self.assertRaises(RunAborted):
                check_invalid(raw, invalid)

    def test_drop_ratio(self):
        check_drop(0, 0)
        check_drop(100, 80)
        with self.assertRaises(RunAborted):
            check_drop(100, 79)


if __name__ == "__main__":
    unittest.main()
