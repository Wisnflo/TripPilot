from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eval'))
from report import component_rows,p_value


class ReportEvidenceTest(unittest.TestCase):
    def test_tiny_positive_p_is_not_rounded_to_zero(self):
        p=8.590765687358437e-22
        result=p_value(p)
        self.assertGreater(float(result),0)
        self.assertAlmostEqual(float(result)/p,1,places=5)
        self.assertEqual(p_value(None),'未知')

    def test_new_component_cohort_is_used_without_legacy_structured_rows(self):
        rows=[{'method':method,'key':key} for method in ('structured','no_feedback','direct_plan') for key in ('first','second')]
        self.assertEqual(len(component_rows({'rows':rows})),6)
        with self.assertRaises(ValueError):component_rows({'rows':rows+rows[:1]})
        with self.assertRaises(ValueError):component_rows({'rows':rows[:-1]})


if __name__=='__main__':unittest.main()
