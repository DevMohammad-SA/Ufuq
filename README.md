# ufuq — رحّال

Youth development tracking platform for the **Horizon Program** (برنامج أُفق), a 14‑week
program that turns daily attendance and weekly activities into a rewards currency. The
end‑user product is branded **رحّال** ("Rahhal").

The entire UI and domain language is **Arabic** (`ar-sa`, `Asia/Riyadh`, right‑to‑left
templates). This README is in English for GitHub; the application itself is not.

## Overview

Participants belong to an *environment* (بيئة) of ~35 people led by a group supervisor. As
supervisors record attendance, participants earn a triple‑currency reward:

| Currency | Field | Notes |
| --- | --- | --- |
| Miles (الأميال) | `miles` | Permanent score; `points × 10`. Never reset. |
| Points (النقاط) | `points` | Spendable / resettable ranking currency. |
| Purchase points (النقاط الشرائية) | `purchase_points` | Spent in the store. Never reset. |

Points table: a Quran‑circle day is 3 points, the weekly gathering is 8 points, and arriving
early to the gathering adds a 2‑point bonus. All balances are clamped at zero.

### Roles

- **Participant** — logs in with national ID only (no password), sees a personal dashboard
  comparing their miles against their environment and the whole program.
- **Group supervisor** — records weekly‑gathering attendance for their environment as a bulk
  roster, and views their participants' data.
- **General supervisor** / **Superadmin** — bulk‑import participants from Excel, program‑wide
  points reset, full participant data table, and Django admin.

> **Security note:** participant login is intentionally passwordless — anyone who knows a
> participant's national ID can sign in as them. This is a deliberate product decision for
> this age group, not an oversight.

## Tech stack

- Python ≥ 3.12, [Django](https://www.djangoproject.com/) 6.0.x
- [django-unfold](https://github.com/unfoldadmin/django-unfold) — themed admin
- [django-environ](https://django-environ.readthedocs.io/) — settings from `.env`
- [openpyxl](https://openpyxl.readthedocs.io/) — Excel participant import
- SQLite (local development)
- [uv](https://docs.astral.sh/uv/) — dependency and environment management

## Getting started

```bash
# 1. Install dependencies into a managed virtualenv
uv sync

# 2. Configure environment
cp .env.example .env
#    edit .env — SECRET_KEY is required; DEBUG defaults to False

# 3. Set up the database
uv run python manage.py migrate

# 4. Create an admin account
uv run python manage.py createsuperuser

# 5. Run the development server
uv run python manage.py runserver
```

- App: <http://127.0.0.1:8000/>
- Admin: <http://127.0.0.1:8000/admin/>

### Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SECRET_KEY` | yes | — | Django secret key |
| `DEBUG` | no | `False` | Debug mode |

`.env` and `db.sqlite3` are gitignored.

## Common commands

```bash
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py test                 # full suite
uv run python manage.py test accounts        # one app
uv run python manage.py test accounts.tests.SomeTestCase.test_method
```

## Project layout

```
config/         Django project package (settings, urls, wsgi/asgi)
accounts/       Identity & authentication — custom User model, Role, auth backend
participants/   Program data — Group, Participant, attendance, dashboards, Excel import
templates/      Shared templates (home page)
static/         Logo and the Excel import template
```

### Apps

- **`accounts`** owns identity only. `User` (`AbstractBaseUser` + `PermissionsMixin`) is
  identified by *either* a username *or* a national ID. `NationalIDOrUsernameBackend`
  authenticates participants by national ID (passwordless) and everyone else by password.
- **`participants`** owns program data. `Participant` is a one‑to‑one extension of `User`.
  Attendance records are plain rows; converting attendance into currency happens explicitly
  in the views through a single `apply_points_delta` helper, so re‑submitting a roster is
  idempotent.

Dependency direction is always `participants → accounts`, never the reverse.

## Contributing

See [`CLAUDE.md`](CLAUDE.md) for detailed architecture notes and conventions (Arabic strings,
unfold admin, the passwordless auth flow, the points model, and more). Keep all user‑facing
strings and model `verbose_name`s in Arabic.
