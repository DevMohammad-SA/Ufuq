# Ufuq (أُفق) — رحّال

Read this in Arabic: [README.ar.md](README.ar.md)

Management platform for the **Horizon Program** (برنامج أُفق التنموي), a
14‑week youth development program run by **Saqeel Society for Youth
Development** (جمعية صقيل لتنمية الشباب). Supervisors record daily and weekly
attendance and a weekly task; participants earn a triple‑currency reward and
progress toward an "elite trip" nomination. The end‑user product is branded
**رحّال** ("Rahhal") — the domain name "Ufuq" and internal features like the
Quran circle stay in the code/model names, but have been removed from
participant‑facing copy.

The entire UI and domain language is **Arabic** (`ar-sa`, `Asia/Riyadh`,
right‑to‑left). This file is in English for GitHub; **[`README.ar.md`](README.ar.md) is the
Arabic version.** Detailed technical docs are in [`docs/`](docs/), and
[`CHANGELOG.md`](CHANGELOG.md) tracks released versions.

**Current version: 1.2.0** (see [`CHANGELOG.md`](CHANGELOG.md)). Its headline
changes: several weekly tasks can be open in parallel with free AND/OR
submission formats ([`docs/weekly-tasks.md`](docs/weekly-tasks.md)), and the
horizontal navbar was replaced by a collapsible sidebar plus a mobile drawer
([`docs/navigation.md`](docs/navigation.md)).

## Overview

- Participants belong to an **environment** (بيئة / `Group`), which one or
  more group supervisors can be assigned to (`Group.supervisor` is a
  many‑to‑many field).
- As supervisors record attendance and review tasks, participants earn a
  triple currency, all converted together through one function,
  `apply_points_delta` (`participants/views.py`):

  | Currency | Field | Rule |
  | --- | --- | --- |
  | Points (النقاط) | `points` | Ranking currency. Resettable program‑wide (a snapshot is saved first). No longer shown as its own card on the participant dashboard, but still computed and used for the elite‑trip ranking. |
  | Miles (الأميال) | `miles` | `points_delta × 10`, applied together. Never reset. |
  | Purchase points (النقاط الشرائية) | `purchase_points` | Spent in the store. Never reset by the reset action; the store is the only feature that moves it directly, bypassing `apply_points_delta`. |

  All balances are clamped at zero.

- **Point sources** (values taken verbatim from `participants/views.py`):

  | Source | Points | Notes |
  | --- | --- | --- |
  | Weekly gathering | 8 attended + 2 early (independent) | `MeetingAttendance` — the early flag scores 2 on its own, even with attendance unchecked |
  | Quran circle | 3 attended + 2 achieved (independent) | `CircleAttendance` |
  | Weekly activity | 10 (flat, single attendance flag) | `WeeklyActivityAttendance` — new |
  | Weekly task acceptance | 10, or 12 if marked "featured" (⭐) | `TaskSubmission.is_featured` |
  | Manual extra points / deduction | −1000 to +1000, mandatory reason | `ExtraPointsView`, general supervisor/superadmin only — new |

  Every one of these (except the store) also writes an audit-log row to
  `PointsLedgerEntry`, viewable per‑group or program‑wide in the new
  **points ledger** (`participants:points_ledger`). Full details:
  [`docs/points-system.md`](docs/points-system.md).

## Roles

Defined in `accounts/models.py` (`Role`):

| Role | Arabic | Summary |
| --- | --- | --- |
| `participant` | مشارك | Logs in with **national ID + password**. Personal dashboard (with an embedded points ledger and the elite‑trip indicator), submission page listing **every open weekly task**, store. |
| `group_supervisor` | مشرف بيئة | Records weekly‑gathering, Quran‑circle, and weekly‑activity attendance for **their own environment(s) only**; views their group's participant data and points ledger. |
| `general_supervisor` | مشرف عام | Program‑wide: Excel import, single‑participant add form, weekly tasks (create, activate/deactivate, review, archive, reopen), store management, manual extra‑points grants, points reset, full points ledger, all environments. |
| `superadmin` | مشرف النظام | Same permissions as general supervisor in every view, plus Django admin (`is_staff`/`is_superuser`). |

Full permission matrix: [`docs/roles-and-permissions.md`](docs/roles-and-permissions.md).

## Features

Every item below maps to a real view and URL — see
[`docs/features.md`](docs/features.md) for the view/template of each.

- **Attendance, three independent kinds**, each a bulk roster with a date
  picker: the weekly gathering (group supervisor, own environment), the Quran
  circle (attendance + achievement scored separately), and the weekly
  activity. Re‑submitting a roster applies only the delta, so it never
  double‑awards.
- **Weekly tasks**: several can be open in parallel; each is created inactive
  and shown only once a supervisor activates it, then drops out by itself at
  its due date. Submission formats are free checkboxes (PDF / image / audio /
  video / direct text) combined as AND ("submit all of these together") or OR
  ("pick one"), validated server‑side. Review is accept (+10, or +12 when
  marked featured ⭐) or reject with a reason the participant sees. One
  submission per participant per task, unless a supervisor reopens it.
