import dataclasses
import unittest

from collector.merge import (
    Reuse,
    build_item,
    join_info,
    join_trail,
    labels,
    mountain_id,
    override_warnings,
    poi_warnings,
    stamp_points,
)
from collector.overrides import Override, StampPoint, load_overrides
from collector.records import parse_list_mountain, parse_mountain_info, parse_poi, parse_trail_info
from collector.tests.support import REAL_OVERRIDES, real_info, real_list, real_pois, real_trail

MOUNTAINS = [parse_list_mountain(row) for row in real_list()]
TRAIL = [parse_trail_info(row) for row in real_trail()]
INFO = [parse_mountain_info(row) for row in real_info()]
POIS = [parse_poi(row) for row in real_pois()]
OVERRIDES = load_overrides(REAL_OVERRIDES)


def mountain(source_name):
    return next(m for m in MOUNTAINS if m.source_name == source_name)


class JoinTest(unittest.TestCase):
    def test_every_real_mountain_joins_the_trail_list_by_name(self):
        joined = join_trail(MOUNTAINS, TRAIL)
        self.assertEqual(len(joined), 100)
        for m in MOUNTAINS:
            self.assertEqual(joined[mountain_id(m)].name, m.name, m.source_name)

    def test_base_name_join_and_duplicates(self):
        joined = join_trail(MOUNTAINS, TRAIL)
        self.assertEqual(joined[mountain_id(mountain("덕유산"))].source_name, "덕유산(향적봉)")
        self.assertEqual(joined[mountain_id(mountain("화악산"))].source_name, "화악산(중봉)")
        self.assertEqual(joined[mountain_id(mountain("백운산(정선)"))].source_name, "백운산(정선)")
        self.assertEqual(joined[mountain_id(mountain("지리산(통영)"))].code, "482202301")

    def test_wrong_list_codes_do_not_matter(self):
        # 15125127 gives 유명산 the code of 월출산 (468102301); the name join is right.
        joined = join_trail(MOUNTAINS, TRAIL)
        self.assertEqual(joined[mountain_id(mountain("유명산"))].code, "418303301")

    def test_no_base_join_when_the_base_name_repeats(self):
        trail = [t for t in TRAIL if t.source_name != "백운산(포천)"] + [dataclasses.replace(TRAIL[0], source_name="백운산(X)", name="백운산")]
        joined = join_trail(MOUNTAINS, trail)
        self.assertNotIn(mountain_id(mountain("백운산(포천)")), joined)

    def test_info_join_by_code_with_name_check(self):
        trail = join_trail(MOUNTAINS, TRAIL)
        joined = join_info(MOUNTAINS, trail, INFO)
        self.assertGreaterEqual(len(joined), 15)  # the fixture is page 1 of 5 (names 가~대)
        for mid, info in joined.items():
            m = next(x for x in MOUNTAINS if mountain_id(x) == mid)
            self.assertEqual(info.name.split("(")[0], m.name)
        self.assertEqual(joined[mountain_id(mountain("가리산"))].code, "421100101")

    def test_info_join_refuses_a_code_with_another_name(self):
        trail = join_trail(MOUNTAINS, TRAIL)
        renamed = [dataclasses.replace(i, name="다른산") if i.code == "421100101" else i for i in INFO]
        self.assertNotIn(mountain_id(mountain("가리산")), join_info(MOUNTAINS, trail, renamed))

    def test_info_join_falls_back_to_a_unique_list_code(self):
        target = mountain("가리산")
        self.assertIn(mountain_id(target), join_info(MOUNTAINS, {}, INFO))
        duplicated = MOUNTAINS + [dataclasses.replace(target, frtrl_id="X1")]
        self.assertNotIn(mountain_id(target), join_info(duplicated, {}, INFO))


class LabelTest(unittest.TestCase):
    def test_real_labels(self):
        names = labels(MOUNTAINS)
        self.assertEqual(names[mountain_id(mountain("백운산(포천)"))], "백운산 (포천)")
        self.assertEqual(names[mountain_id(mountain("지리산(통영)"))], "지리산 (통영)")
        self.assertEqual(names[mountain_id(mountain("지리산"))], "지리산")
        self.assertEqual(names[mountain_id(mountain("가리산"))], "가리산")
        self.assertEqual(len(set(names.values())), 100)

    def test_same_name_without_brackets_gets_the_first_region(self):
        a = mountain("금산")
        b = dataclasses.replace(a, frtrl_id="X2", address="경상북도 김천시 감문면")
        names = labels([a, b])
        self.assertEqual(names[mountain_id(a)], "금산 (남해)")
        self.assertEqual(names[mountain_id(b)], "금산 (김천)")


class StampTest(unittest.TestCase):
    def test_list_point_by_default(self):
        m = mountain("가리산")
        points, source = stamp_points(m, None)
        self.assertEqual((points, source), ((StampPoint(m.lat, m.lng, "정상"),), "list"))

    def test_override_and_keep_source(self):
        m = mountain("백운산(포천)")
        points, source = stamp_points(m, OVERRIDES["m-0000000043"])
        self.assertEqual((source, points[0].name), ("override", "백운산 정상"))
        self.assertEqual(stamp_points(mountain("방장산"), OVERRIDES["m-0000000039"])[1], "list")


