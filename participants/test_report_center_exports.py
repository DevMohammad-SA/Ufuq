"""Pure export tests: SimpleTestCase forbids database access; no fixtures/models.

Can run without creating a test DB:
    DJANGO_SETTINGS_MODULE=config.settings python -m unittest participants.test_report_center_exports
The host needs the existing openpyxl/WeasyPrint dependencies and PDF native libs.
"""
import datetime
import subprocess
import sys
import tempfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.utils.safestring import mark_safe
from openpyxl import load_workbook

from .report_center_exports import (
    _local_pdf_fetcher, _pdf_assets, _pdf_chunks, _pdf_reports, _prepare,
    export_reports_pdf, export_reports_xlsx,
)


NOW = datetime.datetime(2026, 11, 7, 14, 30, tzinfo=ZoneInfo("Asia/Riyadh"))
FILTERS = [{"label": "الفترة", "value": "جميع الفترات"},
           {"label": "البيئة", "value": "جميع البيئات"}]


def example_report(key="students", title="الطلاب التفصيلي", count=120):
    return {
        "key": key, "title": title, "description": "بيانات مصطنعة للتصدير فقط.",
        "metrics": [{"label": "عدد الطلاب", "value": count},
                    {"label": "صافي النقاط", "value": -25},
                    {"label": "المتوسط", "value": 1.25}],
        "notes": ["الرصيد الحالي مستقل عن نقاط الفترة.", "غير المسجل لا يُعد غائبًا."],
        "sections": [
            {"title": "الطلاب", "columns": ["الاسم", "النقاط", "الصافي", "المتوسط", "التاريخ"],
             "rows": [{"cells": ["ROW-{:03d}".format(index), index, -index, index / 4,
                                 datetime.date(2026, 11, 7)],
                       "detail_url": "http://169.254.169.254/latest/meta-data/"}
                      for index in range(count)]},
            {"title": "قسم إضافي", "columns": ["البيان", "القيمة"],
             "rows": [{"cells": ["LAST-SECOND-SECTION", -99]}]},
        ],
    }


