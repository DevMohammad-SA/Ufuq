"""Pure chart adapter for the COMPLETE groups report, before pagination.

Only the five aggregate tables are read. No ORM, report reconstruction, private
student data, or pre-rounded display values enter the returned JSON payload.
"""
import math


_REPORT_FIELDS = {"key", "title", "description", "metrics", "notes", "sections"}
_ERROR = "عقد تقرير مقارنة البيئات غير صالح أو غير مكتمل."
_SCOPE_NOTE = (
    "المقارنة بحسب بيئة الطلاب ومرحلتهم وحالة حسابهم الحالية، لا عضوية تاريخية. "
    "النسب من السجلات المحفوظة وقد تتأثر بالتحضير الجزئي؛ غير المسجل ليس غيابًا."
)
_POINT_TITLE = "حجم البيئة والنقاط — حسب العضوية الحالية"
_POINT_COLUMNS = [
    "البيئة الحالية", "طلاب النطاق", "النشطون", "المعطلون", "المكتسب", "الخصم", "صافي الفترة",
    "مقام متوسط الصافي (طلاب النطاق)", "متوسط الصافي لكل طالب", "مجموع الرصيد الحالي (ليس رصيد الفترة)",
]
_TASK_TITLE = "تسليمات الفترة — مقام الطلاب ظاهر"
_TASK_COLUMNS = [
    "البيئة الحالية", "طلاب النطاق (مقام نسبة الطلاب المسلّمين)", "التسليمات المحفوظة",
    "المقبولة (تشمل المميزة)", "المميزة من المقبولة", "قيد المراجعة", "المعادة للتعديل", "المرفوضة",
    "طلاب لديهم تسليم واحد على الأقل في الفترة", "نسبة الطلاب ذوي التسليم %",
]
_ATTENDANCE_COLUMNS = [
    "البيئة الحالية", "طلاب النطاق", "الحاضر", "الغياب الصريح", "مقام الحضور (السجلات المحفوظة)",
    "نسبة الحضور %",
]
_ATTENDANCE_TABLES = (
    ("meeting", "اللقاء — مقارنة البيئات", "الحضور المبكر"),
    ("circle", "الحلقة القرآنية — مقارنة البيئات", "إنجاز الحلقة"),
    ("activity", "فعالية الأسبوع — مقارنة البيئات", "إنجاز الفعالية"),
)
_SERIES = (
    ("meeting", "نسبة حضور اللقاء", "percent", "الحاضر ÷ سجلات اللقاء المحفوظة خلال الفترة."),
    ("circle", "نسبة حضور الحلقة", "percent", "الحاضر ÷ سجلات الحلقة المحفوظة خلال الفترة."),
    ("achievement", "نسبة إنجاز الحلقة", "percent", "الإنجاز ÷ سجلات الحلقة المحفوظة؛ مستقل عن الحضور."),
    ("activity", "نسبة إنجاز الفعالية", "percent", "الحاضر ÷ سجلات فعالية الأسبوع المحفوظة خلال الفترة."),
    ("submitters", "نسبة الطلاب ذوي التسليم", "percent", "طلاب لديهم تسليم واحد على الأقل بتاريخ التسليم الأول في الفترة ÷ كل طلاب النطاق الحالي."),
    ("net_average", "متوسط صافي النقاط لكل طالب", "points", "صافي حركات دفتر النقاط الموقعة وفق أساس التاريخ المختار ÷ كل طلاب النطاق الحالي؛ ليس متوسط الرصيد."),
)


def _require(condition):
    if not condition:
        # Never include source values, identities, names or notes in errors.
        raise ValueError(_ERROR)


def _count(value, *, signed=False):
    _require(type(value) is int and (signed or value >= 0))


def _display(value, denominator, *, percent=False):
    if denominator == 0:
        _require(isinstance(value, str) and value == "—")
        return
    _require(type(value) in (int, float))
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite and (not percent or 0 <= value <= 100))


def _table(sections, title, columns, display_index, *, signed_index=None):
    matches = [section for section in sections if section["title"] == title]
    _require(len(matches) == 1)
    section = matches[0]
    _require(section["columns"] == columns)
    rows = []
    for row in section["rows"]:
        _require(type(row) is dict and set(row) == {"cells"})
        cells = row["cells"]
        _require(type(cells) is list and len(cells) == len(columns))
        _require(isinstance(cells[0], str))
        for index, value in enumerate(cells[1:], 1):
            if index != display_index:
                _count(value, signed=(index == signed_index))
        rows.append(cells)
    return rows


