import json
import unittest
from pathlib import Path

from collector.config import KEY_ENV
from collector.errors import CallFailed
from collector.http_client import RawResponse
from collector.tests.support import (
    FIRST_RUN_UTC,
    SECOND_RUN_AT,
    FakeDataGoKr,
    real_bytes,
    real_list,
    run_collect,
    snapshot,
    temp_repo,
)

TIMEOUT_504 = RawResponse(504, real_bytes("ktc_plus_list_504.json"))
BAD_KEY = RawResponse(200, real_bytes("kfs_bad_key.xml"))


def read(repo: Path) -> dict:
    return json.loads((repo / "data" / "mountains.json").read_text(encoding="utf-8"))


def item(data: dict, item_id: str) -> dict:
    return next(i for i in data["items"] if i["id"] == item_id)


class FirstRunTest(unittest.TestCase):
    def test_writes_the_contract_document(self):
        with temp_repo() as repo:
            result = run_collect(repo)
            self.assertEqual(result.code, 0, result.text)
            data = read(repo)
        self.assertEqual(list(data), ["schemaVersion", "generatedAt", "sources", "items"])
        self.assertEqual((data["schemaVersion"], data["generatedAt"]), (1, FIRST_RUN_UTC))
        self.assertEqual(len(data["items"]), 100)
        self.assertEqual(len({i["id"] for i in data["items"]}), 100)
        self.assertEqual(sum(1 for i in data["items"] if i["stampSource"] == "override"), 19)
        self.assertEqual({i["set"] for i in data["items"]}, {"top100"})
        self.assertEqual([s["id"] for s in data["sources"]], ["ktc", "ktcPoi", "kfsTrail", "kfsMnt"])
        ktc = data["sources"][0]
        self.assertEqual((ktc["count"], ktc["fetchedAt"], ktc["extractedAt"]), (100, FIRST_RUN_UTC, "2026-11-08 06:01:50"))
        self.assertEqual(ktc["url"], "https://www.data.go.kr/data/15125127/openapi.do")
        pocheon = item(data, "m-0000000043")
        self.assertEqual((pocheon["label"], pocheon["stamp"][0]["name"]), ("백운산 (포천)", "백운산 정상"))
        # 주흘산 has a blank 15158915 reason; its 15058662 row is not on the fixture page.
        self.assertEqual([i["name"] for i in data["items"] if not i["reason"]], ["주흘산"])

    def test_calls_once_per_source_and_reports(self):
        with temp_repo() as repo:
            result = run_collect(repo)
        self.assertEqual(result.transport.kinds(), ["list", "poi", "trail", "info"])
        self.assertIn("summary: items=100 invalid=0 overridden=19 joinedTrail=100", result.out)
        self.assertIn("noPoi=4", result.out)
        self.assertNotIn("poi check m-0000000046", result.out)  # 변산 세봉 = POI 세봉
        self.assertIn("warning: poi check m-0000000081 (천성산)", result.out)
        self.assertTrue(result.commit_message.startswith("chore(data): update mountains (+100 new"))
        self.assertIn("✅ mountain-stamp-data 수집\n산 100 · 검증 탈락 0 · 도장 지점 보정 19", result.notice)
        self.assertIn("⚠ 경고 1", result.notice)

    def test_list_order_and_items_sorted_by_name(self):
        with temp_repo() as repo:
            run_collect(repo)
            names = [i["label"] for i in read(repo)["items"]]
        self.assertEqual(names[:3], ["가리산", "가리왕산", "가야산"])
        self.assertLess(names.index("백운산 (광양)"), names.index("백운산 (정선)"))


class SecondRunTest(unittest.TestCase):
    def test_no_changes_then_a_change(self):
        with temp_repo() as repo:
            run_collect(repo)
            second = run_collect(repo, now=SECOND_RUN_AT)
            self.assertEqual(second.code, 0)
            self.assertTrue(second.commit_message.startswith("chore(data): refresh mountain list (no mountain changes)"))
            self.assertEqual(read(repo)["generatedAt"], "2026-11-16T19:10:00Z")
            rows = real_list()
            rows[0] = {**rows[0], "aslAltide": 1052.0}
            rows[1] = {**rows[1], "lat": rows[1]["lat"] + 0.001}
            third = run_collect(repo, FakeDataGoKr(rows={**FakeDataGoKr().rows, "list": rows[:-1]}))
        self.assertEqual(third.code, 0, third.text)
        self.assertIn("(+0 new, ~1 moved, ~1 height, ~0 info, -1 removed)", third.commit_message)

    def test_failed_description_source_keeps_previous_text(self):
        with temp_repo() as repo:
            run_collect(repo)
            before = read(repo)
            result = run_collect(repo, FakeDataGoKr(errors={"trail": TIMEOUT_504}), now=SECOND_RUN_AT)
            after = read(repo)
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("⚠ 실패한 원천 (이전 글 유지): kfsTrail", result.notice)
        for key in ("m-0000000002", "m-0000000043", "m-0000000099"):
            for field in ("subtitle", "transport", "courses", "summary"):
                self.assertEqual(item(after, key)[field], item(before, key)[field], (key, field))
        self.assertEqual(result.transport.kinds().count("trail"), 6)  # first try + 5 retries
        trail_source = next(s for s in after["sources"] if s["id"] == "kfsTrail")
        self.assertEqual(trail_source["fetchedAt"], FIRST_RUN_UTC)

    def test_failed_poi_and_info_sources_are_warnings(self):
        with temp_repo() as repo:
            run_collect(repo)
            result = run_collect(repo, FakeDataGoKr(errors={"poi": BAD_KEY, "info": CallFailed("kfsMnt", "request failed (timeout)")}))
            data = read(repo)
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("ktcPoi, kfsMnt", result.notice)
        self.assertEqual(item(data, "m-0000000002")["updated"], "2020-12-01")
        self.assertNotIn("poi check", result.out)


