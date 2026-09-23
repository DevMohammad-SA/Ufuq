# نظام النقاط والعملات

> كل رقم في هذا الملف مستخرج حرفيًا من ثوابت أو قيَم حرفية في
> `participants/views.py` (تحقّق مباشر من الكود الفعلي). المراجع بصيغة اسم
> الدالة/الكلاس بدل رقم السطر (أرقام الأسطر تتغيّر مع كل تعديل).

## العملات الثلاث

معرّفة على موديل `Participant` (`participants/models.py`)، كلها
`PositiveIntegerField(default=0)`:

| الحقل | الاسم | الطبيعة |
|-------|------|---------|
| `points` | النقاط | عملة الترتيب. **قابلة للتصفير** برمجيًا (زر تصفير النقاط في لوحة المشرف العام). منذ 1.1.0 **لا تُعرض** كبطاقة مستقلة في لوحة المشارك (فقط الأميال والنقاط الشرائية تُعرضان)، لكنها لا تزال محسوبة ومحفوظة فعليًا وتُستخدم في حساب مؤشر رحلة النخبة. |
| `miles` | الأميال | النتيجة التراكمية الدائمة. **لا تُصفَّر أبدًا** بأي إجراء في الكود. |
| `purchase_points` | النقاط الشرائية | تُصرف في المتجر. **لا تُصفَّر أبدًا** بإجراء التصفير؛ يعدّلها المتجر فقط. |

## معادلة التحويل — `apply_points_delta`

دالة في `participants/views.py`. تأخذ `points_delta` (موجب أو سالب) بالإضافة
إلى بيانات تدقيق (`source`, `description`, `granted_by`)، وتطبّق الدلتا على
الثلاث دفعة واحدة، ثم تكتب سجل تدقيق:

```
participant.points          = max(0, points + points_delta)
participant.miles           = max(0, miles  + points_delta * 10)
participant.purchase_points = max(0, purchase_points + points_delta)
participant.save(update_fields=["points", "miles", "purchase_points"])

PointsLedgerEntry.objects.create(
    participant=participant,
    points_delta=points_delta,
    source=source,
    description=description,
    granted_by=granted_by,
)
```

- **الأميال = النقاط × 10** (مضاعف حرفي `10`).
- **النقاط الشرائية = نفس مقدار النقاط** (دلتا 1:1).
- كل رصيد **محدود بحد أدنى صفر** (`max(0, ...)`) — الدلتا السالبة لا تنزل بأي
  رصيد تحت الصفر.
- **⚠️ تغيّر التوقيع مؤخرًا**: الدالة لم تعد تكتفي بتحديث الأرصدة — كل استدعاء
  لها يكتب أيضًا صفًا في `PointsLedgerEntry` (سجل تدقيق مركزي، انظر
  [`models.md`](models.md)). أي كود يستدعيها يجب أن
  يمرّر `source` (من `PointsLedgerEntry.Source`) و`description` نصيًا، و
  اختياريًا `granted_by` (من نفّذ الإجراء).
- الدوال/الـ Views التي تستدعي `apply_points_delta`: تسجيل حضور اللقاء، تسجيل
  حضور/إنجاز الحلقة القرآنية، تسجيل فعالية الأسبوع، قبول المهمة الأسبوعية،
  منح النقاط الإضافية اليدوي. (المتجر **لا** يستدعيها إطلاقًا — يعدّل
  `purchase_points` وحدها مباشرة، بلا سجل في `PointsLedgerEntry`.)

## مصادر النقاط الحالية والقيَم الفعلية

### 1. حضور اللقاء الأسبوعي — `SupervisorDashboardView`

الثوابت في `participants/views.py`:
```
MEETING_FULL_POINTS        = 8
MEETING_EARLY_BONUS_POINTS = 2
```

الدالة `meeting_attendance_points(attended, is_early)` — العلمان **مستقلان
تمامًا**، تمامًا كالحلقة القرآنية:

```python
def meeting_attendance_points(attended, is_early):
    points = 0
    if attended:
        points += MEETING_FULL_POINTS
    if is_early:
        points += MEETING_EARLY_BONUS_POINTS
    return points
```

| حضر؟ | مبكر؟ | النقاط |
|------|-------|--------|
| لا | لا | 0 |
| نعم | لا | 8 |
| **لا** | **نعم** | **2** |
| نعم | نعم | 10 (‏8 + 2) |

