"""Pure renderers for already-authorized, complete report-centre dictionaries.

No ORM, requests, pagination, privacy inference, or additional data sources belong
here. The caller supplies the complete rows and has already applied include_private.
Both public functions return bytes, writing only to memory. Invalid contracts raise
ValueError; library/import/render errors propagate so the view can return its Arabic
export-error response (never a partial or silently truncated download). WeasyPrint
and its native dependencies are deliberately imported only by the PDF function.
"""
import datetime
import math
import mimetypes
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit


_ASPHALT = "2B2F33"
_GOLD = "C6A664"
_GOLD_INK = "85682F"
_SAND = "F7F3EA"
_LINE = "E2DCD0"
_SLATE = "5F676E"
_OVERVIEW = "الإعدادات والملخص"
_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_SHEET_INVALID = re.compile(r"[\\/*?:\[\]]")
_PDF_ASSETS = {
    "font_regular": "fonts/tajawal/Tajawal-Regular.ttf",
    "font_bold": "fonts/tajawal/Tajawal-Bold.ttf",
    "font_extra_bold": "fonts/tajawal/Tajawal-ExtraBold.ttf",
    "logo": "images/logo.png",
}


def _text(value):
    if not isinstance(value, str):
        raise ValueError("يجب أن تكون عناوين التقرير وملاحظاته نصوصًا.")
    # join also removes Django SafeString's marker: all supplied text is untrusted.
    # XML normalizes literal CR on reload; canonicalize line endings consistently.
    return _XML_INVALID.sub("", "".join((value, ""))).replace("\r\n", "\n").replace("\r", "\n")