class FailureTest(unittest.TestCase):
    def assert_nothing_written(self, repo, result, code):
        self.assertEqual(result.code, code, result.text)
        self.assertEqual(snapshot(repo), {})
        self.assertEqual(result.commit_message, "")
        self.assertIn("이전 자료 유지", result.notice)

    def test_list_call_failure(self):
        with temp_repo() as repo:
            result = run_collect(repo, FakeDataGoKr(errors={"list": TIMEOUT_504}))
            self.assert_nothing_written(repo, result, 3)
        self.assertIn("resultCode=05", result.notice)
        self.assertEqual(result.sleeps, [20.0, 40.0, 80.0, 160.0, 320.0])

    def test_list_retry_then_success(self):
        with temp_repo() as repo:
            result = run_collect(repo, FakeDataGoKr(sequence={"list": [TIMEOUT_504]}))
        self.assertEqual(result.code, 0, result.text)
        self.assertEqual(result.transport.kinds()[:2], ["list", "list"])

    def test_too_many_invalid_rows(self):
        rows = real_list()
        broken = [{**row, "lat": None} for row in rows[:21]] + rows[21:]
        with temp_repo() as repo:
            result = run_collect(repo, FakeDataGoKr(rows={**FakeDataGoKr().rows, "list": broken}))
            self.assert_nothing_written(repo, result, 1)
        self.assertIn("20% 규칙", result.notice)

    def test_twenty_invalid_rows_are_tolerated(self):
        rows = real_list()
        broken = [{**row, "lat": None} for row in rows[:20]] + rows[20:]
        with temp_repo() as repo:
            result = run_collect(repo, FakeDataGoKr(rows={**FakeDataGoKr().rows, "list": broken}))
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("검증 탈락 20", result.notice)

    def test_count_drop_over_twenty_percent(self):
        with temp_repo() as repo:
            run_collect(repo)
            before = snapshot(repo)
            result = run_collect(repo, FakeDataGoKr(rows={**FakeDataGoKr().rows, "list": real_list()[:79]}))
            self.assertEqual(result.code, 1)
            self.assertEqual(snapshot(repo), before)

    def test_duplicate_ids_count_as_invalid(self):
        rows = real_list()
        with temp_repo() as repo:
            result = run_collect(repo, FakeDataGoKr(rows={**FakeDataGoKr().rows, "list": rows + rows[:1]}))
        self.assertEqual(result.code, 0, result.text)
        self.assertIn("warning: ktc duplicate frtrlId 0000000002", result.out)

    def test_missing_key_and_broken_overrides(self):
        with temp_repo() as repo:
            result = run_collect(repo, env={})
            self.assert_nothing_written(repo, result, 2)
            self.assertEqual(result.transport.calls, [])
        with temp_repo() as repo:
            (repo / "overrides" / "summits.json").write_text("{", encoding="utf-8")
            result = run_collect(repo)
            self.assert_nothing_written(repo, result, 2)
            self.assertEqual(result.transport.calls, [])
        with temp_repo() as repo:
            self.assertEqual(run_collect(repo, env={KEY_ENV: "a b"}).code, 2)

    def test_runs_without_an_overrides_file(self):
        with temp_repo(with_overrides=False) as repo:
            result = run_collect(repo)
            data = read(repo)
        self.assertEqual(result.code, 0, result.text)
        self.assertEqual({i["stampSource"] for i in data["items"]}, {"list"})

    def test_unexpected_error(self):
        with temp_repo() as repo:
            result = run_collect(repo, FakeDataGoKr(errors={"list": RuntimeError("boom")}))
            self.assert_nothing_written(repo, result, 4)
        self.assertNotIn("boom", result.text)


if __name__ == "__main__":
    unittest.main()
