from django.contrib.auth.backends import BaseBackend

from .models import Role, User


class NationalIDOrUsernameBackend(BaseBackend):
    """
    Custom authentication backend.

    Tries to find a matching user first by national_id (for Participants),
    then falls back to username (for Group/General Supervisors and Superadmin).
    Registered alongside Django's default ModelBackend in AUTHENTICATION_BACKENDS
    (not as a replacement) — Django tries each backend in order until one succeeds.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = None

        try:
            user = User.objects.get(national_id=username)
        except User.DoesNotExist:
            pass

        if user is not None:
            # SECURITY NOTE: Participants log in with national_id ONLY, no password
            # check at all. This is an intentional business decision documented in
            # the project's requirements — anyone who knows a participant's
            # national_id can log in as them. There is no additional secret involved.
            if user.role == Role.PARTICIPANT:
                return user if user.is_active else None

            if user.check_password(password) and user.is_active:
                return user
            return None

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return None

        if user.check_password(password) and user.is_active:
            return user
        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
