import unittest

from collector.textclean import clean_text
from collector.tests.support import real_trail


class CleanTextTest(unittest.TestCase):
    def test_br_becomes_newline(self):
        self.assertEqual(clean_text("가<br>나<BR/>다<br />라"), "가\n나\n다\n라")

    def test_tags_removed_entities_unescaped_spaces_collapsed(self):
        self.assertEqual(clean_text('<p><span style=font-size: 11pt;>a&nbsp;&amp;\t\t b</span></p>'), "a & b")

    def test_none_blank_and_non_strings(self):
        self.assertEqual(clean_text(None), "")
        self.assertEqual(clean_text(" "), "")
        self.assertEqual(clean_text(19), "19")

    def test_less_than_sign_and_korean_brackets_are_kept(self):
        self.assertEqual(clean_text("3 < 5 그리고 a<b"), "3 < 5 그리고 a<b")
        self.assertEqual(clean_text("&lt;소양강 비경&gt;<br>가리산"), "<소양강 비경>\n가리산")

    def test_real_transport_lines(self):
        row = next(r for r in real_trail() if r["mntnm"] == "가리산")
        self.assertEqual(
            clean_text(row["transport"]).splitlines(),
            ["홍천에서 역내리, 천현리행 버스를 이용해 가리산자연휴양림에서 내린다.", "- 홍천→역내리 60분 간격 운행", "- 홍천→천현리 1일 10회 운행"],
        )


if __name__ == "__main__":
    unittest.main()