- **Store**: participants spend purchase points; supervisors add/edit/delete
  products and complete or refund orders. Stock and balance are re‑checked
  under `select_for_update()` inside a transaction.
- **Points ledger** (`PointsLedgerEntry`): a central audit row for every
  points movement, with search/sort/filter — scoped to their own environment
  for a group supervisor, program‑wide for a general supervisor.
- **Points reset with a snapshot**: resets `points` only, after bulk‑saving
  every participant's current total into `PointsResetSnapshot` in the same
  transaction; past reset events are browsable.
- **Participant roster**: a filterable/sortable data table, plus a **PDF
  export** on the official letterhead (WeasyPrint, embedded Tajawal font).
- **Accounts**: Excel import or a single‑participant form; first password is
  the national ID, and `must_set_password` forces a real one on first login;
  a forgotten password is reset by general‑supervisor approval.
- **Navigation**: a collapsible sidebar on desktop (state persisted in
  `localStorage`) and a fixed bottom bar + slide‑in drawer on mobile, with
  live notification badges — [`docs/navigation.md`](docs/navigation.md).
- **Admin** at `/admin/`, themed with django‑unfold in the Rahhal palette and
  fixed for RTL.

## Tech stack

From `pyproject.toml` (`requires-python = ">=3.12"`):

