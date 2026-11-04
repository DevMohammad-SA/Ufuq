"""Isolated helper and Template.render regressions; no view wiring required."""

import json
from html.parser import HTMLParser

from django.db import connection
from django.template import Context
from django.template.loader import get_template
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import Role, User
from .competition import GENERIC_RIDER_POSITIONS, build_elite_competition
from .models import Group, Participant, PointsLedgerEntry, PointsResetSnapshot
from .views import ParticipantDashboardView


CONTEXT_KEYS = {
    "rank", "total", "limit", "qualified", "difference", "difference_kind",
    "position_percent", "cutoff_percent", "marker_positions",
}


class CompetitionMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def matching(self, class_name):
        return [
            attrs for _, attrs in self.elements
            if class_name in attrs.get("class", "").split()
        ]

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class EliteCompetitionTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name="بيئة منافسة مصطنعة")
        self.other_group = Group.objects.create(name="بيئة أخرى مصطنعة")
        self.number = 0

    def make_participant(self, points=0, *, group=None, active=True):
        self.number += 1
        user = User.objects.create_user(
            national_id=f"8700{self.number:06d}",
            full_name=f"اسم سري لا يعرض {self.number}",
            role=Role.PARTICIPANT,
            password="test-only-password",
            is_active=active,
            must_set_password=False,
        )
        return Participant.objects.create(
            pk=700000 + self.number,
            user=user,
            group=group or self.group,
            academic_stage="grade_7",
            points=points,
            miles=self.number * 100003,
            purchase_points=self.number * 7,
        )

    def make_ranking(self, total=24):
        return [self.make_participant(1000 - index * 13) for index in range(total)]

    @staticmethod
    def render_dashboard(participant, competition, old_status=None):
        # Invoke the real Django Template directly, even before root view wiring.
        context = Context({
            "participant": participant,
            "elite_competition": competition,
            "elite_status": old_status,
            "program_range": None,
            "points_ledger": [],
            "navbar_items": [],
            "csrf_token": "synthetic-csrf-token",
        })
        html = get_template("participants/participant_dashboard.html").template.render(context)
        return html, context

    def test_rank_six_seven_ten_are_numeric_in_helper_and_rendered_template(self):
        ranking = self.make_ranking()
        for rank in (6, 7, 10):
            with self.subTest(rank=rank):
                student = ranking[rank - 1]
                data = build_elite_competition(student)
                self.assertEqual(data["rank"], rank)
                self.assertEqual(data["total"], len(ranking))
                self.assertTrue(data["qualified"])
                self.assertAlmostEqual(data["position_percent"], rank / len(ranking) * 100, places=5)
                html, _ = self.render_dashboard(student, data)
                self.assertIn(f"<dd><bdi>{rank}</bdi></dd>", html)
                self.assertIn(f"أنت / {rank}", html)
                self.assertIn(f'aria-label="ترتيبك {rank} من {len(ranking)}', html)

    def test_exact_gap_and_margin_match_the_existing_elite_status(self):
        ranking = self.make_ranking()
        view = ParticipantDashboardView()
        for rank in (1, 6, 7, 10, 20, 21, 22, 24):
            with self.subTest(rank=rank):
                student = ranking[rank - 1]
                old = view.get_elite_status(student)
                before = dict(old)
                data = build_elite_competition(student)
                self.assertEqual(data["difference"], old["difference"])
                self.assertEqual(data["qualified"], old["eliteStatus"] == "qualified")
                self.assertEqual(data["difference_kind"], "margin" if rank <= 20 else "gap")
                expected = (
                    student.points - ranking[20].points
                    if rank <= 20 else ranking[19].points - student.points
                )
                self.assertEqual(data["difference"], expected)
                _, context = self.render_dashboard(student, data, old)
                self.assertIs(context["elite_status"], old)
                self.assertEqual(old, before)

    def test_rank_twenty_one_zero_gap_does_not_claim_nomination_or_guaranteed_points(self):
        ranking = [self.make_participant(55) for _ in range(21)]
        data = build_elite_competition(ranking[20])
        self.assertEqual(data["rank"], 21)
        self.assertFalse(data["qualified"])
        self.assertEqual(data["difference"], 0)
        self.assertEqual(data["difference_kind"], "gap")
        old = ParticipantDashboardView().get_elite_status(ranking[20])
        html, _ = self.render_dashboard(ranking[20], data, old)
        self.assertIn("أنت خارج أعلى 20 حاليًا", html)
        self.assertIn("التعادل في النقاط لا يعني دخول أعلى 20", html)
        self.assertIn("المعرّف الأسبق", html)
        self.assertNotIn(old["message"], html)
        self.assertNotIn("نقطة واحدة تضمن", html)
        self.assertNotIn("أنت ضمن المرشحين لرحلة النخبة", html)

    def test_rank_twenty_zero_margin_is_qualified_but_not_safe_from_tie_changes(self):
        ranking = [self.make_participant(55) for _ in range(21)]
        data = build_elite_competition(ranking[19])
        self.assertEqual(data["rank"], 20)
        self.assertTrue(data["qualified"])
        self.assertEqual(data["difference"], 0)
        self.assertEqual(data["difference_kind"], "margin")
        html, _ = self.render_dashboard(ranking[19], data)
        self.assertIn("أنت داخل أعلى 20 حاليًا وفق مرجّح التعادل الثابت", html)
        self.assertIn("الترتيب قابل للتغيّر", html)
        self.assertIn("هامش نقاطك عن المركز 21", html)

    def test_all_zero_points_still_use_id_ascending_and_rank_positions(self):
        ranking = [self.make_participant(0) for _ in range(22)]
        for rank in (1, 10, 20, 21, 22):
            with self.subTest(rank=rank):
                data = build_elite_competition(ranking[rank - 1])
                self.assertEqual(data["rank"], rank)
                self.assertEqual(data["difference"], 0)
                self.assertEqual(data["qualified"], rank <= 20)
                self.assertAlmostEqual(data["position_percent"], rank / 22 * 100, places=5)
                self.assertEqual(data, build_elite_competition(ranking[rank - 1]))

    def test_inactive_competitors_and_other_environment_do_not_affect_rank_or_count(self):
        ranking = self.make_ranking(20)
        self.make_participant(999999, active=False)
        self.make_participant(888888, group=self.other_group)
        data = build_elite_competition(ranking[-1])
        self.assertEqual(data["rank"], 20)
        self.assertEqual(data["total"], 20)
        self.assertTrue(data["qualified"])
        self.assertIsNone(data["difference"])
        self.assertIsNone(data["difference_kind"])
        self.assertEqual(data["cutoff_percent"], 100)
        self.assertIsNone(ParticipantDashboardView().get_elite_status(ranking[-1])["difference"])

    def test_small_groups_have_no_comparison_and_limit_at_end(self):
        for total in (1, 6, 7, 10, 20):
            with self.subTest(total=total):
                group = Group.objects.create(name=f"مجموعة صغيرة {total}")
                ranking = [self.make_participant(index, group=group) for index in range(total)]
                data = build_elite_competition(ranking[0])
                self.assertEqual(data["rank"], total)
                self.assertEqual(data["total"], total)
                self.assertTrue(data["qualified"])
                self.assertIsNone(data["difference"])
                self.assertIsNone(data["difference_kind"])
                self.assertEqual(data["cutoff_percent"], 100)
                html, _ = self.render_dashboard(ranking[0], data)
                self.assertIn("لا مقارنة", html)
                self.assertIn("جميع المنافسين النشطين ضمن الحد", html)
                self.assertIn("الخط عند نهاية الشريط", html)
                markup = CompetitionMarkup(html)
                self.assertEqual(markup.matching("elite-competition__cutoff")[0]["style"], "inset-inline-start:100.0%")
                if total == 1:
                    self.assertEqual(data["marker_positions"], [])
                    self.assertFalse(markup.matching("elite-competition__rider"))

    def test_no_group_returns_none_without_queries_and_renders_a_healthy_empty_state(self):
        student = self.make_participant()
        student.group = None
        student.save(update_fields=["group"])
        with self.assertNumQueries(0):
            self.assertIsNone(build_elite_competition(student))
        html, _ = self.render_dashboard(student, None)
        self.assertIn("سيظهر موقعك في المنافسة بعد إسنادك إلى بيئة", html)
        self.assertFalse(CompetitionMarkup(html).matching("elite-competition__course"))

    def test_inactive_or_unsaved_student_is_not_inserted_into_active_ranking(self):
        inactive = self.make_participant(1000, active=False)
        self.make_participant(0)
        self.assertIsNone(build_elite_competition(inactive))
        unsaved = Participant(user=inactive.user, group=self.group, academic_stage="grade_7")
        with self.assertNumQueries(0):
            self.assertIsNone(build_elite_competition(unsaved))
        html, _ = self.render_dashboard(inactive, None)
        self.assertIn("لا تتوفر بيانات منافسة لحسابك حاليًا", html)

    def test_cutoff_line_is_twenty_over_total_and_student_uses_rank_over_total(self):
        ranking = self.make_ranking(24)
        data = build_elite_competition(ranking[20])
        self.assertAlmostEqual(data["cutoff_percent"], 20 / 24 * 100, places=5)
        self.assertAlmostEqual(data["position_percent"], 21 / 24 * 100, places=5)
        html, _ = self.render_dashboard(ranking[20], data)
        markup = CompetitionMarkup(html)
        self.assertEqual(markup.matching("elite-competition__cutoff")[0]["style"], f'inset-inline-start:{data["cutoff_percent"]}%')
        self.assertEqual(markup.matching("elite-competition__me")[0]["style"], f'inset-inline-start:{data["position_percent"]}%')
        self.assertIn("يبدأ الترتيب من اليمين", html)
        self.assertIn("حد الترشيح: أعلى 20", html)

    def test_current_points_change_rank_and_no_miles_or_snapshots_are_consulted(self):
        ranking = self.make_ranking()
        student = ranking[9]
        before = build_elite_competition(student)
        PointsResetSnapshot.objects.create(participant=student, points_before_reset=999999)
        Participant.objects.filter(pk=student.pk).update(miles=999999999, purchase_points=999999999)
        self.assertEqual(build_elite_competition(student), before)
        # The helper reads current DB points even when the caller's object is stale.
        Participant.objects.filter(pk=student.pk).update(points=9999999)
        with CaptureQueriesContext(connection) as queries:
            after = build_elite_competition(student)
        self.assertEqual(after["rank"], 1)
        self.assertLessEqual(len(queries), 3)
        for query in queries:
            sql = query["sql"].lower()
            self.assertTrue(sql.startswith("select"))
            self.assertNotIn("miles", sql)
            self.assertNotIn("pointsresetsnapshot", sql)

    def test_context_and_json_have_only_own_rank_aggregates_and_generic_markers(self):
        ranking = self.make_ranking()
        student = ranking[5]
        data = build_elite_competition(student)
        self.assertEqual(set(data), CONTEXT_KEYS)
        self.assertEqual(data["marker_positions"], list(GENERIC_RIDER_POSITIONS))
        encoded = json.dumps(data, ensure_ascii=False)
        html, _ = self.render_dashboard(student, data)
        for other in ranking:
            if other == student:
                continue
            self.assertNotIn(other.user.full_name, html)
            self.assertNotIn(other.user.national_id, html)
            self.assertNotIn(str(other.pk), html)
            self.assertNotIn(str(other.pk), encoded)
            self.assertNotIn(other.user.full_name, encoded)
            self.assertNotIn(other.user.national_id, encoded)
            self.assertNotIn(f'"points": {other.points}', encoded)
        self.assertNotIn("data-participant-id", html)
        self.assertNotIn("<script type=\"application/json\"", html)
        self.assertIn("الفرسان رموز توضيحية عامة", html)

    def test_other_students_cutoff_points_are_not_rendered_or_returned(self):
        ranking = self.make_ranking(19)
        cutoff = self.make_participant(981357)
        student = self.make_participant(940123)
        Participant.objects.filter(pk__in=[row.pk for row in ranking]).update(points=999999)
        data = build_elite_competition(student)
        self.assertEqual(data["rank"], 21)
        self.assertEqual(data["difference"], cutoff.points - student.points)
        html, _ = self.render_dashboard(student, data)
        encoded = json.dumps(data)
        for forbidden in (str(cutoff.points), cutoff.user.national_id, cutoff.user.full_name, str(cutoff.pk)):
            self.assertNotIn(forbidden, html)
            self.assertNotIn(forbidden, encoded)

    def test_large_environment_uses_bounded_queries_and_at_most_six_generic_riders(self):
        student = self.make_participant(10)
        total_others = 2000
        users = User.objects.bulk_create([
            User(
                national_id=f"8800{index:06d}", full_name=f"خصوصية مجموعة كبيرة {index}",
                role=Role.PARTICIPANT, password="!", must_set_password=False,
            )
            for index in range(total_others)
        ])
        Participant.objects.bulk_create([
            Participant(user=user, group=self.group, points=20, academic_stage="grade_7")
            for user in users
        ])
        with self.assertNumQueries(3):
            data = build_elite_competition(student)
        self.assertEqual(data["total"], total_others + 1)
        self.assertEqual(data["rank"], total_others + 1)
        self.assertEqual(data["position_percent"], 100)
        self.assertLessEqual(len(data["marker_positions"]), 6)
        html, _ = self.render_dashboard(student, data)
        markup = CompetitionMarkup(html)
        self.assertLessEqual(len(markup.matching("elite-competition__rider")), 6)
        self.assertEqual(len(markup.matching("elite-competition__me")), 1)
        self.assertLess(len(markup.elements), 200)
        self.assertNotIn("خصوصية مجموعة كبيرة", html)

    def test_helper_and_template_do_not_write_balances_ledger_or_snapshots(self):
        ranking = self.make_ranking()
        before = list(Participant.objects.order_by("pk").values_list("pk", "points", "miles", "purchase_points"))
        data = build_elite_competition(ranking[5])
        self.render_dashboard(ranking[5], data)
        after = list(Participant.objects.order_by("pk").values_list("pk", "points", "miles", "purchase_points"))
        self.assertEqual(before, after)
        self.assertEqual(PointsLedgerEntry.objects.count(), 0)
        self.assertEqual(PointsResetSnapshot.objects.count(), 0)

    def test_old_context_and_invitation_survive_before_root_wiring_without_duplicate_card(self):
        student = self.make_participant(100)
        old = ParticipantDashboardView().get_elite_status(student)
        self.assertEqual(set(old), {"eliteStatus", "difference", "message"})
        before = dict(old)
        html, context = self.render_dashboard(student, None, old)
        self.assertIn(old["message"], html)
        self.assertIn('class="elite-card elite-qualified"', html)
        self.assertIs(context["elite_status"], old)
        self.assertEqual(old, before)
        wired_html, _ = self.render_dashboard(student, build_elite_competition(student), old)
        self.assertIn("أنت ضمن المرشحين لرحلة النخبة", wired_html)
        self.assertEqual(len(CompetitionMarkup(wired_html).matching("elite-competition")), 1)
        self.assertFalse(CompetitionMarkup(wired_html).matching("elite-card"))
        self.assertEqual(old, before)

    def test_percentages_are_not_localized_into_invalid_css(self):
        ranking = self.make_ranking()
        data = build_elite_competition(ranking[5])
        html, _ = self.render_dashboard(ranking[5], data)
        self.assertIn(f'width:{data["cutoff_percent"]}%', html)
        self.assertNotIn("inset-inline-start:25,", html)
        self.assertIn('href="/static/css/elite-competition.css"', html)
