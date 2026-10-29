"""PostgreSQL parity tests on an explicitly LOCAL Unix socket only.

Create a disposable local database and role first. Never point these tests at
production: this module intentionally does not accept a remote host or DSN.
"""
import os
import pwd

from .test_settings import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("UFUQ_TEST_DB_NAME", "ufuq_port_test_base"),
        "USER": os.environ.get("UFUQ_TEST_DB_USER", pwd.getpwuid(os.getuid()).pw_name),
        "HOST": "/var/run/postgresql",
        "PORT": "5432",
    },
}
