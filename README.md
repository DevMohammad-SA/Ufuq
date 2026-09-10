# Ufuq (أُفق) — رحّال

Management platform for the **Horizon Program** (برنامج أُفق التنموي), a
14‑week youth development program run by **Saqeel Society for Youth
Development** (جمعية صقيل لتنمية الشباب). Supervisors record daily and weekly
attendance; participants earn a triple‑currency reward and progress toward an
"elite trip" nomination. The end‑user product is branded **رحّال** ("Rahhal").

The entire UI and domain language is **Arabic** (`ar-sa`, `Asia/Riyadh`,
right‑to‑left). This file is in English for GitHub; **`README.ar.md` is the
Arabic version.** Detailed technical docs are in [`docs/`](docs/).

> ⚠️ The previous version of this README described participant login as
> "passwordless". **That design was fully retired** — participants now
> authenticate with a real password. See
> [`docs/authentication.md`](docs/authentication.md).

## Overview

- Participants belong to an **environment** (بيئة / `Group`) led by one group
  supervisor.
- As supervisors record attendance, participants earn a triple currency:

  | Currency | Field | Rule |
  | --- | --- | --- |
  | Points (النقاط) | `points` | Ranking currency. Resettable program‑wide (a snapshot is saved first). |
  | Miles (الأميال) | `miles` | `points_delta × 10`, applied together. Never reset. |
  | Purchase points (النقاط الشرائية) | `purchase_points` | Spent in the store. Never reset by the reset action. |

  All balances are clamped at zero. Conversion lives in one helper,
  `apply_points_delta` (`participants/views.py`).

- **Point sources** (values taken verbatim from `participants/views.py`):
  weekly gathering = **8** (+**2** early bonus); Quran circle = **3** attended
  + **2** achieved (independent); accepted weekly task = **10**.
  Full details: [`docs/points-system.md`](docs/points-system.md).

## Roles

Defined in `accounts/models.py` (`Role`):

| Role | Arabic | Summary |
| --- | --- | --- |
| `participant` | مشارك | Logs in with **national ID + password**. Personal dashboard, weekly task upload, store. |
| `group_supervisor` | مشرف بيئة | Records weekly‑gathering and Quran‑circle attendance for **their own environment only**, views their participants' data. |
| `general_supervisor` | مشرف عام | Program‑wide: Excel import, weekly tasks, store management, points reset, all environments. |
| `superadmin` | مشرف النظام | Same permissions as general supervisor in every view, plus Django admin (`is_staff`/`is_superuser`). |

Full permission matrix: [`docs/roles-and-permissions.md`](docs/roles-and-permissions.md).

## Tech stack

From `pyproject.toml` (`requires-python = ">=3.12"`):

- [Django](https://www.djangoproject.com/) `>=6.0,<6.1`
- [django-unfold](https://unfoldadmin.com/) `>=0.104.1` — themed admin
- [django-environ](https://django-environ.readthedocs.io/) `>=0.14` — `.env` settings
- [openpyxl](https://openpyxl.readthedocs.io/) `>=3.1.5` — Excel participant import
- [Pillow](https://python-pillow.org/) `>=12.3` — uploaded‑image compression
- [WeasyPrint](https://weasyprint.org/) `>=70.0` — participant‑roster PDF export
- [uv](https://docs.astral.sh/uv/) — dependency & environment management
- **Chart.js 4** — loaded from CDN in the general‑supervisor dashboard only
- **Database:** SQLite (`db.sqlite3`) — the only database configured in the
  repository. See [Deployment](#deployment).

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

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SECRET_KEY` | yes | — | Django secret key |
| `DEBUG` | no | `False` | Debug mode |

`.env`, `db.sqlite3`, and `media/` are gitignored.

### Common commands

```bash
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py check
uv run python manage.py test                 # full suite
uv run python manage.py test participants     # one app
```

> **WeasyPrint note:** the PDF export endpoint (`/participants/data/export-pdf/`)
> needs the system libraries WeasyPrint depends on (Pango, Cairo, GObject),
> which are **not** installed by `uv sync`. It also expects
> `static/images/letterhead.png`, which is not currently in the repo. The
> import is lazy, so the rest of the site is unaffected. See
> [`docs/known-limitations.md`](docs/known-limitations.md).

## Deployment

The repository does **not** contain a production configuration: no
`Dockerfile`, `docker-compose.yml`, Nginx/Gunicorn config, `STATIC_ROOT`,
`ALLOWED_HOSTS`, or a PostgreSQL setting — `config/settings.py` ships with
SQLite only.

Hosting / update procedures are maintained by the project owner **outside this
repository** (guides referenced as `دليل_رفع_الاستضافة.md` and
`دليل_تحديث_الموقع.md` are not tracked here). When they are added, link them
from this section. See [`docs/known-limitations.md`](docs/known-limitations.md)
§7 for the full list of what is missing for production.

## Project layout

```
config/         Django project package (settings, urls, wsgi/asgi)
accounts/       Identity & authentication — custom User, Role, auth backend, middleware
participants/   Program data — Group, Participant, attendance, tasks, store, dashboards
templates/      Shared templates (public home page)
static/         Logo (logo.png) and the Excel import template
docs/           Detailed technical documentation (Arabic)
```

Dependency direction is always `participants → accounts`, never the reverse.

## Documentation

Start with [`docs/README.md`](docs/README.md). Most important for anyone
picking up maintenance: **[`docs/known-limitations.md`](docs/known-limitations.md)**.

## License / ownership

Developed for **Saqeel Society for Youth Development** (جمعية صقيل لتنمية
الشباب) for the Horizon Program. No open‑source license file is present in the
repository; all rights are held by the association unless stated otherwise.