class OverrideWarningTest(unittest.TestCase):
    def test_real_file_has_no_warnings(self):
        self.assertEqual(override_warnings(MOUNTAINS, OVERRIDES), [])

    def test_far_move_without_flag_unknown_id_and_other_name(self):
        far = dataclasses.replace(OVERRIDES["m-0000000043"], far_from_source=False)
        named = dataclasses.replace(OVERRIDES["m-0000000016"], name="다른산")
        warnings = override_warnings(MOUNTAINS, {"m-0000000043": far, "m-0000000016": named, "m-9999999999": named})
        self.assertEqual(len(warnings), 3)
        self.assertIn("name differs", warnings[0])
        self.assertIn("7481 m from the source without farFromSource", warnings[1])
        self.assertIn("no such mountain", warnings[2])

    def test_short_move_needs_no_flag(self):
        near = dataclasses.replace(OVERRIDES["m-0000000054"], far_from_source=False)
        self.assertEqual(override_warnings(MOUNTAINS, {"m-0000000054": near}), [])


class PoiCheckTest(unittest.TestCase):
    def test_real_data_with_real_overrides(self):
        warnings, no_poi = poi_warnings(MOUNTAINS, OVERRIDES, POIS)
        self.assertEqual(no_poi, 4)
        self.assertEqual([w.split("(")[1].split(")")[0] for w in warnings], ["변산", "천성산"])

    def test_without_overrides_the_known_errors_show(self):
        warnings, _ = poi_warnings(MOUNTAINS, {}, POIS)
        named = {w.split("(")[1].split(")")[0] for w in warnings}
        for name in ("백운산", "황석산", "삼악산", "소요산", "소백산", "마니산", "대둔산", "방장산", "적상산"):
            self.assertIn(name, named)


class BuildItemTest(unittest.TestCase):
    def item(self, source_name, previous=None, reuse=Reuse(), trail=True, info=True):
        m = mountain(source_name)
        mid = mountain_id(m)
        trail_by_id = join_trail(MOUNTAINS, TRAIL)
        info_by_id = join_info(MOUNTAINS, trail_by_id, INFO)
        return build_item(
            m,
            labels(MOUNTAINS)[mid],
            OVERRIDES.get(mid),
            trail_by_id.get(mid) if trail else None,
            info_by_id.get(mid) if info else None,
            previous,
            reuse,
        )

    def test_contract_fields(self):
        item = self.item("백운산(포천)")
        self.assertEqual(
            list(item),
            ["id", "set", "name", "label", "sido", "address", "heightM", "stamp", "stampSource", "subtitle", "reason",
             "summary", "details", "transport", "tourism", "courses", "updated"],
        )
        self.assertEqual((item["id"], item["set"], item["name"], item["label"]), ("m-0000000043", "top100", "백운산", "백운산 (포천)"))
        self.assertEqual((item["sido"], item["heightM"], item["stampSource"]), ("경기", 904.0, "override"))
        self.assertEqual(item["stamp"], [{"lat": 38.07488, "lng": 127.44456, "name": "백운산 정상"}])
        self.assertTrue(item["reason"].startswith("수려한 계곡미를"), item["reason"][:20])
        self.assertIsNone(item["updated"])  # 416501801 is not on the 산정보 fixture page
        self.assertEqual(self.item("가리산")["updated"], "2020-12-01")

    def test_list_point_is_rounded_to_five_decimals(self):
        item = self.item("민주지산")
        self.assertEqual(item["stamp"], [{"lat": 36.03977, "lng": 127.84933, "name": "정상"}])
        self.assertEqual(item["stampSource"], "list")

    def test_missing_sources_and_reuse(self):
        bare = self.item("가리산", trail=False, info=False)
        self.assertEqual((bare["reason"], bare["summary"], bare["updated"]), ("", "", None))
        previous = {"reason": "이전 이유", "summary": "이전 개관", "updated": "2020-12-01", "courses": "이전 코스"}
        kept = self.item("가리산", previous=previous, reuse=Reuse(text=True, updated=True), trail=False, info=False)
        self.assertEqual((kept["reason"], kept["courses"], kept["updated"]), ("이전 이유", "이전 코스", "2020-12-01"))
        fresh = self.item("가리산", previous=previous, reuse=Reuse(text=True), trail=True, info=False)
        self.assertTrue(fresh["reason"].startswith("강원도에서"))

    def test_info_fills_text_when_trail_text_is_missing(self):
        m = mountain("가리산")
        trail_by_id = join_trail(MOUNTAINS, TRAIL)
        empty = dataclasses.replace(trail_by_id[mountain_id(m)], reason="", summary="", details="")
        info = join_info(MOUNTAINS, trail_by_id, INFO)[mountain_id(m)]
        item = build_item(m, "가리산", None, empty, info, None)
        self.assertEqual(item["reason"], info.reason)
        self.assertTrue(item["reason"])


class OverrideDataclassTest(unittest.TestCase):
    def test_keep_source_has_no_points(self):
        self.assertEqual(Override("m-1", None, (), False, True).stamp, ())


if __name__ == "__main__":
    unittest.main()
