# Setup

## What you need

- Python 3.11 or newer (`python3 --version`)
- Nothing else. No Node, no build step, no external database.

Postgres is optional — the app runs on SQLite out of the box.

---

## Local, five minutes

```bash
git clone https://github.com/mohdabrarbaloch-arch/day-6-markaz.git
cd day-6-markaz

python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env
```

Set a signing key. If you skip this the app generates a random one at startup and logs a warning — which works, but every restart invalidates every session, so you'll be re-logging-in constantly.

```bash
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
```

Run it:

```bash
uvicorn app.main:app --reload
```

- App: <http://127.0.0.1:8000>
- API docs: <http://127.0.0.1:8000/api/docs>
- Health: <http://127.0.0.1:8000/api/health>

Register an account on the login screen. There is no seed user and no default password — the first account you create is yours.

---

## With Docker

```bash
docker compose up --build
```

That starts Postgres 16 and the app, waits for the database to report healthy, then serves on port 8000. Data lives in the `pgdata` volume, so `docker compose down` keeps it and `docker compose down -v` doesn't.

`docker compose logs -f app` if it doesn't come up.

---

## With Postgres but no Docker

```bash
createdb markaz
pip install "psycopg[binary]==3.2.3"
```

Then in `.env`:

```
DATABASE_URL=postgresql+psycopg://youruser@localhost:5432/markaz
```

The `psycopg` driver is not in `requirements.txt` because most local runs use SQLite and there's no reason to make everyone install a Postgres client. Add it when you need it.

---

## Tests, linting, formatting

```bash
pytest              # 76 tests
ruff check .        # lints clean
black --check .     # format check
black .             # format in place
```

The test suite sets `BCRYPT_ROUNDS=4` internally. At the production cost of 12, hashing dominates the runtime and 76 tests take about 25 seconds instead of one.

Tests use a temporary SQLite file, created fresh per session, and never touch your `markaz.db`.

---

## Deploying

### Vercel

The repo already has `vercel.json` and `api/index.py`.

```bash
npm i -g vercel
vercel --prod --yes --token "$VERCEL_TOKEN"
```

Two things to do in the Vercel dashboard first, because neither can be done from a config file:

1. **Set `SECRET_KEY`** in Project → Settings → Environment Variables. Without it, each serverless instance generates its own key, and a JWT signed by one instance is rejected by the next. This is the single most common reason a serverless deploy "works but logs everyone out".
2. **Set `DATABASE_URL`** to a real Postgres instance (Neon, Supabase, RDS). Vercel's filesystem is read-only and ephemeral, so SQLite there loses data on every cold start.

Current status: **not deployed.** This sandbox has no `VERCEL_TOKEN`, so there is a working repo and no live URL rather than a URL that doesn't answer.

### Any VPS

```bash
git clone <repo> && cd day-6-markaz
python3 -m venv /opt/markaz && /opt/markaz/bin/pip install -r requirements.txt
cp .env.example /opt/markaz/.env && $EDITOR /opt/markaz/.env   # set SECRET_KEY
```

Then a systemd unit:

```ini
[Unit]
Description=Markaz
After=network.target

[Service]
User=markaz
WorkingDirectory=/opt/markaz/app
EnvironmentFile=/opt/markaz/.env
ExecStart=/opt/markaz/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

[Install]
WantedBy=multi-user.target
```

Put nginx or Caddy in front of it. If you do, `X-Forwarded-For` reaches the app and rate limiting keys on the real client address; without a proxy that header is client-controlled and the limiter becomes advisory.

---

## Troubleshooting

**`AttributeError: module 'bcrypt' has no attribute '__about__'`**
You're on `passlib` with bcrypt 4.x. This project calls `bcrypt` directly precisely to avoid that, so if you see it, something reinstalled `passlib` — check `pip list`.

**`422` when registering**
`EmailStr` rejects reserved TLDs including `.test` and `.local`. Use a real-looking domain. If you want to allow them in your own fork, relax the validator in `app/schemas.py`.

**Everyone logged out after a restart**
`SECRET_KEY` isn't set, so a new random key was generated. See above.

**`sqlite3.OperationalError: database is locked`**
Shouldn't happen — WAL is enabled and the busy timeout is 30s. If it does, you're probably running two processes against the same file. Give each its own `DATABASE_URL`.
