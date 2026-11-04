from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from .models import Group, Participant


class CompetitionPageIntegrationTests(TestCase):
    def setUp(self):
        self.group = Group.objects.create(name='بيئة المنافسة')
        self.user = User.objects.create_user(username='own_competitor', full_name='صاحب الصفحة', password='synthetic-password', role=Role.PARTICIPANT, must_set_password=False)
        self.student = Participant.objects.create(user=self.user, group=self.group, points=70)
        rival = User.objects.create_user(username='private_rival', full_name='اسم المنافس السري', password='synthetic-password', role=Role.PARTICIPANT, must_set_password=False)
        Participant.objects.create(user=rival, group=self.group, points=90)
        self.url = reverse('participants:dashboard')

    def test_real_page_has_own_rank_and_no_rival_identity(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['elite_competition']['rank'], 2)
        self.assertEqual(response.context['elite_competition']['total'], 2)
        self.assertContains(response, 'elite-competition')
        self.assertNotContains(response, 'اسم المنافس السري')
        self.assertNotContains(response, 'private_rival')
        self.assertEqual(set(response.context['elite_competition']), {'rank', 'total', 'limit', 'qualified', 'difference', 'difference_kind', 'position_percent', 'cutoff_percent', 'marker_positions'})

    def test_supervisor_cannot_use_student_page(self):
        for role in (Role.GROUP_SUPERVISOR, Role.GENERAL_SUPERVISOR, Role.SUPERADMIN):
            user = User.objects.create_user(username=f'forbidden_{role}', password='synthetic-password', role=role, must_set_password=False)
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 403)

    def test_no_environment_is_safe(self):
        self.student.group = None
        self.student.save(update_fields=['group'])
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['elite_competition'])

    def test_disabled_student_has_no_session_access(self):
        self.client.force_login(self.user)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