def _scalar(value):
    if isinstance(value, str):
        return _text(value)
    if value is None:  # A missing scalar is blank, never an invented zero.
        return None
    if isinstance(value, (datetime.date, int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("لا يمكن تصدير أعداد غير منتهية.")
        return value
    raise ValueError("قيم التقرير يجب أن تكون نصوصًا أو أعدادًا أو تواريخ فقط.")


def _list(value):
    # In particular, never evaluate a QuerySet or an iterator supplied by mistake.
    if not isinstance(value, list):
        raise ValueError("يجب تمرير بيانات التقرير كاملة في قوائم جاهزة.")
    return value


def _prepare(reports, filter_labels, generated_at):
    if not isinstance(generated_at, datetime.datetime) or generated_at.utcoffset() is None:
        raise ValueError("وقت إصدار التقرير يجب أن يتضمن المنطقة الزمنية.")
    labels = [{"label": _text(item["label"]), "value": _scalar(item["value"])}
              for item in _list(filter_labels)]
    prepared = []
    for report in _list(reports):
        sections = []
        for section in _list(report["sections"]):
            columns = [_text(column) for column in _list(section["columns"])]
            rows = []
            for row in _list(section["rows"]):
                cells = [_scalar(cell) for cell in _list(row["cells"])]
                if len(cells) != len(columns):
                    raise ValueError("عدد قيم الصف لا يطابق أعمدة التقرير.")
                rows.append({"cells": cells})  # detail_url is intentionally not activated.
            sections.append({"title": _text(section["title"]),
                             "columns": columns, "rows": rows})
        prepared.append({
            "key": _text(report["key"]), "title": _text(report["title"]),
            "description": _text(report["description"]),
            "metrics": [{"label": _text(metric["label"]), "value": _scalar(metric["value"])}
                        for metric in _list(report["metrics"])],
            "notes": [_text(note) for note in _list(report["notes"])],
            "sections": sections,
        })
    return prepared, labels


def _display(value):
    if value is None:
        return "—"
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, datetime.date):
        return value.isoformat()
    return str(value)


def export_reports_xlsx(reports, filter_labels, generated_at) -> bytes:
    """One RTL settings/index sheet plus one sheet per selected report (at most 5).

    Text cells are explicit OOXML strings, not formulas, including leading =/+/−/@,
    tabs, carriage returns, and URL-looking values. Numbers remain native numbers.
    Excel's own cell/sheet limits raise an error rather than losing any data.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.worksheet.hyperlink import Hyperlink
    from openpyxl.worksheet.table import Table, TableStyleInfo
    from openpyxl.utils import get_column_letter

    reports, labels = _prepare(reports, filter_labels, generated_at)
    if len(reports) > 5:
        raise ValueError("اختر خمسة تقارير كحد أقصى لملف Excel واحد.")
    workbook = Workbook()
    overview = workbook.active
    overview.title = _OVERVIEW
    workbook.properties.title = "مركز التقارير — رحّال"
    workbook.properties.creator = "رحّال — جمعية صقيل"
    workbook.properties.created = generated_at.replace(tzinfo=None)
    regular = Font(name="Tajawal", size=11, color=_ASPHALT)
    heading = Font(name="Tajawal", size=11, bold=True, color=_ASPHALT)
    border_side = Side(style="thin", color=_LINE)

    def write_cell(sheet, row, column, value, *, header=False, numeric_format=None):
        if row > 1048576 or column > 16384:
            raise ValueError("تجاوز التقرير حدود ورقة Excel؛ لا يمكن تصديره كاملًا.")
        cell = sheet.cell(row, column)
        if isinstance(value, str):
            value = _text(value)
            if len(value) > 32767:
                raise ValueError("نص إحدى الخلايا أطول من حد Excel؛ لن يُختصر تلقائيًا.")
            cell.value = value
            cell.data_type = "s"
            cell.number_format = "@"
        elif isinstance(value, datetime.datetime):
            # Excel stores a wall-clock datetime, not a timezone. The explicit UTC
            # offset remains in the issue-time metadata on every worksheet.
            cell.value = value.replace(tzinfo=None)
            cell.number_format = "yyyy-mm-dd hh:mm:ss"
        elif isinstance(value, datetime.date):
            cell.value = value
            cell.number_format = "yyyy-mm-dd"
        else:
            cell.value = value
            cell.number_format = numeric_format or ("#,##0.00########" if isinstance(value, float) else "#,##0")
        cell.font = heading if header else regular
        cell.alignment = Alignment(horizontal="right", vertical="center", wrap_text=True, readingOrder=2)
        if header:
            cell.fill = PatternFill("solid", fgColor=_SAND)
        return cell

    def setup(sheet, width):
        sheet.sheet_view.rightToLeft = True
        sheet.sheet_view.showGridLines = False
        sheet.column_dimensions["A"].width = 3
        for column in range(2, width + 2):
            sheet.column_dimensions[get_column_letter(column)].width = 25
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.print_options.horizontalCentered = True
        sheet.sheet_properties.outlinePr.summaryRight = False

    def text_row(sheet, row, text, width, *, title=False):
        write_cell(sheet, row, 2, text, header=title)
        if width > 1:
            sheet.merge_cells(start_row=row, start_column=2, end_row=row, end_column=width + 1)
        if title:
            sheet.cell(row, 2).font = Font(name="Tajawal", size=18, bold=True, color=_GOLD_INK)
        lines = sum(max(1, math.ceil(len(line) / (width * 22))) for line in text.split("\n"))
        sheet.row_dimensions[row].height = min(409, max(35 if title else 23, lines * 16 + 10))
        return row + 1

    def frame(sheet, start, end, width):
        for row in range(start, end + 1):
            for column in range(2, width + 2):
                sheet.cell(row, column).border = Border(
                    left=border_side if column == 2 else Side(),
                    right=border_side if column == width + 1 else Side(),
                    top=border_side,
                    bottom=Side(style="medium", color=_GOLD) if row == start else border_side,
                )

    def metadata(sheet, title, description, width):
        row = text_row(sheet, 2, title, width, title=True)
        if description:
            row = text_row(sheet, row, description, width)
        row = text_row(sheet, row, "وقت الإصدار: " + _display(generated_at), width)
        write_cell(sheet, row, 2, "الإعداد", header=True)
        write_cell(sheet, row, 3, "القيمة", header=True)
        start = row
        row += 1
        for label in labels:
            write_cell(sheet, row, 2, label["label"])
            write_cell(sheet, row, 3, label["value"])
            sheet.row_dimensions[row].height = 32
            row += 1
        frame(sheet, start, row - 1, 2)
        return row + 2

    setup(overview, 4)
    row = metadata(overview, "مركز التقارير — رحّال", "جمعية صقيل | إعدادات الإصدار وفهرس التقارير المختارة", 4)
    row = text_row(overview, row, "التقارير المختارة", 4, title=True)
    index_start = row
    for column, label in enumerate(["التقرير", "عدد الأقسام", "عدد الصفوف", "البيان"], 2):
        write_cell(overview, row, column, label, header=True)
    row += 1
    used_names = {_OVERVIEW.casefold()}
    for report_number, report in enumerate(reports, 1):
        name = _SHEET_INVALID.sub(" ", report["title"]).strip().strip("'")[:31] or "تقرير"
        stem = name
        suffix = 2
        while name.casefold() in used_names:
            ending = " ({})".format(suffix)
            name = stem[:31 - len(ending)] + ending
            suffix += 1
        used_names.add(name.casefold())
        sheet = workbook.create_sheet(name)
        width = max([4] + [len(section["columns"]) for section in report["sections"]])
        if width + 1 > 16384:
            raise ValueError("عدد الأعمدة يتجاوز حدود Excel.")
        setup(sheet, width)
        index_cell = write_cell(overview, row, 2, report["title"])
        index_cell.hyperlink = Hyperlink(ref=index_cell.coordinate, location="'{}'!B2".format(name.replace("'", "''")))
        index_cell.font = Font(name="Tajawal", size=11, color=_GOLD_INK, underline="single")
        write_cell(overview, row, 3, len(report["sections"]))
        write_cell(overview, row, 4, sum(len(section["rows"]) for section in report["sections"]))
        write_cell(overview, row, 5, report["description"])
        overview.row_dimensions[row].height = 48
        row += 1
        sheet_row = metadata(sheet, report["title"], report["description"], width)
        back = write_cell(sheet, sheet_row, 2, "العودة إلى الإعدادات والملخص")
        back.hyperlink = Hyperlink(ref=back.coordinate, location="'{}'!B2".format(_OVERVIEW))
        back.font = Font(name="Tajawal", size=11, color=_GOLD_INK, underline="single")
        sheet_row += 2
        if report["metrics"]:
            sheet_row = text_row(sheet, sheet_row, "المؤشرات", width, title=True)
            start = sheet_row
            write_cell(sheet, sheet_row, 2, "المؤشر", header=True)
            write_cell(sheet, sheet_row, 3, "القيمة", header=True)
            sheet_row += 1
            for metric in report["metrics"]:
                write_cell(sheet, sheet_row, 2, metric["label"])
                write_cell(sheet, sheet_row, 3, metric["value"])
                sheet.row_dimensions[sheet_row].height = 32
                sheet_row += 1
            frame(sheet, start, sheet_row - 1, 2)
            sheet_row += 2
        if report["notes"]:
            sheet_row = text_row(sheet, sheet_row, "ملاحظات وبيان احتساب النتائج", width, title=True)
            for note in report["notes"]:
                sheet_row = text_row(sheet, sheet_row, note, width)
            sheet_row += 1
        first_header = None
        for section_number, section in enumerate(report["sections"], 1):
            sheet_row = text_row(sheet, sheet_row, section["title"], width, title=True)
            if not section["columns"]:
                sheet_row = text_row(sheet, sheet_row, "لا توجد أعمدة لهذا القسم.", width)
                continue
            start = sheet_row
            first_header = first_header or start
            column_count = len(section["columns"])
            for column, label in enumerate(section["columns"], 2):
                write_cell(sheet, sheet_row, column, label, header=True)
            sheet.row_dimensions[sheet_row].height = 40
            sheet_row += 1
            formats = ["#,##0.00########" if any(isinstance(item["cells"][index], float) for item in section["rows"])
                       else "#,##0" for index in range(column_count)]
            for item in section["rows"]:
                row_lines = 1
                for column, value in enumerate(item["cells"], 2):
                    write_cell(sheet, sheet_row, column, value, numeric_format=formats[column - 2])
                    text = _display(value)
                    row_lines = max(row_lines, sum(max(1, math.ceil(len(line) / 22)) for line in text.split("\n")))
                sheet.row_dimensions[sheet_row].height = min(409, row_lines * 16 + 10)
                sheet_row += 1
            frame(sheet, start, sheet_row - 1, column_count)
            if section["rows"]:
                ref = "B{}:{}{}".format(start, get_column_letter(column_count + 1), sheet_row - 1)
                table = Table(displayName="Report{}Section{}".format(report_number, section_number), ref=ref)
                table.tableStyleInfo = TableStyleInfo(name="TableStyleLight19", showRowStripes=True, showColumnStripes=False)
                sheet.add_table(table)
                if not sheet.auto_filter.ref:
                    sheet.auto_filter.ref = ref
            else:
                sheet_row = text_row(sheet, sheet_row, "لا توجد نتائج مطابقة في هذا القسم.", width)
            sheet_row += 2
        sheet.freeze_panes = "B{}".format(first_header + 1 if first_header else 5)
        if first_header:
            sheet.print_title_rows = "{}:{}".format(first_header, first_header)
        sheet.print_area = "B2:{}{}".format(get_column_letter(width + 1), sheet_row - 1)
    frame(overview, index_start, row - 1, 4)
    if not reports:
        row = text_row(overview, row, "لم تُختر تقارير لهذا الإصدار.", 4)
    overview.freeze_panes = "B{}".format(index_start + 1)
    overview.auto_filter.ref = "B{}:E{}".format(index_start, max(index_start, row - 1))
    overview.print_area = "B2:E{}".format(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _pdf_assets():
    """Locate only exact bundled assets, independent of STATIC_URL/HTTP hosting."""
    from django.conf import settings

    roots = [Path(settings.BASE_DIR).absolute() / "static"]
    static_root = getattr(settings, "STATIC_ROOT", None)
    if static_root:
        roots.append(Path(static_root).absolute())
    # A static directory symlink must not expand the allowed filesystem boundary.
    roots = [root for root in roots if root.is_dir() and root.resolve() == root]
    assets = {}
    allowed = set()
    for key, relative in _PDF_ASSETS.items():
        for root in roots:
            candidate = root / relative
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if resolved.is_relative_to(root) and resolved.is_file():
                assets[key] = resolved.as_uri()
                allowed.add(resolved)
                break
    return assets, frozenset(allowed), tuple(roots)


def _local_pdf_fetcher(allowed, roots):
    """No default/network fetcher: deny every scheme and non-whitelisted path."""
    def fetch(url, *args, **kwargs):
        from weasyprint.urls import URLFetcherResponse

        parsed = urlsplit(url)
        if parsed.scheme != "file" or parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("لا يسمح التصدير بتحميل موارد خارجية.")
        path = Path(unquote(parsed.path))
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("مورد محلي غير متاح.") from exc
        if path not in allowed or resolved not in allowed or not any(resolved.is_relative_to(root) for root in roots):
            raise ValueError("المورد خارج قائمة الأصول المحلية المسموح بها.")
        with resolved.open("rb") as resource:
            body = resource.read()
        return URLFetcherResponse(resolved.as_uri(), body, headers={
            "Content-Type": mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"})
    # WeasyPrint 70 checks this flag to decide whether resource errors are fatal.
    # Never return a successful-looking PDF after losing an existing bundled asset.
    fetch._fail_on_errors = True
    return fetch


def _pdf_chunks(value):
    # Keep unusually long cells within one printable row. Continued physical rows
    # contain every character, not a shortened preview; even newline-heavy or
    # single-token text cannot force an oversized table row off the page.
    text = _display(value)
    chunks = []
    while text:
        length = min(240, len(text))
        breaks = [match.end() for match in re.finditer(r"\n", text[:length])]
        if len(breaks) > 8:
            length = breaks[7]
        chunks.append(text[:length])
        text = text[length:]
    return chunks or [""]


def _pdf_reports(reports):
    result = []
    for report in reports:
        sections = []
        for section in report["sections"]:
            count = len(section["columns"])
            # Wide tables are horizontal panels with the identifying first column
            # repeated. Every source column and every source row remains present.
            groups = [list(range(count))] if count <= 6 else [
                [0] + list(range(start, min(start + 5, count))) for start in range(1, count, 5)
            ]
            panels = []
            for panel_number, indices in enumerate(groups, 1):
                rows = []
                for source_row in section["rows"]:
                    chunks = [_pdf_chunks(source_row["cells"][index]) for index in indices]
                    for part in range(max([1] + [len(cell) for cell in chunks])):
                        rows.append({"cells": [cell[part] if part < len(cell) else "" for cell in chunks],
                                     "continued": part > 0})
                panels.append({"columns": [section["columns"][index] for index in indices],
                               "rows": rows, "number": panel_number, "total": len(groups)})
            sections.append({"title": section["title"], "row_count": len(section["rows"]), "panels": panels})
        metrics = []
        for metric in report["metrics"]:
            label_chunks = _pdf_chunks(metric["label"])
            value_chunks = _pdf_chunks(metric["value"])
            for part in range(max(len(label_chunks), len(value_chunks))):
                metrics.append({"label": label_chunks[part] if part < len(label_chunks) else "",
                                "value": value_chunks[part] if part < len(value_chunks) else ""})
        result.append({**report, "sections": sections,
                       "metrics": metrics})
    return result


def export_reports_pdf(reports, filter_labels, generated_at) -> bytes:
    """Arabic A4-landscape PDF; autoescaped text and bundled file-URI assets only.

    ImportError/OSError from unavailable WeasyPrint/native libraries and renderer
    errors deliberately propagate to the caller's Arabic error handling. No request
    object, absolute web URL, filesystem export, or default URL fetcher is used.
    """
    reports, labels = _prepare(reports, filter_labels, generated_at)
    from django.template import Context, Engine
    from weasyprint import HTML
    from weasyprint.text.fonts import FontConfiguration

    assets, allowed, roots = _pdf_assets()
    engine = Engine(dirs=[str(Path(__file__).parent / "templates")], autoescape=True)
    template = engine.get_template("participants/report_center_pdf.html")
    html = template.render(Context({
        "reports": _pdf_reports(reports),
        "filter_labels": [{"label": label["label"], "value": _display(label["value"])} for label in labels],
        "generated_at": _display(generated_at), "assets": assets,
    }, autoescape=True, use_l10n=False, use_tz=False))
    return HTML(string=html, url_fetcher=_local_pdf_fetcher(allowed, roots)).write_pdf(
        font_config=FontConfiguration())
