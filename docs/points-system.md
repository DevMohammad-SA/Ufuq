# نظام النقاط والعملات

> كل رقم في هذا الملف مستخرج حرفيًا من ثوابت أو قيَم حرفية في
> `participants/views.py`، مع ذكر السطر.

## العملات الثلاث

معرّفة على موديل `Participant` (`participants/models.py:95-97`)، كلها
`PositiveIntegerField(default=0)`:

| الحقل | الاسم | الطبيعة |
|-------|------|---------|
| `points` | النقاط | عملة الترتيب. **قابلة للتصفير** برمجيًا (زر تصفير النقاط في لوحة المشرف العام). |
| `miles` | الأميال | النتيجة التراكمية الدائمة. **لا تُصفَّر أبدًا** بأي إجراء في الكود. |
| `purchase_points` | النقاط الشرائية | تُصرف في المتجر. **لا تُصفَّر أبدًا** بإجراء التصفير؛ يعدّلها المتجر فقط. |

## معادلة التحويل — `apply_points_delta`

`participants/views.py:211-222`. تأخذ `points_delta` (موجب أو سالب) وتطبّقه على
الثلاث دفعة واحدة:

```
participant.points          = max(0, points + points_delta)
participant.miles           = max(0, miles  + points_delta * 10)
participant.purchase_points = max(0, purchase_points + points_delta)
participant.save(update_fields=["points", "miles", "purchase_points"])
```

- **الأميال = النقاط × 10** (المضاعف الحرفي `10` في السطر 220).
- **النقاط الشرائية = نفس مقدار النقاط** (دلتا 1:1).
- كل رصيد **محدود بحد أدنى صفر** (`max(0, ...)`) — الدلتا السالبة لا تنزل بأي
  رصيد تحت الصفر.
- الدوال التي تستدعي `apply_points_delta`: تسجيل حضور اللقاء، تسجيل حضور/إنجاز
  الحلقة القرآنية، قبول المهمة الأسبوعية. (المتجر **لا** يستدعيها — يعدّل
  `purchase_points` وحدها مباشرة.)

## مصادر النقاط الحالية والقيَم الفعلية

### 1. حضور اللقاء الأسبوعي — `SupervisorDashboardView`

الثوابت (`participants/views.py:43-44`):
```
MEETING_FULL_POINTS        = 8
MEETING_EARLY_BONUS_POINTS = 2
```

الدالة `meeting_attendance_points(attended, is_early)`
(`participants/views.py:244-250`):

| الحالة | النقاط |
|--------|--------|
| لم يحضر | 0 |
| حضر (غير مبكر) | 8 |
| حضر مبكرًا | 10 (‏8 + 2) |

يُحفظ في موديل `MeetingAttendance` (حقلا `attended` و`is_early`)، وتُطبّق دلتا
الفرق عبر `apply_points_delta` في `post()` (`participants/views.py:498-500`).

### 2. حضور وإنجاز الحلقة القرآنية — `QuranCircleAttendanceView`

الثوابت (`participants/views.py:233-234`):
```
QURAN_ATTENDANCE_POINTS  = 3
QURAN_ACHIEVEMENT_POINTS = 2
```

الدالة `quran_circle_points(attended, achieved)`
(`participants/views.py:237-241`) — الحضور والإنجاز **مستقلان تمامًا**:

| حضر؟ | أنجز؟ | النقاط |
|------|-------|--------|
| لا | لا | 0 |
| نعم | لا | 3 |
| لا | نعم | 2 |
| نعم | نعم | 5 |

يُحفظ في موديل `CircleAttendance` (حقلا `attended` و`achieved`)، وتُطبّق دلتا
الفرق في `post()` (`participants/views.py:643-645`).

> **تحذير — كود ميت:** يوجد ثابت مستقل `CIRCLE_DAY_POINTS = 3`
> (`participants/views.py:42`) ودالة `circle_attendance_points(attended)`
> (`participants/views.py:225-226`) — **غير مستخدَمين في أي View**. الحلقة
> القرآنية تُحسب حصريًا بـ `quran_circle_points`. راجع
> [`known-limitations.md`](known-limitations.md).

### 3. قبول المهمة الأسبوعية — `WeeklyTaskReviewView`

`participants/views.py:1240-1241`:
```python
if action == "accept":
    apply_points_delta(submission.participant, 10)
```

- **قبول تسليم مهمة أسبوعية = 10 نقاط** (قيمة **حرفية** `10`، **ليست ثابتًا
  مسمّى**).
- الرفض = 0 نقطة (لا استدعاء لـ `apply_points_delta`).
- لا يوجد تقييم جزئي — القبول يمنح 10 كاملة.
- إعادة فتح التسليم ثم رفع ملف جديد يعيده لحالة `PENDING`؛ إذا رُفض بعد أن كان
  مقبولًا، لا يوجد في الكود خصم تلقائي للـ 10 نقاط الممنوحة سابقًا (يحسب
  المراجع الفرق يدويًا فقط عند القبول/الرفض المباشر من `PENDING`).

## تصفير النقاط (وحفظ اللقطة)

`GeneralSupervisorDashboardView.post()` عند `action == "reset_points"`
(`participants/views.py:939-960`) داخل `transaction.atomic()`:

1. ينشئ صف `PointsResetSnapshot` **لكل مشارك** بقيمة `points` الحالية
   (`bulk_create`).
2. ثم `Participant.objects.update(points=0)` — **`points` فقط**.

الأميال والنقاط الشرائية **لا تُمَس**. راجع
[`features.md`](features.md#لقطة-تاريخية-للنقاط) و[`models.md`](models.md).

## مؤشر رحلة النخبة (لا يمنح نقاطًا — عرض فقط)

`ParticipantDashboardView.get_elite_status` (`participants/views.py:281-347`).
يحسب ترتيب المشارك **داخل بيئته** حسب `-points` ثم `id`، ويقارن بحدّ **أعلى 20**
(قيَم حرفية `20`/`21`). النتيجة رسالة نصية تُعرض في لوحة المشارك — **لا تُخزَّن
أبدًا** وتُعاد حسابها كل طلب. لا علاقة لها بأي عملة.
