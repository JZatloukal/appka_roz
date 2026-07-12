# Rozanov Sales Manager

Flask aplikace pro sledování prodejů, výzev, XP postupu, odměn a hlasových pochval.

## Lokální spuštění

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

Aplikace používá lokální `.env`, který se neposílá do gitu. Ukázka proměnných je v `.env.example`.

## Důležité proměnné prostředí

- `SECRET_KEY` — dlouhý náhodný secret pro Flask session
- `APP_ENV` — `development` nebo `production`
- `APP_USERNAME` — login jméno
- `APP_PASSWORD` — login heslo
- `APP_DISPLAY_NAME` — zobrazované jméno v aplikaci
- `SESSION_COOKIE_SECURE` — na produkci `true`
- `DATABASE_URL` — Postgres connection string pro produkční databázi

## Data

Lokálně aplikace používá JSON soubory v kořeni projektu. Pro produkci je připravený Postgres migrační skript v `database/`.

## Hlasové nahrávky

Nahrávky jsou ve `static/audio/voice/`. Aplikace je vybírá náhodně po dosažení nastaveného počtu prodejů, bez opakování stejné nahrávky dvakrát za sebou.
