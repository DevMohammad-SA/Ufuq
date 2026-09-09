from django.contrib.auth.backends import BaseBackend

from .models import User


class NationalIDOrUsernameBackend(BaseBackend):
    """
    Authenticates by national_id (participants) or username (everyone
    else). Participants now go through a real password check like any
    other role — the previous no-password-check design for participants
    has been retired entirely. A participant's initial password is their
    own national_id, which they are forced to change on first login (see
    User.must_set_password / ForcePasswordSetupMiddleware).

    Registered alongside Django's default ModelBackend in
    AUTHENTICATION_BACKENDS (not as a replacement) — Django tries each
    backend in order until one succeeds.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = None

        try:
            user = User.objects.get(national_id=username)
        except User.DoesNotExist:
            pass

        if user is None:
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