class ReportCentreXlsxTests(SimpleTestCase):
    def workbook(self, reports=None, filters=None):
        data = export_reports_xlsx(reports if reports is not None else [example_report()],
                                   FILTERS if filters is None else filters, NOW)
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b"PK"))
        return load_workbook(BytesIO(data)), data

    def test_complete_rows_multiple_sections_and_native_numbers_dates(self):
        workbook, _ = self.workbook()
        sheet = workbook.worksheets[1]
        values = {cell.value: cell for row in sheet for cell in row if isinstance(cell.value, str)}
        self.assertIn("ROW-119", values)  # beyond a 50-row UI page
        self.assertIn("LAST-SECOND-SECTION", values)
        last = values["ROW-119"].row
        for column, expected in [(3, 119), (4, -119), (5, 29.75)]:
            cell = sheet.cell(last, column)
            self.assertEqual(cell.value, expected)
            self.assertEqual(cell.data_type, "n")
            self.assertNotEqual(cell.number_format, "General")
        self.assertEqual(sheet.cell(last, 6).value.date(), datetime.date(2026, 11, 7))
        self.assertEqual(sheet.cell(last, 6).number_format, "yyyy-mm-dd")
        self.assertEqual(len(sheet.tables), 2)
        self.assertTrue(all(table.autoFilter is not None for table in sheet.tables.values()))
        self.assertIsNotNone(sheet.auto_filter.ref)
        self.assertIsNotNone(sheet.freeze_panes)

    def test_five_selected_reports_produce_six_sheets_and_four_produce_five(self):
        reports = [example_report(key, title, 1) for key, title in [
            ("summary", "الملخص العام"), ("students", "الطلاب التفصيلي"),
            ("attendance", "الحضور والإنجاز"), ("tasks", "المهام الأسبوعية"),
            ("groups", "مقارنة البيئات")]]
        workbook, _ = self.workbook(reports)
        self.assertEqual(len(workbook.worksheets), 6)
        self.assertEqual(len(self.workbook(reports[:4])[0].worksheets), 5)
        for sheet in workbook:
            self.assertTrue(sheet.sheet_view.rightToLeft)
            self.assertFalse(sheet.sheet_view.showGridLines)
        links = [cell.hyperlink for row in workbook.worksheets[0] for cell in row if cell.hyperlink]
        self.assertEqual(len(links), 5)
        self.assertTrue(all(link.location and link.target is None for link in links))
        self.assertIn("+03:00", " ".join(str(cell.value) for row in workbook.worksheets[1] for cell in row))
        with self.assertRaises(ValueError):
            export_reports_xlsx(reports + [example_report("extra", "سادس", 1)], FILTERS, NOW)

    def test_all_untrusted_strings_are_explicit_strings_no_formula_xml_or_urls(self):
        attacks = ["=SUM(1,2)", "+cmd|' /C calc'!A0", "-1+2", "@SUM(A1)",
                   "\t=HYPERLINK(\"https://example.invalid\")", "\r=1+1",
                   "https://example.invalid/", "=1\x00+2\x0b"]
        report = example_report(count=0)
        report.update(title="=Report", description=attacks[0],
                      metrics=[{"label": text, "value": text} for text in attacks], notes=attacks)
        report["sections"] = [{"title": attacks[1], "columns": attacks,
                               "rows": [{"cells": attacks, "detail_url": "https://example.invalid/"}]}]
        filters = [{"label": text, "value": text} for text in attacks]
        workbook, data = self.workbook([report], filters)
        string_values = []
        for sheet in workbook:
            for row in sheet:
                for cell in row:
                    self.assertNotEqual(cell.data_type, "f")
                    if isinstance(cell.value, str):
                        self.assertEqual(cell.data_type, "s")
                        string_values.append(cell.value)
                    if cell.hyperlink:
                        self.assertIsNone(cell.hyperlink.target)
        for text in attacks[:-1]:
            self.assertIn(text.replace("\r", "\n"), string_values)
        self.assertIn("=1+2", string_values)
        self.assertNotIn("'=SUM(1,2)", string_values)
        with ZipFile(BytesIO(data)) as archive:
            for name in archive.namelist():
                if name.endswith(".xml"):
                    root = ET.fromstring(archive.read(name))
                    self.assertFalse(root.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}f"))
                if name.endswith(".rels"):
                    self.assertNotIn(b'TargetMode="External"', archive.read(name))

    def test_no_private_fields_are_pulled_or_new_columns_invented(self):
        report = example_report(count=1)
        report["private"] = {"national_id": "PRIVATE-MUST-NOT-APPEAR", "phone": "PRIVATE-PHONE"}
        report["sections"][0]["rows"][0]["national_id"] = "PRIVATE-MUST-NOT-APPEAR"
        before = deepcopy(report)
        workbook, _ = self.workbook([report])
        values = [cell.value for sheet in workbook for row in sheet for cell in row]
        self.assertNotIn("PRIVATE-MUST-NOT-APPEAR", values)
        self.assertNotIn("PRIVATE-PHONE", values)
        self.assertEqual(report, before)
        # Explicitly included upstream scalars must not be independently filtered.
        report["sections"][0]["columns"].append("الهوية")
        report["sections"][0]["rows"][0]["cells"].append("IDENTITY-OPT-IN")
        workbook, _ = self.workbook([report])
        self.assertIn("IDENTITY-OPT-IN", [cell.value for sheet in workbook for row in sheet for cell in row])

    def test_empty_reports_sections_and_duplicate_sheet_names(self):
        self.assertEqual(len(self.workbook([])[0].worksheets), 1)
        report = example_report(count=0)
        report["sections"] = [{"title": "فارغ", "columns": ["الاسم"], "rows": []}]
        workbook, _ = self.workbook([report, deepcopy(report)])
        self.assertEqual(len(set(workbook.sheetnames)), 3)
        self.assertIn("لا توجد نتائج مطابقة في هذا القسم.",
                      [cell.value for row in workbook.worksheets[1] for cell in row])

    def test_invalid_contracts_never_silently_truncate_or_evaluate_iterators(self):
        with self.assertRaises(ValueError):
            export_reports_xlsx([], [], NOW.replace(tzinfo=None))
        report = example_report(count=1)
        report["sections"][0]["rows"][0]["cells"].append("extra")
        with self.assertRaises(ValueError):
            export_reports_xlsx([report], FILTERS, NOW)
        report = example_report(count=1)
        report["sections"][0]["rows"][0]["cells"][0] = "x" * 32768
        with self.assertRaises(ValueError):
            export_reports_xlsx([report], FILTERS, NOW)
        with self.assertRaises(ValueError):
            export_reports_xlsx(iter([example_report()]), FILTERS, NOW)
        report = example_report(count=1)
        report["sections"][0]["rows"][0]["cells"][1] = float("nan")
        with self.assertRaises(ValueError):
            export_reports_xlsx([report], FILTERS, NOW)


