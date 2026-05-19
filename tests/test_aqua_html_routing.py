import unittest

from services.aqua_keys import (
    aqua_service_for_html_dir,
    is_valid_aqua_service,
    normalize_aqua_service,
)
from services.html_templates import html_subdir_for_service, html_template_path


class AquaHtmlRouting(unittest.TestCase):
    def test_services(self) -> None:
        self.assertEqual(normalize_aqua_service("tori.fi"), "tori_fi")
        self.assertEqual(normalize_aqua_service("posti.fi"), "posti_fi")
        self.assertFalse(is_valid_aqua_service("tutti_ch"))

    def test_html_dirs(self) -> None:
        self.assertEqual(html_subdir_for_service("tori_fi"), "tori_fi")
        self.assertEqual(html_subdir_for_service("posti_fi"), "posti_fi")
        self.assertEqual(aqua_service_for_html_dir("posti_fi"), "posti_fi")

    def test_template_exists(self) -> None:
        p = html_template_path("tori_fi", "confirmation.html")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertTrue(p.is_file())


if __name__ == "__main__":
    unittest.main()
