import unittest

from techpack_pipeline.localize import lock_cell, load_glossary, localize_table_html


class GlossaryLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs = load_glossary("zh")

    def lock(self, text: str) -> str:
        locked, *_rest = lock_cell(text, self.pairs)
        return locked

    def test_terms(self):
        self.assertEqual(self.lock("DTM"), "同色配线")
        self.assertEqual(self.lock("CB"), "后中")
        self.assertEqual(self.lock("Swift Tack"), "打枪条")

    def test_na_does_not_eat_natural_or_name(self):
        self.assertEqual(self.lock("N/A"), "N/A")
        self.assertEqual(self.lock("Natural"), "本色")
        self.assertTrue(self.lock("Style Name:").startswith("款名"))
        self.assertIn("品牌", self.lock("Brand Name/Logo:"))
        self.assertTrue(self.lock("Designer's Name:").startswith("设计师"))
        self.assertTrue(self.lock("Pattern File Name:").startswith("纸样文件"))
        self.assertTrue(self.lock("Additional Notes & Comments:").startswith("备注"))

    def test_digits_and_codes_stay(self):
        self.assertEqual(self.lock("YKK: 316"), "YKK: 316")
        self.assertIn("97%", self.lock("Fabric: 97% Cotton, 3% Spandex Poplin, 5.5 oz, 60\""))
        self.assertIn("5.5 oz", self.lock("Fabric: 97% Cotton, 3% Spandex Poplin, 5.5 oz, 60\""))
        self.assertIn("60\"", self.lock("Fabric: 97% Cotton, 3% Spandex Poplin, 5.5 oz, 60\""))
        self.assertIn("#5", self.lock("Zipper: #5 Close End with Auto-Lock Slider"))
        self.assertIn("18\" L", self.lock("18\" L"))

    def test_sample_rows(self):
        fabric = self.lock('Fabric: 97% Cotton, 3% Spandex Poplin, 5.5 oz, 60"')
        self.assertIn("面料", fabric)
        self.assertIn("棉", fabric)
        self.assertIn("氨纶", fabric)
        self.assertIn("府绸", fabric)
        self.assertEqual(self.lock("Main Fabric"), "主面料")
        self.assertEqual(self.lock("As Per Requirement"), "按需")
        self.assertEqual(self.lock("4\" From Hem"), "距脚口 4\"")
        zipper = self.lock("Zipper: #5 Close End with Auto-Lock Slider")
        self.assertTrue(zipper.startswith("拉链"))
        self.assertIn("闭尾自动锁拉链头", zipper)
        self.assertEqual(
            self.lock("Swift Tack: 1\", 100% Recycled Polypropylene"),
            "打枪条: 1\", 100% 再生聚丙烯",
        )

    def test_html_bilingual_and_idempotent(self):
        src = "<table><tr><td>DTM</td><td>YKK: 316</td><td></td></tr></table>"
        out, report = localize_table_html(src, residual=False)
        self.assertGreater(report.glossary_hits, 0)
        self.assertIn("同色配线", out)
        self.assertIn("YKK: 316", out)
        self.assertIn("data-tp-zh", out)
        again, report2 = localize_table_html(out, residual=False)
        self.assertEqual(report2.changed, 0)
        self.assertEqual(again, out)


if __name__ == "__main__":
    unittest.main()