class ReportCentrePdfTests(SimpleTestCase):
    def test_module_import_does_not_load_weasyprint_or_native_libraries(self):
        script = "import sys; sys.modules['weasyprint'] = None; import participants.report_center_exports; print('lazy-ok')"
        result = subprocess.run([sys.executable, "-c", script], cwd=settings.BASE_DIR,
                                capture_output=True, text=True, check=True)
        self.assertIn("lazy-ok", result.stdout)

    def test_html_autoescape_even_safestring_and_no_detail_or_remote_urls(self):
        attack = mark_safe('<img src="http://169.254.169.254/secret"><script>alert(1)</script>')
        report = example_report(count=1)
        report.update(title=attack, description=attack, notes=[attack], metrics=[{"label": attack, "value": attack}])
        report["sections"][0]["title"] = attack
        report["sections"][0]["columns"][0] = attack
        report["sections"][0]["rows"][0]["cells"][0] = attack
        with patch("weasyprint.HTML") as html:
            html.return_value.write_pdf.return_value = b"%PDF-test"
            self.assertEqual(export_reports_pdf([report], [{"label": attack, "value": attack}], NOW), b"%PDF-test")
        rendered = html.call_args.kwargs["string"]
        self.assertNotIn("<script>", rendered)
        self.assertNotIn('<img src="http', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("latest/meta-data", rendered)
        self.assertNotIn("base_url", html.call_args.kwargs)
        self.assertNotIn("letterhead", rendered)
        self.assertIn("A4 landscape", rendered)
        self.assertIn("table-header-group", rendered)
        self.assertNotIn("|safe", rendered)

    def test_fetcher_accepts_only_exact_local_bundled_files(self):
        assets, allowed, roots = _pdf_assets()
        self.assertEqual(len(assets), 4)
        fetch = _local_pdf_fetcher(allowed, roots)
        resource = fetch(assets["font_regular"])
        try:
            self.assertGreater(len(resource.read()), 50000)
        finally:
            resource.close()
        for url in ["https://example.invalid/font.ttf", "http://127.0.0.1/", "data:text/plain,secret",
                    "ftp://example.invalid/", "file:///etc/passwd", "file://localhost/etc/passwd",
                    (Path(settings.BASE_DIR) / "static/fonts/tajawal/OFL.txt").as_uri(),
                    assets["font_regular"] + "?query=1", assets["font_regular"] + "#fragment"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch(url)

    def test_asset_resolution_different_static_root_and_symlink_escape(self):
        with tempfile.TemporaryDirectory(prefix="report-export-test-") as directory:
            base = Path(directory)
            collected = base / "collected"
            fonts = base / "static/fonts/tajawal"
            fonts.mkdir(parents=True)
            outside = base / "outside.ttf"
            outside.write_bytes(b"not-a-trusted-font")
            (fonts / "Tajawal-Regular.ttf").symlink_to(outside)
            with override_settings(BASE_DIR=base, STATIC_ROOT=collected):
                assets, allowed, roots = _pdf_assets()
                self.assertNotIn("font_regular", assets)
                with self.assertRaises(ValueError):
                    _local_pdf_fetcher(allowed, roots)((fonts / "Tajawal-Regular.ttf").as_uri())
        with override_settings(STATIC_ROOT="/tmp/absent-report-static-root"):
            assets, _, _ = _pdf_assets()
            self.assertTrue(assets["font_regular"].startswith((Path(settings.BASE_DIR) / "static").as_uri()))

    def test_wide_tables_and_long_cells_keep_every_column_row_and_character(self):
        report = example_report(count=0)
        long_text = "بداية " + "long-token-" * 600 + "\n" * 40 + " النهاية"
        source = {"title": "واسع", "columns": ["عمود {}".format(index) for index in range(18)],
                  "rows": [{"cells": ["ROW-{:03d}".format(row)] + [long_text if index == 17 else index for index in range(1, 18)]}
                           for row in range(61)]}
        report["sections"] = [source]
        prepared, _ = _prepare([report], FILTERS, NOW)
        panels = _pdf_reports(prepared)[0]["sections"][0]["panels"]
        self.assertEqual(len(panels), 4)
        self.assertEqual({column for panel in panels for column in panel["columns"]}, set(source["columns"]))
        for panel in panels:
            self.assertLessEqual(len(panel["columns"]), 6)
            self.assertEqual(sum(not row["continued"] for row in panel["rows"]), 61)
            self.assertIn("ROW-060", [row["cells"][0] for row in panel["rows"]])
        self.assertEqual("".join(_pdf_chunks(long_text)), long_text)
        self.assertTrue(all(len(chunk) <= 240 and chunk.count("\n") <= 8 for chunk in _pdf_chunks(long_text)))

    def test_real_pdf_header_and_library_errors_propagate(self):
        data = export_reports_pdf([example_report(count=61)], FILTERS, NOW)
        self.assertIsInstance(data, bytes)
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertGreater(len(data), 5000)
        with patch("weasyprint.HTML", side_effect=OSError("native renderer unavailable")):
            with self.assertRaises(OSError):
                export_reports_pdf([example_report(count=1)], FILTERS, NOW)
