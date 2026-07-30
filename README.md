# all-clad-recipe-bot

An automation pipeline that watches a Google Drive folder for recipe files, uses **Claude**
to parse and categorize each one, fills a designed `.docx` template, and files the result
back into Drive under the right category — then archives the source. Runs on a daily
schedule via GitHub Actions, or on demand.

```
recipes_to_input/  →  extract text  →  Claude (parse + classify)  →  fill recipe_template.docx
                                                                          │
        completed/  ←  move source                    category folder  ←  upload .docx (+ optional PDF)
```

- **Input:** `.txt`, `.pdf`, or `.docx` recipe files (even messy free-text notes) dropped
  into `recipes_to_input/`.
- **Output:** a filled copy of `recipe_template.docx` — the primary, editable artifact —
  optionally exported to PDF, saved into the recipe's category folder
  (`breakfast` / `beef` / `dessert` / `pork` / `poultry` / `seafood` / `other`).
- **Idempotent by design:** a processed source is moved to `completed/`, so the input folder
  is always the work queue. Re-running is safe and produces no duplicates.
- **Safe by design:** never deletes or overwrites anything, uploads are create-only, the bot
  account can only see the one shared folder, and each file is processed in isolation so one
  bad file never affects the others.

See **[DESIGN.md](DESIGN.md)** for the full architecture and the reasoning behind each choice.

## Expected Drive layout

The pipeline resolves everything by name under a single root folder (`ROOT_FOLDER_ID`):

```
<root folder>/
├── breakfast/ beef/ dessert/ pork/ poultry/ seafood/   ← outputs, by category
├── other/                     (auto-created if missing)
├── claude/
│   ├── recipes_to_input/       ← drop recipe files here
│   └── completed/              ← sources move here after processing
└── recipe_template.docx        ← the template that gets filled
```

`recipes_to_input/` and `completed/` may live either directly under the root or inside a
`claude/` subfolder — the resolver handles both.

## Setup

Requires **Python 3.11+**. PDF export additionally requires **LibreOffice** (`soffice` on
PATH); without it, set `EXPORT_PDF=0` to produce `.docx` only.

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -e ".[auth,dev]"
```

> Behind a TLS-inspecting corporate proxy? Add
> `--trusted-host pypi.org --trusted-host files.pythonhosted.org` to the pip command. At
> runtime the app uses [`truststore`](https://pypi.org/project/truststore/) to verify TLS
> against the OS certificate store, so it works through such proxies automatically.

### Credentials (one-time)

The pipeline authenticates to Drive as a dedicated **bot Google account** via OAuth — its
only access is the one folder you share with it.

1. Create the bot account and share the root Drive folder to it as **Editor**.
2. In a Google Cloud project on that account: enable the **Drive API**, create an **OAuth
   Desktop client**, and publish the consent screen to **Production**.
3. Mint a refresh token locally (opens a browser once):
   ```bash
   python scripts/get_refresh_token.py path/to/client_secret_*.json
   ```
4. `cp .env.example .env` and fill in the three printed `GOOGLE_OAUTH_*` values, your
   `ROOT_FOLDER_ID` (from the folder's Drive URL), and `ANTHROPIC_API_KEY`.

Nothing secret is committed — `.env` and all credential files are gitignored.
`scripts/list_drive_ids.py` prints the resolved folder layout as a sanity check.

## Usage

```bash
recipe-pipeline                 # or: python -m recipe_pipeline
```

Processes every new file in `recipes_to_input/`. Environment toggles:

- `DRY_RUN=1` — parse and render, but write nothing back to Drive.
- `EXPORT_PDF=0` — produce `.docx` only (skip the LibreOffice PDF export).
- `RECIPE_MODEL` — override the Claude model (default `claude-haiku-4-5`).

Exit code is the number of files that failed (0 = clean), so a scheduled run surfaces
problems automatically.

## Scheduled automation (GitHub Actions)

`.github/workflows/run.yml` runs the pipeline **daily** (and on manual dispatch, with an
optional dry-run toggle). It installs LibreOffice so scheduled outputs include PDFs.

Add these five repository **Actions secrets** (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` | from the OAuth client |
| `GOOGLE_OAUTH_CLIENT_SECRET` | from the OAuth client |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | minted by `get_refresh_token.py` |
| `ROOT_FOLDER_ID` | the root folder's Drive id |
| `ANTHROPIC_API_KEY` | from console.anthropic.com |

## Tests

```bash
pytest
```

## License

[MIT](LICENSE)
