import unittest

from collector.records import (
    RecordInvalid,
    clean_rich,
    parse_list_mountain,
    parse_mountain_info,
    parse_poi,
    parse_trail_info,
    split_name,
)
from collector.tests.support import real_info, real_list, real_pois, real_trail


def by_name(rows, field, value):
    return next(row for row in rows if row[field] == value)


class ListMountainTest(unittest.TestCase):
    def test_every_real_row_is_valid(self):
        mountains = [parse_list_mountain(row) for row in real_list()]
        self.assertEqual(len({m.frtrl_id for m in mountains}), 100)
        self.assertEqual({m.sido for m in mountains} - {"서울", "부산", "인천", "울산", "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"}, set())

    def test_real_qualified_name(self):
        mountain = parse_list_mountain(by_name(real_list(), "frtrlNm", "백운산(포천)"))
        self.assertEqual((mountain.name, mountain.qualifier, mountain.source_name), ("백운산", "포천", "백운산(포천)"))
        self.assertEqual((mountain.frtrl_id, mountain.code, mountain.sido), ("0000000043", "416501801", "경기"))
        self.assertEqual((mountain.lat, mountain.lng, mountain.height_m), (38.130579, 127.492505, 904.0))
        self.assertEqual(mountain.extracted, "2026-11-08 06:01:50")

    def test_real_plain_name_and_new_sido_name(self):
        mountain = parse_list_mountain(real_list()[0])
        self.assertEqual((mountain.name, mountain.qualifier, mountain.sido), ("가리산", None, "강원"))
        self.assertTrue(mountain.address.startswith("강원도 춘천시"))

    def test_invalid_rows(self):
        good = real_list()[0]
        cases = {
            "bad frtrlId": {"frtrlId": "../x"},
            "empty frtrlNm": {"frtrlNm": " "},
            "bad lat": {"lat": "abc"},
            "outside": {"lat": 127.95, "lot": 37.87},
            "nan": {"lot": float("nan")},
            "bool": {"aslAltide": True},
            "height": {"aslAltide": 0},
            "too high": {"aslAltide": 2950},
            "sido": {"ctpvNm": None},
        }
        for label, change in cases.items():
            with self.subTest(label), self.assertRaises(RecordInvalid):
                parse_list_mountain({**good, **change})
        with self.assertRaises(RecordInvalid):
            parse_list_mountain([good])

    def test_bad_code_becomes_none_and_unknown_sido_kept(self):
        mountain = parse_list_mountain({**real_list()[0], "mtnCd": "12", "ctpvNm": "어느도"})
        self.assertEqual((mountain.code, mountain.sido), (None, "어느도"))


class SplitNameTest(unittest.TestCase):
    def test_split(self):
        self.assertEqual(split_name("지리산(통영)"), ("지리산", "통영"))
        self.assertEqual(split_name("덕유산 (향적봉)"), ("덕유산", "향적봉"))
        self.assertEqual(split_name("가리산"), ("가리산", None))
        self.assertEqual(split_name("(포천)"), ("(포천)", None))


class PoiTest(unittest.TestCase):
    def test_every_real_poi_is_valid(self):
        pois = [parse_poi(row) for row in real_pois()]
        self.assertEqual(len(pois), 960)
        self.assertEqual(len({p.frtrl_id for p in pois}), 96)

    def test_invalid_poi(self):
        with self.assertRaises(RecordInvalid):
            parse_poi({**real_pois()[0], "lat": None})


class TrailInfoTest(unittest.TestCase):
    def test_every_real_row_parses(self):
        rows = [parse_trail_info(row) for row in real_trail()]
        self.assertEqual(len(rows), 100)
        self.assertEqual(sum(1 for r in rows if r.code is None), 1)  # 방장산 has no mntncd

    def test_real_gariisan_text_is_clean(self):
        info = parse_trail_info(by_name(real_trail(), "mntnm", "가리산"))
        self.assertEqual((info.code, info.name, info.subtitle), ("421100101", "가리산", "소양호에서 폭포 따라 정상까지"))
        self.assertTrue(info.summary.startswith("<소양강 비경 담은 신비한 암봉>\n가리산은 강원도 홍천군"), info.summary[:60])
        self.assertIn("\n②", info.courses)
        self.assertTrue(info.reason.startswith("강원도에서 진달래가 가장 많이 피는 산"))

    def test_all_real_text_fields_are_clean(self):
        for row in real_trail():
            info = parse_trail_info(row)
            for name in ("subtitle", "reason", "summary", "details", "transport", "tourism", "courses"):
                text = getattr(info, name)
                self.assertNotRegex(text, r"<\s*/?\s*[A-Za-z]", (info.name, name))
                self.assertNotRegex(text, r"&(lt|gt|amp|nbsp|quot);", (info.name, name))
                self.assertEqual(text, text.strip())

    def test_qualified_trail_names(self):
        info = parse_trail_info(by_name(real_trail(), "mntnm", "덕유산(향적봉)"))
        self.assertEqual((info.name, info.source_name), ("덕유산", "덕유산(향적봉)"))

    def test_name_is_required(self):
        with self.assertRaises(RecordInvalid):
            parse_trail_info({**real_trail()[0], "mntnm": " "})


class MountainInfoTest(unittest.TestCase):
    def test_real_rows(self):
        rows = [parse_mountain_info(row) for row in real_info()]
        self.assertEqual(len(rows), 1000)
        self.assertEqual({r.updated for r in rows}, {"2020-12-01"})

    def test_placeholder_and_blank_fields(self):
        info = parse_mountain_info({**real_info()[0], "mntidetails": "( - )", "mntitop": " ", "mntinfdt": "2020"})
        self.assertEqual((info.details, info.reason, info.updated), ("", "", None))

    def test_bad_code(self):
        with self.assertRaises(RecordInvalid):
            parse_mountain_info({**real_info()[0], "mntilistno": "x"})

    def test_double_escaped_html(self):
        self.assertEqual(clean_rich("&lt;p&gt;가&lt;br /&gt;나&lt;/p&gt;"), "가\n나")
        self.assertEqual(clean_rich(None), "")


if __name__ == "__main__":
    unittest.main()
