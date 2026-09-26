# 🛒 Shropping List

A **sh**ared sh**opping list** for your household. Self-hosted, simple, and private.

Everyone in the house gets their own login, adds things to one shared list, and can see who added what. When the shopping's done, hit **Shop complete**: the list is saved to history and a fresh one starts.

## Features

- **One shared list** for the whole household, refreshed on everyone's device every few seconds
- **Items with a count**: type "eggs" with 12. Adding something that's already on the list bumps its count instead of duplicating it
- **Separate accounts** so you can see who added each item and who ticked it off
- **Shop complete** saves the list to history and starts a new one. Anything not ticked off moves to the next shop automatically, or tick "Mark all items as completed" to count everything as bought
- **History** of every past shop: what was bought, what wasn't, and who did the shop
- **Household admin**: add members, reset passwords, disable accounts. No public sign-up
- Works nicely on phones and can be added to your home screen
- A single container with a single SQLite file. No external database needed

## Quick start (Docker)

```bash
git clone https://github.com/ZayanMA/shropping-list.git
cd shropping-list
docker compose up -d
```

Open `http://<your-server>:8000`. The first visit asks you to create the admin account. Then go to **Household → Add a member** for everyone else.

Your data lives in `./data/shoplist.db`. Back up that file and you've backed up everything.

## Running without Docker

Needs Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn shoplist.main:app --host 0.0.0.0 --port 8000
```

## Configuration

| Environment variable       | Default              | What it does |
|----------------------------|----------------------|--------------|
| `SHOPLIST_DB`              | `data/shoplist.db`   | Where the SQLite database is stored |
| `SHOPLIST_SECURE_COOKIES`  | `false`              | Set to `true` when serving over HTTPS so login cookies are only sent over HTTPS |

## Accessing it from outside your home

The app has its own logins, but **it should still be served over HTTPS** if it's reachable from the internet. Some easy options:

- **Tailscale**: only your household's devices can reach it; no ports opened
- **Cloudflare Tunnel**: HTTPS on your own domain without opening ports
- **A reverse proxy** (Caddy, Traefik, nginx) with a Let's Encrypt certificate

Then set `SHOPLIST_SECURE_COOKIES=true`.

## Why separate logins instead of one shared password?

A shared password is quicker to set up, but you can't tell who added "20 × chocolate". Separate accounts give you attribution in the list and in history, and let you remove someone (a lodger who moves out, say) without changing the password for everyone.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
.venv/bin/uvicorn shoplist.main:app --reload
```

- `shoplist/main.py`: API routes
- `shoplist/db.py`: SQLite schema and helpers
- `shoplist/auth.py`: password hashing (scrypt), sessions, login rate limiting
- `shoplist/static/`: the frontend (plain HTML/CSS/JS, no build step)

## Roadmap

- [ ] **Supermarket helpers (Tesco, Sainsbury's, …)**: neither offers a public API for placing orders, so the realistic first step is a "shop at…" button that opens each item as a search on the supermarket's website, making it quick to fill a basket
- [ ] Live updates via server-sent events instead of polling
- [ ] Categories / aisles to group items
- [ ] Frequently-bought suggestions from history
- [ ] Multiple lists (e.g. a separate one for the chemist)

## License

[MIT](LICENSE)
