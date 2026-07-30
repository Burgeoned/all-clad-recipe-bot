# Recipe Pipeline — Design

An automation pipeline over the **"all-clad chad"** Google shared drive. It watches
`recipes_to_input/` for recipe files (`.txt` / `.pdf` / `.docx`), uses **Claude** to
normalize *and categorize* each into a structured schema, fills the drive's
**`recipe_template.docx`**, optionally exports a **PDF**, saves the result into the recipe's
**category folder**, and moves the original source into `completed/`.

The filled **`.docx` is the primary artifact** — it preserves the designed layout and stays
editable (user adds photos + "notes for next time" later). PDF is a derived export.

Status: **Implemented and running.** This document records the design and rationale; see
[README.md](README.md) for setup and usage. ("all-clad chad" is the author's private folder
name — the code refers to it only as the configurable root folder.)

---

## 1. Decisions (locked)

| Area | Decision | Rationale |
|---|---|---|
| Auth | **OAuth as a dedicated `recipe-bot` account** | It's a personal (consumer) My Drive — service accounts can't own files there (upload quota fails). A throwaway bot account, shared into just the folder, runs OAuth and keeps blast radius to that one folder. |
| Parser | **Claude LLM** | Source files are inconsistent; LLM normalizes robustly into one schema. |
| Categorize | **Claude picks 1 of 7** | `breakfast/beef/dessert/pork/poultry/seafood/other`. Drives output-folder routing. |
| Trigger | **Scheduled poll (daily)** | GitHub Actions cron; recipes are sparse (few/week, often 0), so daily is plenty. |
| Output | **Fill `recipe_template.docx` → optional PDF** | Pixel-exact to your design; stays editable. PDF via LibreOffice headless. |
| Template source | **Read from Drive at runtime** | `recipe_template.docx` is the single source of truth; restyle in Drive, no repo change. |
| Output routing | **Category folder** | Finished template lands in its category folder. |
| Source disposal | **Move to `completed/`** | Processed inputs are archived; input folder stays a clean queue. |
| Photos | **Blank labeled placeholders** | No image handling in v1; user inserts images into the docx later. |

---

## 2. Open questions

