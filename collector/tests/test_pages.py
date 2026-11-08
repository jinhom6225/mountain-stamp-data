import unittest

from collector.config import MNT, POI, TOP100, TRAIL
from collector.errors import CallFailed
from collector.http_client import ApiClient, RawResponse
from collector.pages import decode_page, fetch_all
from collector.tests.support import FAKE_KEY, FakeClock, hand_bytes, ktc_page, real_bytes, real_list


class SequenceTransport:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.urls = []

    def get(self, url, *, operation, timeout, max_bytes):
        self.urls.append(url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def client(*outcomes):
    transport, clock = SequenceTransport(*outcomes), FakeClock()
    return ApiClient(transport, FAKE_KEY, clock.sleep), transport, clock


class RealPagesTest(unittest.TestCase):
    def test_top100_list(self):
        page = decode_page(TOP100, 200, real_bytes("ktc_top100_list.json"))
        self.assertEqual((len(page.items), page.total_count), (100, 100))
        self.assertEqual(page.items[0]["frtrlNm"], "가리산")

    def test_peak_poi(self):
        page = decode_page(POI, 200, real_bytes("ktc_peak_poi_peak_p1.json"))
        self.assertEqual((len(page.items), page.total_count), (960, 960))
        self.assertEqual({row["orgnPlaceTpeCd"] for row in page.items}, {"PEAK"})

    def test_trail_xml(self):
        page = decode_page(TRAIL, 200, real_bytes("kfs_trail_search_p1.xml"))
        self.assertEqual((len(page.items), page.total_count), (100, 100))
        self.assertEqual((page.items[0]["mntnm"], page.items[0]["mntncd"]), ("가리산", "421100101"))

    def test_mountain_info_xml_page_and_empty_page(self):
        page = decode_page(MNT, 200, real_bytes("kfs_mnt_info_all_p1.xml"))
        self.assertEqual((len(page.items), page.total_count), (1000, 4705))
        empty = decode_page(MNT, 200, real_bytes("kfs_mnt_info_all_p6.xml"))
        self.assertEqual((empty.items, empty.total_count), ((), 4705))

    def test_real_empty_search(self):
        page = decode_page(TRAIL, 200, real_bytes("kfs_trail_search_empty.xml"))
        self.assertEqual((page.items, page.total_count), ((), 0))

    def test_hand_empty_json_page(self):
        page = decode_page(TOP100, 200, hand_bytes("ktc_empty_page.json"))
        self.assertEqual((page.items, page.total_count), ((), 0))

    def test_real_gateway_timeout_is_retryable(self):
        for name in ("ktc_plus_list_504.json", "ktc_top100_list_empty_504.json"):
            with self.assertRaises(CallFailed) as caught:
                decode_page(TOP100, 504, real_bytes(name))
            error = caught.exception
            self.assertEqual((error.result_code, error.status, error.retryable), ("05", 504, True))

    def test_real_key_errors_are_not_retryable(self):
        for source, name in ((TOP100, "ktc_bad_key.json"), (TRAIL, "kfs_bad_key.xml")):
            with self.assertRaises(CallFailed) as caught:
                decode_page(source, 200, real_bytes(name))
            self.assertEqual((caught.exception.result_code, caught.exception.retryable), ("30", False))


class BadPagesTest(unittest.TestCase):
    def assert_fails(self, source, body, status=200, retryable=None):
        with self.assertRaises(CallFailed) as caught:
            decode_page(source, status, body)
        if retryable is not None:
            self.assertEqual(caught.exception.retryable, retryable)
        return caught.exception

    def test_result_code(self):
        error = self.assert_fails(TOP100, hand_bytes("ktc_result_code_99.json"), retryable=False)
        self.assertEqual(error.result_code, "99")
        xml = real_bytes("kfs_trail_search_empty.xml").replace(b"<resultCode>00<", b"<resultCode>05<")
        self.assertEqual(self.assert_fails(TRAIL, xml, retryable=True).result_code, "05")

    def test_xml_doctype_and_truncated(self):
        self.assert_fails(MNT, hand_bytes("kfs_doctype.xml"), retryable=False)
        self.assert_fails(MNT, hand_bytes("kfs_truncated.xml"), retryable=True)

    def test_not_json_not_utf8_and_odd_shapes(self):
        self.assert_fails(TOP100, b"<html>x</html>", retryable=False)
        self.assert_fails(TOP100, b"\xff\xfe", retryable=False)
        self.assert_fails(TOP100, b'{"response": 1}', retryable=False)
        self.assert_fails(TOP100, b'{"response": {"header": {"resultCode": "0"}, "body": {"items": 5}}}', retryable=False)
        self.assert_fails(TOP100, b'{"response": {"header": {"resultCode": "0"}, "body": {"items": {"item": [1]}}}}')
        self.assert_fails(TOP100, b'{"response": {"header": {"resultCode": "0"}, "body": {"totalCount": "x"}}}')
        self.assert_fails(TOP100, b"{broken", retryable=True)
        self.assert_fails(TRAIL, b"<other/>", retryable=False)

    def test_http_errors_without_gateway_body(self):
        self.assertEqual(self.assert_fails(TOP100, b"", status=500, retryable=True).status, 500)
        self.assertEqual(self.assert_fails(TOP100, b"", status=404, retryable=False).status, 404)

    def test_single_item_object_and_bom(self):
        row = real_list()[0]
        body = b"\xef\xbb\xbf" + ktc_page([row], 1, 1).replace(b'"item": [', b'"item": ').replace(b"}]}", b"}}")
        page = decode_page(TOP100, 200, body)
        self.assertEqual(page.items, (row,))


class FetchAllTest(unittest.TestCase):
    ROWS = real_list()

    def page(self, start, end, page_no, total=100):
        return RawResponse(200, ktc_page(self.ROWS[start:end], page_no, total))

    def test_one_page_is_enough_when_total_is_reached(self):
        api, transport, _ = client(self.page(0, 100, 1))
        self.assertEqual(len(fetch_all(api, TOP100)), 100)
        self.assertEqual(len(transport.urls), 1)
        self.assertIn("numOfRows=100&pageNo=1&serviceKey=", transport.urls[0])
        self.assertIn("type=json", transport.urls[0])

    def test_pages_until_total(self):
        source = TOP100.__class__(**{**TOP100.__dict__, "page_size": 40})
        api, transport, _ = client(self.page(0, 40, 1), self.page(40, 80, 2), self.page(80, 100, 3))
        self.assertEqual(len(fetch_all(api, source)), 100)
        self.assertEqual(len(transport.urls), 3)

    def test_empty_page_before_total_is_incomplete(self):
        source = TOP100.__class__(**{**TOP100.__dict__, "page_size": 60})
        api, _, _ = client(self.page(0, 60, 1), self.page(0, 0, 2))
        with self.assertRaises(CallFailed) as caught:
            fetch_all(api, source)
        self.assertIn("incomplete", caught.exception.describe())

    def test_more_rows_than_total(self):
        api, _, _ = client(self.page(0, 100, 1, total=90))
        with self.assertRaises(CallFailed):
            fetch_all(api, TOP100)

    def test_empty_list_and_page_limit(self):
        api, _, _ = client(RawResponse(200, hand_bytes("ktc_empty_page.json")))
        self.assertEqual(fetch_all(api, TOP100), [])
        source = TOP100.__class__(**{**TOP100.__dict__, "page_size": 1})
        api, _, _ = client(*[self.page(i, i + 1, i + 1) for i in range(3)])
        with self.assertRaises(CallFailed):
            fetch_all(api, source, max_pages=3)

    def test_mountain_info_uses_capital_key_param(self):
        rows = RawResponse(200, real_bytes("kfs_trail_search_empty.xml"))
        api, transport, _ = client(rows)
        fetch_all(api, MNT)
        self.assertIn("&ServiceKey=", transport.urls[0])
        self.assertNotIn("serviceKey=", transport.urls[0])

    def test_gateway_timeout_retried_then_success(self):
        timeout = RawResponse(504, real_bytes("ktc_plus_list_504.json"))
        api, transport, clock = client(timeout, timeout, self.page(0, 100, 1))
        self.assertEqual(len(fetch_all(api, TOP100)), 100)
        self.assertEqual((len(transport.urls), clock.sleeps), (3, [20.0, 40.0]))

    def test_key_error_is_not_retried(self):
        api, transport, _ = client(RawResponse(200, real_bytes("ktc_bad_key.json")))
        with self.assertRaises(CallFailed):
            fetch_all(api, TOP100)
        self.assertEqual(len(transport.urls), 1)


if __name__ == "__main__":
    unittest.main()