- [Django](https://www.djangoproject.com/) `>=6.0,<6.1`
- [django-unfold](https://unfoldadmin.com/) `>=0.104.1` — themed admin
- [django-environ](https://django-environ.readthedocs.io/) `>=0.14` — `.env` settings
- [openpyxl](https://openpyxl.readthedocs.io/) `>=3.1.5` — Excel participant import
- [Pillow](https://python-pillow.org/) `>=12.3` — uploaded‑image compression
- [WeasyPrint](https://weasyprint.org/) `>=70.0` — participant‑roster PDF export
- [gunicorn](https://gunicorn.org/) `>=26.2.0` — production WSGI server (Docker deployment)
- [psycopg2-binary](https://www.psycopg.org/) `>=2.9.12` — PostgreSQL driver (Docker deployment)
- [uv](https://docs.astral.sh/uv/) — dependency & environment management
- **Chart.js 4** — loaded from CDN in the general‑supervisor dashboard only
- **Database:** **SQLite** (`db.sqlite3`) for local development (the
  default, unchanged); **PostgreSQL 16** in the Docker production setup
  (`USE_POSTGRES=True`). See [Deployment](#deployment).

## Local development

```bash
# 1. Install dependencies into a managed virtualenv
uv sync

# 2. Configure environment
cp .env.example .env
#    edit .env — SECRET_KEY is required; DEBUG defaults to False

# 3. Apply migrations
uv run python manage.py migrate

# 4. Create an admin account (role is set to superadmin automatically)
uv run python manage.py createsuperuser

# 5. Run the development server
uv run python manage.py runserver
```

- App: <http://127.0.0.1:8000/>
- Admin: <http://127.0.0.1:8000/admin/>

### Environment variables

Local development only needs the first two; the rest apply to the Docker
production setup (`.env.docker`, see [Deployment](#deployment)).

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SECRET_KEY` | yes | — | Django secret key |
| `DEBUG` | no | `False` | Debug mode |
| `ALLOWED_HOSTS` | no (prod: yes) | `[]` | Comma-separated allowed hosts |
| `CSRF_TRUSTED_ORIGINS` | no (prod: yes) | `[]` | Comma-separated trusted HTTPS origins |
| `USE_POSTGRES` | no | `False` | `True` switches `DATABASES` to PostgreSQL |
| `DB_ENGINE` | no (prod: with `USE_POSTGRES`) | `django.db.backends.postgresql` | DB backend |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` / `DB_HOST` | with `USE_POSTGRES` | — | PostgreSQL connection |
| `DB_PORT` | no | `5432` | PostgreSQL port |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | with Docker's `db` service | — | Read by the official `postgres` image itself on first run; must match `DB_NAME`/`DB_USER`/`DB_PASSWORD` exactly (same values, different variable names — required by that image's own design). |

`.env`, `.env.docker`, `db.sqlite3`, and `media/` are gitignored.

### Common commands

```bash
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py check
uv run python manage.py test                 # full suite
uv run python manage.py test participants     # one app
```

`accounts/tests.py` and `participants/tests.py` contain real test coverage
(participant password flow, Quran circle point deltas, points‑reset
snapshot history, store management branches, PDF export role scoping, and the
navigation shell). As of 1.2.0 the suite is **37 tests, all passing**.

> **WeasyPrint note:** the PDF export endpoint (`/participants/data/export-pdf/`)
> needs the system libraries WeasyPrint depends on (Pango, Cairo, GObject).
> These are **installed inside the production Docker image** (`Dockerfile`),
> so PDF export works there; a local dev machine without them will only
> break this one endpoint (the import is lazy, inside the view, so the rest
> of the site is unaffected). `static/images/letterhead.png` (the official
> letterhead used as the PDF's page background) is present in the repo. See
> [`docs/known-limitations.md`](docs/known-limitations.md).

## Deployment

The repository ships a full Docker‑based production setup:

| File | Role |
| --- | --- |
| `Dockerfile` | `python:3.12-slim` base, installs WeasyPrint's and psycopg2's native library dependencies, installs deps via `uv sync --frozen --no-dev`. |
| `docker-compose.yml` | Four services: `db` (PostgreSQL 16), `web` (Gunicorn), `nginx` (reverse proxy + static/media serving), `certbot` (Let's Encrypt renewal every 12h). |
| `docker/entrypoint.sh` | Waits for PostgreSQL, runs `migrate` + `collectstatic`, then starts Gunicorn (3 workers, 120s timeout). |
| `docker/nginx/nginx.conf` | HTTP‑only bootstrap config, used before the first TLS certificate exists (serves the ACME HTTP‑01 challenge). |
| `docker/nginx/nginx-ssl.conf` | Full HTTPS config — activated manually after the first certificate is issued. |
| `docker/certbot-init.sh` | One‑time manual script to issue the first Let's Encrypt certificate. |
| `.env.docker.example` | Template production env file — copy to `.env.docker` and fill in real values before `docker compose up`. |

```bash
cp .env.docker.example .env.docker
#    edit .env.docker with real SECRET_KEY, ALLOWED_HOSTS, DB credentials, etc.
docker compose up -d --build
#    first time only, once the stack is reachable over plain HTTP:
./docker/certbot-init.sh
#    then activate HTTPS:
cp docker/nginx/nginx-ssl.conf docker/nginx/nginx.conf
docker compose up -d --force-recreate nginx
```

Notes:
- TLS terminates at Nginx; Gunicorn only sees plain HTTP over the internal
  Docker network. `SECURE_PROXY_SSL_HEADER` in `config/settings.py` trusts
  `X-Forwarded-Proto` from Nginx so Django's CSRF check doesn't reject HTTPS
  POSTs.
- Certificate renewal is automatic, but **Nginx is not reloaded
  automatically after a renewal** — see
  [`docs/known-limitations.md`](docs/known-limitations.md).
- Guides referenced as `دليل_رفع_الاستضافة.md` and `دليل_تحديث_الموقع.md`
  are still **not tracked in this repository**; if the project owner has
  them elsewhere, add them to `docs/` to make them an actual reference.

Full technical breakdown: [`docs/architecture.md`](docs/architecture.md#النشر-الإنتاجي-docker).

## Project layout

```
config/         Django project package (settings, urls, wsgi/asgi)
accounts/       Identity & authentication — custom User, Role, auth backend, middleware
participants/   Program data — Group, Participant, attendance (3 kinds), tasks, store, points ledger, dashboards
templates/      Shared templates (public home page, Unfold admin overrides)
static/         Logo, PDF letterhead, Tajawal font, admin RTL stylesheet, and the Excel import template
locale/         Arabic catalog for django-unfold's own strings only
docker/         Production deployment: entrypoint script, Nginx configs, certbot init script
docs/           Detailed technical documentation (Arabic)
```

Dependency direction is always `participants → accounts`, never the reverse.

## Documentation

All docs are in Arabic, under [`docs/`](docs/):

| File | Contents |
| --- | --- |
| [`docs/README.md`](docs/README.md) | Index — start here. |
| [`docs/known-limitations.md`](docs/known-limitations.md) | **Read first.** What is *not* built or automated, plus a section recording every constraint that has since changed. |
| [`docs/architecture.md`](docs/architecture.md) | App split, dependency direction, presentation layer, Docker setup, migration history. |
| [`docs/roles-and-permissions.md`](docs/roles-and-permissions.md) | The four roles and a per‑view permission matrix taken from each `test_func()`. |
| [`docs/points-system.md`](docs/points-system.md) | The three currencies, `apply_points_delta`, and the exact value of every point source. |
| [`docs/models.md`](docs/models.md) | Every model field by field, constraints, `related_name`s, admin registration. |
| [`docs/authentication.md`](docs/authentication.md) | Auth backend, forced password setup, supervisor‑approved reset. |
| [`docs/features.md`](docs/features.md) | Every feature mapped to its view + template, and the full URL table. |
| [`docs/weekly-tasks.md`](docs/weekly-tasks.md) | **New in 1.2.0.** Task lifecycle, `get_active_tasks()`, AND/OR submission formats with examples, validation rules, and what was retired. |
| [`docs/navigation.md`](docs/navigation.md) | **New in 1.2.0.** Sidebar/drawer structure, per‑role menus, badge logic, and how to add a new page to the navigation. |
| [`docs/deployment.md`](docs/deployment.md) | **New in 1.2.0.** Deploy/update runbook: Docker, SSL, **backup before every `migrate`**, and data‑compatibility checks before sensitive migrations. |

See [`CHANGELOG.md`](CHANGELOG.md) for the release history.

## License / ownership

Developed for **Saqeel Society for Youth Development** (جمعية صقيل لتنمية
الشباب) for Ufuq Program. No open‑source license file is present in the
repository; all rights are held by the association unless stated otherwise.
