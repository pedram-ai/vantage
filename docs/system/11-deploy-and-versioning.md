# 11 — Deploy & versioning

*How a change reaches production, and how you know it got there.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

## 1. Versioning

**`VERSION` at the repo root**, stepped by **0.001** per deploy by `scripts/bump_version.py`.
The footer of every page reads `V0.002 · deployed Sep 20, 2026 · 12:10 AM PT · <sha>`.

> **Invariant:** `VERSION` is **committed**. `app/build_info.json` is gitignored, so a version
> living only there would reset to 0.001 on any clean checkout and two different deploys would
> claim the same number. A version that repeats makes "did my fix ship?" unanswerable.

> **Invariant:** the number is stored and incremented as an **integer of thousandths**. Float
> steps of 0.001 accumulate binary error and reach `0.029000000000000002` — proven in
> `tests/test_platform.py`, which runs both and compares.

> **Invariant:** `deploy.sh` bumps **before** baking the build info. `gen_build_info.py` reads
> `VERSION`, so bumping afterwards ships the previous number and the footer claims a build that
> is not live.

> **Invariant:** with no build info the footer reads **"dev build"**, never a guessed version.
> A wrong version number is worse than an absent one — it is the thing you check.

## 2. Times are Pacific

> **Invariant:** every timestamp shown to the operator is Pacific. Container clocks and GCP APIs
> are UTC; that is an implementation detail, not something to subtract seven hours from. Storage
> stays UTC; `core/version.py` converts at render.

## 3. Deploying

```
bash scripts/deploy.sh
```

Bumps the version, bakes the build info, deploys the Cloud Run service from source, then repoints
the daily job at the new image.

> **Invariant:** the job image is updated in the same script. A service on a new image and a job
> on an old one is a split-brain that shows up only when the job next fires — on a weekday
> morning, unattended.

### ⛔ `--set-env-vars` replaces the entire environment

> **Invariant:** every environment variable the service needs is listed in `deploy.sh`. A variable
> omitted from that line is **deleted** from the service, not left alone.

## 4. The change log

Generated, not written: every commit, baked from `git log` by `scripts/gen_build_info.py` into
`app/build_info.json` and served at Admin → Documentation.

> **Invariant:** "every commit is logged" is therefore **structural**, not a discipline someone
> has to remember.

> **Invariant:** it must be generated **before** the upload, never inside the Dockerfile.
> `.gcloudignore` excludes `.git/`, so a `RUN` step in the image would silently write
> `available: false` and wipe a good log. CI does it; `deploy.sh` does it for manual deploys.

## 5. Documentation shipping

`docs/system/*.md` ships inside the image and is read off disk at request time.

> **Invariant:** any behaviour change updates the affected document **in the same commit** as the
> code, and moves that document's *Applies to build* line.

> ⚠ Finder and iCloud keep regenerating byte-identical `Name 2.md` duplicates. They are deleted
> and also **filtered defensively** in `list_docs()`, because deleting alone has not held.

## 6. Markdown rendering is escaped first

> **Invariant:** repo text is HTML-escaped **before** any formatting is applied, so a document
> containing HTML cannot inject. Slug lookup is exact and confined to the docs directories;
> `../../etc/passwd` and friends are covered by tests.

## 7. Repositories

| Remote | URL |
|---|---|
| `bitbucket` | `git@bitbucket.org:patexia/argentridge.git` — the current home |
| `origin` | `git@github.com:pedram-ai/vantage.git` — the original |

Both are pushed. CI on GitHub deploys via **Workload Identity Federation — no service-account key
exists**, scoped by `attribute.repository_owner=='pedram-ai'`.