**None blocking.** Template filling is by **structural / known-placeholder targeting** with
`recipe_template.docx` left untouched (won't be restyled) — see §4.2.

*(Resolved: consumer My Drive → **OAuth as a `recipe-bot` account** (§10); template filling =
structural, no token injection; volume ≈ a few/week, often 0 → **Haiku**, **daily** poll,
`max_files_per_run=10`; deps signed off; plus parser, output format, per-category routing,
`completed/` archive, `other` fallback, template-from-drive.)*

---

## 3. Architecture

### 3.0 Drive layout & routing

```
all-clad chad/            ← My Drive folder = ROOT_FOLDER_ID (the one configured id)
├── breakfast/ beef/ dessert/ pork/ poultry/ seafood/   ← OUTPUT by category
├── other/                  ← misfit category (created if missing)
├── claude/                 ← the bot's working folders live here
│   ├── recipes_to_input/    ← INPUT: watched every run
│   └── completed/           ← source files moved here after success
├── Recipe_Template.docx    ← template, fetched at runtime (case-insensitive match)
└── Kitchen Equipment List  ← ignored in v1 (see note)
```

- **Resolution by name** (case-insensitive), all under `ROOT_FOLDER_ID` — the only id you
  configure. Category folders, `other`, and the template are **direct children of root**;
  the two working folders (`recipes_to_input`, `completed`) live under the **`claude/`**
  working subfolder. The resolver tolerates either location (root or `claude/`) for the
  working folders, so this stays robust if they move.
- Adding a new category later = create the folder in Drive **and** add it to `Category`.
- `Kitchen Equipment List` is **not** used in v1. Later, cross-checking a recipe's equipment
  against what you own is a clean follow-up.

### 3.1 Data flow

```
   recipes_to_input/  ──1. list──▶  for each file:
                                      │
   ┌──────────────┐  2. download   ┌──────────────┐  3. extract  ┌──────────────┐
   │ drive_client │───────────────▶│  raw bytes   │─────────────▶│  extractors  │
   └──────────────┘                └──────────────┘              └──────┬───────┘
                                                                        │ raw text
                                                                        ▼
   ┌──────────────┐                ┌──────────────────────────┐  4. structure + classify
   │ drive_client │  fetch once    │          parser          │◀───────(Claude)
   │ .fetch_      │───template────▶│  → Recipe (incl. category)│
   │  template()  │    bytes       └─────────────┬────────────┘
   └──────────────┘                              │ Recipe
                                                 ▼
   ┌──────────────┐  6. upload to   ┌──────────────────────────┐  5. render
   │ drive_client │◀──category──────│         renderer         │◀── fill template.docx
   │              │   folder        │  → RenderedDocs(docx[,pdf])│    [+ soffice → pdf]
   │  7. move src │                 └──────────────────────────┘
   │  → completed/│
   └──────────────┘
```

### 3.2 Modules (one job each)

| Module | Responsibility | Key inputs → outputs |
|---|---|---|
| `config.py` | Load & validate runtime config from env | env → `Settings` (frozen dataclass) |
| `models.py` | Data contracts | `Recipe`, `Category`, `SourceFile`, `RenderedDocs`, `ProcessResult` |
| `drive_client.py` | All Drive I/O | list / download / fetch template / resolve category folders / upload / move |
| `extractors.py` | Bytes → plain text, per file type | `SourceFile` → `str` |
| `parser.py` | Plain text → structured + categorized recipe via Claude | `str` → `Recipe` |
| `renderer.py` | Fill template from `Recipe`; optional PDF export | `Recipe` + template bytes → `RenderedDocs` |
| `pipeline.py` | Orchestration, queue handling, error isolation | run() → list[`ProcessResult`] |
| `__main__.py` | CLI entrypoint (`python -m recipe_pipeline`) | argv → exit code |

Dependency direction is one-way: `pipeline` depends on everything; nothing depends on it.
`models`/`config` depend on nothing internal.

### 3.3 Idempotency / queue model — **move-based, stateless**

The `completed/` move *is* the processed-marker, so no local state and no `appProperties`:

- A file in `recipes_to_input/` = not yet done. Process it.
- **On success:** upload output to the category folder, then move the source to `completed/`.
- **On failure:** leave the source in `recipes_to_input/`; it's retried next run and recorded
  as `FAILED` this run.
- **Edge case:** if upload succeeds but the move fails, the next run reprocesses and may
  create a duplicate output. Accepted for v1; mitigated by doing the move immediately after a
  confirmed upload. (If duplicates ever bite, we add an output-exists check — not needed now.)

---

## 4. Interface contracts

> Contracts frozen before implementation. **`Recipe` uses Pydantic** (LLM output needs
> validation + JSON); config/transport structs use `@dataclass`. This is the piece most
> worth editing now — flag any field changes.

### 4.1 Data models

```python
# models.py
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from pydantic import BaseModel, Field


class Category(str, Enum):
    BREAKFAST = "breakfast"
    BEEF = "beef"
    DESSERT = "dessert"
    PORK = "pork"
    POULTRY = "poultry"
    SEAFOOD = "seafood"
    OTHER = "other"        # fallback; folder auto-created if missing


class Ingredient(BaseModel):
    quantity: str | None = Field(None, description="e.g. '2', '1 1/2', None if 'to taste'")
    unit: str | None = Field(None, description="e.g. 'cup', 'g', 'tbsp'")
    item: str = Field(..., description="e.g. 'all-purpose flour'")
    note: str | None = Field(None, description="e.g. 'sifted', 'room temperature'")


class IngredientGroup(BaseModel):
    heading: str | None = None      # sub-components: "For the sauce", "For the dough"
    ingredients: list[Ingredient]


class Nutrition(BaseModel):
    # Per serving. Strings so we can carry units or leave blank.
    calories: str | None = None
    protein: str | None = None
    carbs: str | None = None
    fat: str | None = None
    fiber: str | None = None
    sodium: str | None = None


class Recipe(BaseModel):
    # --- header ---
    title: str
    description: str | None = None
    category: Category = Field(..., description="Claude classifies into one of the seven")
    # --- at a glance ---
    prep_time_min: int | None = None
    cook_time_min: int | None = None
    total_time_min: int | None = None
    servings: str | None = Field(None, description="e.g. '10' or 'Makes 12 muffins'")
    # --- metadata line ---
    cuisine: str | None = None
    course: str | None = None
    difficulty: str | None = None
    dietary_tags: list[str] = Field(default_factory=list)
    source: str | None = Field(None, description="e.g. 'Custom R&D Recipe', a URL, a book")
    # --- body ---
    ingredient_groups: list[IngredientGroup]
    substitutions: list[str] = Field(default_factory=list)
    equipment: list[str] = Field(default_factory=list)
    steps: list[str] = Field(..., description="Ordered; each is one instruction")
    tips: list[str] = Field(default_factory=list, description="Tips & variations")
    nutrition: Nutrition | None = None
    # --- provenance (not user content) ---
    source_filename: str


class FileType(str, Enum):
    TXT = "txt"
    PDF = "pdf"
    DOCX = "docx"


@dataclass(frozen=True)
class SourceFile:
    file_id: str
    name: str
    file_type: FileType
    content: bytes


class Status(str, Enum):
    PROCESSED = "processed"
    FAILED = "failed"


@dataclass
class RenderedDocs:
    docx: bytes                 # filled template — always produced
    pdf: bytes | None = None    # LibreOffice export — only when export_pdf is on


@dataclass(frozen=True)
class DriveLayout:
    # Resolved once per run by listing children of the root "all-clad chad" folder by name.
    input_folder_id: str                    # recipes_to_input
    completed_folder_id: str                # completed
    category_folders: dict[Category, str]   # includes 'other' (auto-created if missing)
    template_file_id: str                   # recipe_template.docx


@dataclass
class ProcessResult:
    source_name: str
    status: Status
    category: Category | None = None
    output_file_id: str | None = None   # uploaded .docx file id
    error: str | None = None
```

**Template chrome rendered but NOT parsed** (static or blank): `PRE-COOK CHECKLIST`
(boilerplate), `NOTES FOR NEXT TIME` (blank), `PHOTOS` (blank placeholders).

**Schema → template section mapping**

| Template section | `Recipe` field(s) |
|---|---|
| Title / description banner | `title`, `description` |
| *(routing, not printed)* | `category` |
| AT A GLANCE grid | `prep_time_min`, `cook_time_min`, `total_time_min`, `servings` |
| Metadata line | `cuisine`, `course`, `difficulty`, `dietary_tags`, `source` |
| INGREDIENTS table | `ingredient_groups[].ingredients[]` → quantity / unit / item / note |
| Substitutions & Notes | `substitutions` |
| EQUIPMENT | `equipment` |
| INSTRUCTIONS (numbered) | `steps` |
| TIPS & VARIATIONS | `tips` |
| NUTRITION (PER SERVING) | `nutrition.*` |

Empty/`None` fields render as em-dash or a hidden row, so partial recipes still look intact.

### 4.2 Config

```python
# config.py
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    # OAuth (bot account) — minted once via local consent, then stored as secrets
    oauth_client_id: str
    oauth_client_secret: str
    oauth_refresh_token: str
    # Drive
    root_folder_id: str           # the "all-clad chad" folder; everything else resolved by name
    anthropic_api_key: str
    model: str = "claude-haiku-4-5-20251001"   # sparse volume → Haiku is plenty
    export_pdf: bool = True       # also export PDF via LibreOffice; off = docx only
    max_files_per_run: int = 10
    dry_run: bool = False         # parse+render, skip all writes (upload + move)
```

Only **one** id to configure (`root_folder_id`). `drive_client` resolves `recipes_to_input`,
`completed`, the category folders, and `recipe_template.docx` by name underneath it, and
auto-creates `other/` if absent. Expected child names are fixed (the six categories +
`recipes_to_input` + `completed` + `recipe_template.docx`).

### 4.3 Module signatures

```python
# drive_client.py  — thin wrapper over google-api-python-client (OAuth bot account)
class DriveClient:
    def __init__(self, settings: Settings) -> None: ...       # builds creds from refresh token
    def resolve_layout(self) -> DriveLayout: ...              # children of root_folder_id, by name
    def list_input_files(self, input_folder_id: str) -> list[SourceFile]: ...  # non-recursive
    def fetch_template(self, template_file_id: str) -> bytes: ...
    def upload_output(self, docs: RenderedDocs, base_name: str, folder_id: str) -> str:
        ...  # uploads <base>.docx (+ <base>.pdf if present); returns .docx file id
    def move_to_completed(self, file_id: str, completed_folder_id: str) -> None: ...

# extractors.py
def extract_text(source: SourceFile) -> str: ...             # dispatch on source.file_type

# parser.py
def parse_recipe(raw_text: str, source_filename: str, settings: Settings) -> Recipe: ...
    # One Claude call, tool/JSON-schema-constrained to Recipe (category included).

# renderer.py
def render(recipe: Recipe, template_bytes: bytes, settings: Settings) -> RenderedDocs: ...
    # Fills the template (python-docx); if settings.export_pdf, shells out to
    # `soffice --headless --convert-to pdf` and attaches pdf bytes.

# pipeline.py
def run(settings: Settings) -> list[ProcessResult]: ...
    # 1) resolve_layout()  2) fetch_template()  3) list_input_files()
    # 4) per file: extract → parse → render → upload(category folder) → move source → result
```

**Template filling (decided):** `renderer` targets slots by **document structure + known
placeholder text** — the table whose header row is `QUANTITY|UNIT|INGREDIENT|NOTES`, the
cells under the AT A GLANCE labels, the paragraphs under each heading, the NUTRITION cells,
etc. **No tokens injected; `recipe_template.docx` stays untouched.** Safe because the
template is fixed (won't be restyled). If a single slot ever proves ambiguous during build,
I'll add a minimal token to just that slot and flag it.

---

## 5. Dependencies — **need your approval**

| Package | Purpose | Notes |
|---|---|---|
| `google-api-python-client` | Drive API (My Drive folder ops) | Official. |
| `google-auth` | Refresh access tokens from the stored refresh token | Runtime auth. |
| `google-auth-oauthlib` | One-time local OAuth consent to mint the refresh token | Only used by `scripts/get_refresh_token.py`, not in CI. |
| `anthropic` | Claude API (parse + classify) | Official SDK. |
| `pydantic` (v2) | `Recipe` validation + JSON | Your convention for validated data. |
| `pypdf` | Extract text from `.pdf` inputs | Light; swap to `pdfplumber` only if scans/columns. |
| `python-docx` | Extract `.docx` inputs **and fill the template** | Double duty. |

**System (non-pip) dep:** **LibreOffice** (`soffice`) for docx→PDF — free, CI-installable,
**only when `export_pdf=True`**. Set it `False` → pure-pip, `.docx`-only output.

No `reportlab`/`weasyprint`: layout lives in `recipe_template.docx`, not code.

*(Scanned-image PDF inputs would need OCR — out of scope for v1.)*

---

## 6. Config & secrets

Env vars (12-factor; no secrets in repo):

```
GOOGLE_OAUTH_CLIENT_ID       # OAuth desktop client (bot account's GCP project)
GOOGLE_OAUTH_CLIENT_SECRET
GOOGLE_OAUTH_REFRESH_TOKEN   # minted once via scripts/get_refresh_token.py
ROOT_FOLDER_ID               # the "all-clad chad" folder id (from its Drive URL)
ANTHROPIC_API_KEY
RECIPE_MODEL                 # optional override
EXPORT_PDF                   # optional, "0" to output .docx only (skip LibreOffice)
DRY_RUN                      # optional, "1" to skip writes
```

`.env.example` committed; real `.env` gitignored. In GitHub Actions these come from repo
**Actions secrets** (private even in a public repo; unavailable to fork PRs). Two helper
scripts: `scripts/get_refresh_token.py` (one-time local consent → refresh token) and
`scripts/list_drive_ids.py` (prints the resolved layout so you can sanity-check names).

---

## 7. Error handling (per-file isolation)

- One bad file must **not** abort the batch. `pipeline.run` wraps each file; failures →
  `ProcessResult(status=FAILED, error=...)`, logged, source left in place for retry.
- Specific exceptions only (no bare `except`): `HttpError` (Drive), `APIError` (Anthropic),
  `ValidationError` (Pydantic), extractor/`soffice` errors.
- Claude output failing `Recipe` validation → one bounded retry with the error fed back,
  then FAILED.
- **Not a recipe:** the parser offers a `not_a_recipe` tool alongside `save_recipe`
  (`tool_choice: "any"`). If Claude judges the input isn't a recipe (invoice, notes, garbage,
  or absurdly long text), it's `REJECTED` and moved to `rejected/` — so it's never filed as a
  fake recipe and never re-parsed on later runs (which would waste tokens). Distinct from
  FAILED, which leaves the file queued for retry.
- **Move happens only after a confirmed upload** (see §3.3 edge case).
- Exit code = number of FAILED files (0 = clean), so CI surfaces problems.

---

## 8. Proposed repo layout

```
recipe-pipeline/
├── DESIGN.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── .github/workflows/run.yml        # scheduled poll (installs LibreOffice)
├── scripts/
│   ├── get_refresh_token.py         # one-time: local OAuth consent → refresh token
│   └── list_drive_ids.py            # one-off: print the resolved layout to sanity-check
├── src/recipe_pipeline/
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── models.py
│   ├── drive_client.py
│   ├── extractors.py
│   ├── parser.py
│   ├── renderer.py
│   └── pipeline.py
└── tests/
    ├── fixtures/                    # sample recipes + a copy of the template for offline tests
    ├── test_extractors.py
    ├── test_parser.py
    ├── test_renderer.py
    └── test_pipeline.py
```

---

## 9. Build order (once approved)

0. Use a local copy of the template structure (Yun's `Lu_Rou_Fan.docx` mirrors it) as the
   renderer dev fixture — no template edits needed.
1. `models.py` + `config.py` (contracts).
2. `extractors.py` (pure, offline-testable).
3. `renderer.py` (fill template → docx; wire PDF export; iterate on the filled Lu Rou Fan).
4. `parser.py` (Claude parse + classify; test on your Lu Rou Fan + a few real files).
5. `drive_client.py` (OAuth bot creds + the real "all-clad chad" folder; name resolution, move).
6. `pipeline.py` wiring + queue/error handling.
7. `.github/workflows/run.yml` scheduling.
8. Tests per module, then README.

---

## 10. Security & open-source safety (public GitHub repo)

The repo is public, so **nothing sensitive is committed**. Enforced by:

- **Secrets only via env / GitHub Secrets.** No SA key, API key, or drive/folder ids in
  code — `config.py` reads everything from the environment.
- **`.gitignore`** covers `.env`, credential keys (`service_account*.json`,
  `credentials*.json`, `token*.json`), `__pycache__/`, build artifacts, and local
  `output/` / `scratch/` dirs, so real recipes/outputs never land in git.
- **`.env.example`** ships placeholders only — the setup contract, no real values.
- **Drive/folder ids live in env, not code.** Not passwords, but keeping them out avoids
  publishing your drive's structure.
- **Test fixtures are synthetic** — a throwaway sample recipe + a stripped template copy,
  nothing personal.
- **Least privilege via the bot account.** OAuth runs as a throwaway `recipe-bot` account
  whose *only* Drive access is the `all-clad chad` folder you shared to it. Even with the
  full `drive` scope, a leaked refresh token can reach nothing but that folder — your real
  Google account is never in the loop. Revoke instantly by un-sharing the folder or removing
  the bot's access at the account's security page.
- **Refresh token care.** It's the one real secret: minted locally (never printed to logs),
  stored only in `.env` (gitignored) and GitHub Actions secrets. To stop Google's 7-day
  expiry on tokens from "Testing" OAuth apps, the bot's consent screen is published to
  **Production** (the "unverified app" warning is expected and fine for self-use).
- **Optional belt-and-suspenders:** a `gitleaks` pre-commit hook + GitHub secret-scanning to
  block an accidental key commit. Adds one dev-only dependency — your call.

## 11. What I need from you to proceed

**You (one-time Google setup — I'll give exact click-paths):**
1. Create the `recipe-bot` Google account; share the `all-clad chad` folder to it as Editor.
2. In a Google Cloud project on that account: enable the Drive API, create an **OAuth
   Desktop client**, and publish the consent screen to **Production** (scope: `drive`).
3. Send me: the **client id + secret**, and the **`all-clad chad` folder id** (from its URL).
   *Not* your password or the JSON — just those three strings (client secret is low-risk but
   still paste it privately). I'll wire `scripts/get_refresh_token.py` so you mint the
   refresh token locally yourself.

**Me (in parallel, no drive access needed):** `models.py`, `extractors.py`, and `renderer.py`
against your local `Lu_Rou_Fan.docx`.

**Then, together:** the §4.2 template-anchor decision (blocks finishing `renderer`).

*(Volume, deps, output format, routing, and auth are all settled.)*
```