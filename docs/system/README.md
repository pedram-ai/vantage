# System documentation

*What Argent Ridge does, how each part works, and the rules it upholds.*

**Applies to build:** `0.002` · **Last reviewed:** 2026-09-20

---

This is the functional single source of truth. It lives in the repo beside the code and ships
inside the same container image, so it **cannot drift from what is running** — the Documentation
page reads these files off disk at request time and stamps them with the build that served them.

> **Invariant:** any behaviour change updates the affected document **in the same commit** as the
> code. A change that is not written down did not happen.

## The documents

| # | Document | Answers |
|---|---|---|
| [01](./01-overview.md) | **Overview** | What this is, who it is for, what it deliberately is not |
| [02](./02-architecture.md) | **Architecture** | Every moving part and how a request flows through them |
| [03](./03-authentication.md) | **Authentication & access** | Who can sign in, how sessions work, why there is no sign-up |
| [04](./04-market-data.md) | **Market data** | Where prices come from, why Yahoo needs a browser fingerprint |
| [05](./05-instruments-and-sessions.md) | **Instruments & sessions** | Why a futures window is wrong for a stock |
| [06](./06-profiles-and-zones.md) | **Profiles & zones** | The POC/VAH/VAL math and how an action map is built |
| [07](./07-portfolio.md) | **Portfolio** | Trades, cash, and the two ways to compute P&L |
| [08](./08-performance-and-charts.md) | **Performance & charts** | Periods, the read cache, and what each chart may not hide |
| [09](./09-admin-console.md) | **Admin console** | Every admin page, what it shows, who can reach it |
| [10](./10-email.md) | **Email** | The weekday briefing, SES, and the sent archive |
| [11](./11-deploy-and-versioning.md) | **Deploy & versioning** | How a change reaches production and how you know it did |
| [12](./12-testing.md) | **Testing** | The nine suites, and why a green typecheck proves nothing |
| [13](./13-research.md) | **Research** | Substack in, a read on SPY out, and what keeps it honest |
| [14](./14-spy-conversion.md) | **SPY conversion** | Why the model never does the arithmetic, and why the ratio is dated |

## How to read a document

Each one carries the build it was last reviewed against. Rules appear as blockquotes:

> **Invariant:** a statement the code is expected to uphold. If you change behaviour so that an
> invariant no longer holds, the invariant is what you update first — then the code, then the test.

## The change log

Separate from these documents and generated rather than written: **every commit**, baked from
`git log` at build time by `scripts/gen_build_info.py`. See [11 — Deploy & versioning](./11-deploy-and-versioning.md).