> **⚠️ تصحيح:** كانت نسخة سابقة من هذا الملف تعرض ثلاث حالات فقط وتقول إن
> "لم يحضر = 0" دائمًا. هذا **غير صحيح**: `is_early` وحده يمنح نقطتين حتى لو لم
> يُعلَّم الحضور. التصميم القديم (`if not attended: return 0`) تغيّر عمدًا في
> hotfix `b51a099` ضمن الإصدار 1.1.0 ("إصلاح خلل في آلية التحضير المبكر للطالب
> دون إكمال الحضور" في `CHANGELOG.md`)، لكن الجدول هنا لم يُحدَّث حينها.

يُحفظ في موديل `MeetingAttendance` (حقلا `attended` و`is_early`)، وتُطبّق دلتا
الفرق عبر `apply_points_delta` في `SupervisorDashboardView.post()` بمصدر
`PointsLedgerEntry.Source.MEETING_ATTENDANCE`.

### 2. حضور وإنجاز الحلقة القرآنية — `QuranCircleAttendanceView`

الثوابت في `participants/views.py`:
```
QURAN_ATTENDANCE_POINTS  = 3
QURAN_ACHIEVEMENT_POINTS = 2
```

الدالة `quran_circle_points(attended, achieved)` — الحضور والإنجاز **مستقلان
تمامًا**:

| حضر؟ | أنجز؟ | النقاط |
|------|-------|--------|
| لا | لا | 0 |
| نعم | لا | 3 |
| لا | نعم | 2 |
| نعم | نعم | 5 |

يُحفظ في موديل `CircleAttendance` (حقلا `attended` و`achieved`)، وتُطبّق دلتا
الفرق في `QuranCircleAttendanceView.post()` بمصدر
`PointsLedgerEntry.Source.QURAN_CIRCLE`.

> **تحذير — كود ميت لا يزال قائمًا:** يوجد ثابت مستقل `CIRCLE_DAY_POINTS = 3`
> ودالة `circle_attendance_points(attended)` في `participants/views.py` —
> **غير مستخدَمين في أي View**. الحلقة القرآنية تُحسب حصريًا بـ
> `quran_circle_points`. راجع [`known-limitations.md`](known-limitations.md).

### 3. فعالية الأسبوع — `WeeklyActivityAttendanceView` (جديد منذ 1.1.0)

ثابت واحد في `participants/views.py`:
```
WEEKLY_ACTIVITY_POINTS = 10
```

علم حضور واحد فقط، بلا أبعاد إضافية (خلافًا للحلقة القرآنية):

| حضر؟ | النقاط |
|------|--------|
| لا | 0 |
| نعم | 10 |

يُحفظ في موديل `WeeklyActivityAttendance`، وتُطبّق دلتا الفرق في
`WeeklyActivityAttendanceView.post()` بمصدر
`PointsLedgerEntry.Source.WEEKLY_ACTIVITY`. نمط التحضير (كشف + منتقي تاريخ،
قفل بيئة مشرف البيئة، اختيار حر للمشرف العام/النظام) مطابق تمامًا لنمط
`QuranCircleAttendanceView`.

### 4. قبول المهمة الأسبوعية — `WeeklyTaskReviewView`

في `participants/views.py`، فرع `action == "accept"`:

```python
points = 12 if is_featured else 10
apply_points_delta(
    submission.participant,
    points,
    source=PointsLedgerEntry.Source.WEEKLY_TASK,
    description=description,  # يتضمن "(مميزة ⭐)" إن كانت مميزة
    granted_by=request.user,
)
```

- تُراجَع تسليمات **كل المهام النشطة** من نفس الصفحة (عدة مهام نشطة بالتوازي
  منذ 1.2.0)، والتسليم يُبحث بمعرّفه مباشرة دون تقييده بمهمة بعينها، ولا
  تُنفَّذ المراجعة إلا على تسليم حالته `PENDING`. راجع
  [`weekly-tasks.md`](weekly-tasks.md).
- **قبول عادي = 10 نقاط**. **قبول "مميز" (`is_featured`) = 12 نقطة** (10 + 2
  نقطة إضافية للتميّز) — **⚠️ تغيّر عن التصميم السابق** الذي كان يمنح 10 ثابتة
  بلا أي تدرّج. خانة "مميزة" تظهر فقط عند القبول، وتُخزَّن على
  `TaskSubmission.is_featured`.
- الرفض = 0 نقطة (لا استدعاء لـ `apply_points_delta`)، مع تسجيل
  `TaskSubmission.rejection_reason` النصي ليظهر للمشارك.
- لا يوجد تقييم جزئي أبعد من هذا — لا تدرّج آخر غير عادي/مميز.
- إعادة فتح التسليم ثم رفع ملف جديد يعيده لحالة `PENDING`؛ إذا رُفض بعد أن كان
  مقبولًا، لا يوجد في الكود خصم تلقائي للنقاط الممنوحة سابقًا (يحسب المراجع
  الفرق يدويًا فقط عند القبول/الرفض المباشر من `PENDING`).

### 5. نقاط إضافية / خصم يدوي — `ExtraPointsView` (جديد منذ 1.1.0)

منح أو خصم يدوي بسبب نصي إلزامي، عبر `ExtraPointsForm`:

```python
apply_points_delta(
    form.cleaned_data["participant"],
    form.cleaned_data["points"],   # عدد صحيح بين -1000 و1000
    source=PointsLedgerEntry.Source.EXTRA,
    description=form.cleaned_data["reason"],
    granted_by=self.request.user,
)
```

- الدلتا محدودة بين **-1000 و+1000** على مستوى النموذج (`min_value`/`max_value`).
- الخصم يتم بإدخال قيمة سالبة — لا حقل منفصل لـ"خصم".
- **مقصور على المشرف العام/النظام فقط** — تم سحب هذه الصلاحية من مشرف البيئة
  (راجع `CHANGELOG.md`، الإصدار 1.1.0، قسم "Changed"). راجع
  [`roles-and-permissions.md`](roles-and-permissions.md).

## سجل النقاط (تدقيق) — `PointsLedgerEntry` / `PointsLedgerView`

كل استدعاء لـ `apply_points_delta` (أي المصادر الخمسة أعلاه، **ما عدا المتجر**)
يكتب صفًا في `PointsLedgerEntry` يحمل: المشارك، الدلتا الموقّعة، المصدر
(`Source`)، وصفًا نصيًا حرًا، ومن نفّذ الإجراء.

- **العرض**: `PointsLedgerView` (المسار `participants:points_ledger`،
  "سجل النقاط" في قسم "النقاط" بالقائمة الجانبية) — مشرف البيئة يرى سجل بيئته فقط، والمشرف
  العام/النظام يرى كل السجل. محدود بأحدث **200** صف بلا ترقيم صفحات (حد مؤقت
  بسيط، مقبول حاليًا نظرًا لحجم البيانات).
- كذلك تُعرض أحدث **50** حركة لكل مشارك ضمن لوحته الشخصية (`participant.points_ledger_entries`).
- **لا يشمل** حركات المتجر (شراء/استرجاع) — تلك تُتابَع عبر `StoreOrder` نفسه
  فقط، لأنها تحرّك `purchase_points` وحدها دون المرور بـ `apply_points_delta`.

## تصفير النقاط (وحفظ اللقطة)

فرع `action == "reset_points"` في `GeneralSupervisorDashboardView.post()`،
داخل `transaction.atomic()`:

1. ينشئ صف `PointsResetSnapshot` **لكل مشارك** بقيمة `points` الحالية
   (`bulk_create`).
2. ثم `Participant.objects.update(points=0)` — **`points` فقط**.

الأميال والنقاط الشرائية **لا تُمَس**. هذا الإجراء **لا يكتب** في
`PointsLedgerEntry` (السجل مخصص لحركات `apply_points_delta` الفردية، لا
للتصفير الجماعي — تاريخ التصفير له نظامه الخاص عبر `PointsResetSnapshot`).
راجع [`features.md`](features.md#لقطة-تاريخية-للنقاط) و[`models.md`](models.md).

## مؤشر رحلة النخبة (لا يمنح نقاطًا — عرض فقط)

طريقة `ParticipantDashboardView.get_elite_status`. يحسب ترتيب المشارك **داخل
بيئته** حسب `-points` ثم `id` (كمُرجِّح ثابت عند التعادل)، ويقارن بحدّ
**أعلى 20** (قيَم حرفية `20`/`21`). النتيجة رسالة نصية عربية عامية (بعد تحديث
1.1.0، بلهجة شبابية وبرموز تعبيرية 🚀💪) تُعرض في لوحة المشارك — **لا تُخزَّن
أبدًا** وتُعاد حسابها كل طلب. لا علاقة لها بأي عملة ولا تمنح نقاطًا بنفسها.