def _point(name, numerator, denominator, *, percent=True):
    value = None
    if denominator:
        try:
            value = round(numerator * (100 if percent else 1) / denominator, 2)
        except OverflowError:
            raise ValueError(_ERROR) from None
        _require(math.isfinite(value))
    return {
        "name": str.__str__(name), "value": value,
        "numerator": numerator, "denominator": denominator,
    }


def build_group_comparison(report):
    """Return six ordered, plain-JSON series from a complete groups report.

    The caller must pass build_reports' groups result BEFORE Paginator mutates
    its sections. Malformed, partial or other report contracts raise ValueError
    with a fixed, non-sensitive message. Extra detailed/private sections are
    never copied; group buckets (including empty and ungrouped) remain intact.
    """
    _require(type(report) is dict and set(report) == _REPORT_FIELDS)
    _require(report["key"] == "groups")
    _require(isinstance(report["title"], str) and isinstance(report["description"], str))
    _require(type(report["notes"]) is list and all(isinstance(note, str) for note in report["notes"]))
    sections = report["sections"]
    _require(type(sections) is list)
    for section in sections:
        _require(type(section) is dict and set(section) == {"title", "columns", "rows"})
        _require(isinstance(section["title"], str))
        _require(type(section["columns"]) is list and all(isinstance(column, str) for column in section["columns"]))
        _require(type(section["rows"]) is list)

    metrics = report["metrics"]
    _require(type(metrics) is list and len(metrics) == 2)
    metric_values = {}
    for metric in metrics:
        _require(type(metric) is dict and set(metric) == {"label", "value"})
        _require(isinstance(metric["label"], str) and metric["label"] not in metric_values)
        _count(metric["value"])
        metric_values[metric["label"]] = metric["value"]
    _require(set(metric_values) == {"البيئات والتجميعات المعروضة", "طلاب النطاق الحالي"})

    points = _table(sections, _POINT_TITLE, _POINT_COLUMNS, 8, signed_index=6)
    tasks = _table(sections, _TASK_TITLE, _TASK_COLUMNS, 9)
    attendance = {
        key: _table(sections, title, _ATTENDANCE_COLUMNS + [extra_label], 5)
        for key, title, extra_label in _ATTENDANCE_TABLES
    }
    _require(len(points) == metric_values["البيئات والتجميعات المعروضة"])
    _require(sum(row[1] for row in points) == metric_values["طلاب النطاق الحالي"])
    buckets = [(row[0], row[1]) for row in points]
    for rows in [tasks, *attendance.values()]:
        _require([(row[0], row[1]) for row in rows] == buckets)

    result = {key: [] for key, *_ in _SERIES}
    for index, row in enumerate(points):
        name, size, active, inactive, earned, deducted, net, denominator, average, balance = row
        _require(active + inactive == size and denominator == size and net == earned - deducted)
        _require(size > 0 or (earned == deducted == net == balance == 0))
        _display(average, denominator)
        result["net_average"].append(_point(name, net, size, percent=False))

        task = tasks[index]
        submitted, accepted, featured, pending, returned, rejected, submitters = task[2:9]
        _require(accepted + pending + returned + rejected == submitted and featured <= accepted)
        _require(submitters <= size and submitters <= submitted)
        _require((submitters > 0) == (submitted > 0))
        _display(task[9], size, percent=True)
        result["submitters"].append(_point(name, submitters, size))

        for key, rows in attendance.items():
            current = rows[index]
            present, absent, recorded, display, extra = current[2:]
            _require(present + absent == recorded and extra <= recorded)
            _require(size > 0 or recorded == 0)
            _require(key != "activity" or extra == present)
            _display(display, recorded, percent=True)
            result[key].append(_point(name, present, recorded))
            if key == "circle":
                result["achievement"].append(_point(name, extra, recorded))
    return {
        "series": [{"key": key, "label": label, "unit": unit, "description": description,
                    "points": result[key]} for key, label, unit, description in _SERIES],
        "scope_note": _SCOPE_NOTE,
    }
