# AGENT TASK — Free local run mode (Windows) + cost optimizations

> **Dành cho chủ dự án (đọc phần này thôi):** đây là bản giao việc cho agent AI
> (Antigravity, Claude Code, Codex…). Mở agent trong thư mục dự án và gõ:
> **"Đọc và làm đúng theo file `docs/AGENT_TASK.md`."**
> Agent sẽ làm Phần 1 và Phần 2. Phần 3 (tiết kiệm chi phí AI) **chỉ làm khi bạn
> đồng ý**, agent sẽ hỏi bạn trước.

---

## 0. How to use this document (agent: read first)

You are an engineering agent continuing work on this repository. This document is
the **complete and binding specification** of your task.

- Read the **whole** document before changing anything.
- Execute phases **in order**: Phase 1 → Phase 2 → (ask owner) → Phase 3.
- Each phase ends with a **Definition of Done (DoD)** checklist. A phase is done only
  when **every** DoD item passes. Do not start the next phase before that.
- Where this document says **MUST / MUST NOT**, there is no discretion.
- If something here conflicts with the code you find, or a step cannot be done as
  written, **stop and report** (section 7). Do not improvise a different design.
- The repository owner is **non-technical** and reads Vietnamese. All owner-facing
  text (Telegram messages are English and stay English; setup guide and your final
  report are Vietnamese) must be simple, short and step-by-step.

---

## 1. Context

**What the project is.** A Telegram bot where the CEO (the owner) asks a question with
`/council <question>`; three AI agents (Codex/OpenAI, Claude/Anthropic,
Gemini/Google) review independently, cross-review, debate (≤ 3 cycles), synthesize,
and the bot posts the result with Approve / Reject / Defer buttons. Decisions are
recorded once, immutably. Read `README.md` and `docs/ARCHITECTURE_v0.2.1.md`.

**Current architecture (as of commit `446785b` on `main`):**

```
Telegram ──HTTPS webhook──▶ council/webhook/handler.py  (FastAPI, POST /webhook)
                              │ record_event()  → telegram_events (dedup by update_id)
                              │ enqueue_update() → job_queue (COMPLETE_ROUND | NOTIFY_CEO)
                              ▼
                         PostgreSQL  (sole state + queue authority)
                              ▲
council/worker/worker.py ─────┘  claims jobs (FOR UPDATE SKIP LOCKED), heartbeat, lease recovery
   └─ council/orchestrator/service.py   job handlers (@register_handler)
        └─ council/orchestrator/engine.py  CouncilEngine (rounds, quorum, debate, synthesis)
```

Key facts you will rely on:

| Item | Where |
|---|---|
| Webhook intake: `_event()`, `verify_ceo()`, `record_event()`, `enqueue_update()`, `handle_webhook()` | `council/webhook/handler.py` |
| Settings (pydantic-settings, reads `.env`) | `council/config.py` |
| Worker loop, `claim_job()`, `process_job()`, `run_worker()`, `load_handlers()` | `council/worker/worker.py` |
| Job handlers `handle_council_command` (COMPLETE_ROUND), `handle_ceo_update` (NOTIFY_CEO) | `council/orchestrator/service.py` |
| `MAX_DEBATE_CYCLES = 3`, `MODERATOR_ID = "codex"`, `CouncilEngine` | `council/orchestrator/engine.py` |
| `AGENT_TIMEOUT_S = 180`, `PROVIDER_RETRIES = 2` | `council/orchestrator/service.py` |
| Telegram output goes through `get_client()` / `set_client()` | `council/telegram/sender.py` |
| Migration runner (must run from repo root; uses `alembic.ini`) | `council/db/migrate.py` |
| Real-PostgreSQL end-to-end tests (opt-in via `TEST_DATABASE_URL`) | `tests/test_postgres_e2e.py` |

**Why this task exists.** The owner needs a **free** way to run the bot. The webhook
needs a public HTTPS URL, which requires a paid server. Telegram also supports
**long polling** (`getUpdates`), which needs no public URL and can run on the owner's
Windows PC. Phase 1 adds polling; Phase 2 makes it runnable on Windows by a
non-technical person; Phase 3 (optional) reduces AI API cost.

---

## 2. Non-negotiable rules (all phases)

1. **Architecture locks** (from `docs/ARCHITECTURE_v0.2.1.md`) MUST be preserved:
   PostgreSQL is the only state and queue; **no Redis, no in-memory queue, no SQLite,
   no second queue source**; CEO is the final authority; decisions and audit log stay
   append-only; idempotency layers stay intact.
2. MUST NOT change `council/db/schema.sql` or `alembic/versions/0001_initial.py`.
   If a schema change is truly required, STOP and report (none is expected).
3. MUST NOT change agent roles, providers, or model ids in `council/db/seeds.sql`.
4. MUST NOT remove or weaken existing tests. Only edit an existing assertion where
   this document explicitly says so.
5. MUST NOT commit secrets. `.env` is git-ignored; keep it that way. Never print API
   keys or tokens in logs, docs, or your report.
6. MUST NOT add new runtime dependencies unless this document names them. (None are
   needed: `python-telegram-bot` already provides `Bot.get_updates` and
   `Bot.delete_webhook`.)
7. Code style: Python ≥ 3.11, `ruff` config in `pyproject.toml` (line length 100),
   type hints, small functions, module docstrings like the existing files.
8. Keep the webhook mode working exactly as before. Polling is an **additional** mode.
9. After every phase run the full verification (section 6). All must pass.
10. Git: work on branch `agent/free-local-mode`. Commit per phase with messages
    `feat(polling): …`, `docs(windows): …`, `perf(cost): …`. Open one pull request
    to `main`. Do not push directly to `main`. Do not force-push.

---

## 3. Phase 1 — Telegram long-polling mode

### 3.1 Goal

Run the bot **without a public URL**: one process polls Telegram for updates, writes
them through the **same intake path** as the webhook, and the existing worker
processes them. Also provide a single command that runs poller + worker together.

### 3.2 Required changes

**A. Extract shared intake (refactor, no behavior change).**
Create `council/webhook/intake.py` containing:

```python
async def ingest_update(update: dict) -> str:
    """Store one Telegram update and queue its job.

    Returns "ignored" (unknown type or not the CEO), "duplicate" (update_id
    already stored) or "queued". Raises on database errors so callers can retry.
    """
```

- Move `_event()`, `verify_ceo()`, `record_event()`, `enqueue_update()` into
  `intake.py` unchanged in behavior. `ingest_update` does exactly what
  `handle_webhook` does today after JSON parsing: classify, check CEO, open
  `get_engine().begin()`, `record_event`, `enqueue_update`, audit log entry
  `TELEGRAM_<EVENT_TYPE>` with actor `CEO`.
- `handler.py` MUST import these from `intake.py` and `handle_webhook` MUST call
  `ingest_update` (secret check and 400 on bad JSON stay in `handler.py`).
- Keep the old names importable from `council.webhook.handler` (re-export) so
  nothing else breaks.

**B. Add the poller.** Create `council/telegram/poller.py`:

- `async def poll_forever(stop: asyncio.Event | None = None) -> None`
  1. Require `settings.telegram_bot_token`; if empty, raise `SystemExit` with a
     clear English message telling the user to set `TELEGRAM_BOT_TOKEN` in `.env`.
  2. On start call `bot.delete_webhook(drop_pending_updates=False)` (Telegram refuses
     `getUpdates` while a webhook is set). Log one INFO line saying polling started.
  3. Loop until `stop` is set:
     `bot.get_updates(offset=offset, timeout=settings.telegram_poll_timeout_s,
     allowed_updates=["message", "callback_query"])`.
  4. For each update **in order**: `await ingest_update(update.to_dict())`.
     Only after `ingest_update` returns (any of the three results) set
     `offset = update.update_id + 1`.
  5. If `ingest_update` raises (e.g. database down): log the exception, **do not
     advance the offset past that update**, sleep with backoff (2 s, 4 s, … max
     30 s), retry. Telegram re-delivers it; `telegram_update_id` uniqueness makes
     re-delivery safe.
  6. If `get_updates` raises a network/Telegram error: log WARNING, backoff as above,
     continue. Never crash the loop on transient errors.
- `def main() -> None` running `poll_forever()` with `asyncio.run`, plus
  `if __name__ == "__main__": main()`.
- The `Bot` instance MUST be obtainable through a small factory function
  (e.g. `_make_bot()`) so tests can replace it with a fake.

**C. Add settings** to `council/config.py`:

```python
telegram_poll_timeout_s: int = 30   # long-poll wait; Telegram max is 50
```

and document it in `.env.example` under the Telegram section (commented, with a
one-line explanation).

**D. One-command local runner.** Create `council/run_local.py`:

- `async def run_local() -> None` that configures logging like `run_worker()` and runs
  `run_worker()` and `poll_forever()` concurrently with `asyncio.gather`.
- `main()` + `__main__` guard. Ctrl+C must exit cleanly (no traceback spam): catch
  `KeyboardInterrupt` in `main()` and print one line `Stopped.`.
- Add console scripts in `pyproject.toml`:
  `ai-council-poller = "council.telegram.poller:main"` and
  `ai-council-local = "council.run_local:main"`.

**E. Windows compatibility.** In `run_local.main()` and `poller.main()`, before
`asyncio.run`, on Windows only (`sys.platform == "win32"`) set
`asyncio.WindowsSelectorEventLoopPolicy()` **only if** you verify it is needed for
asyncpg in your environment; otherwise leave the default. Document your finding in
the PR description either way.

**F. Tests** — create `tests/test_polling.py`:

1. Unit (no DB): fake bot returning two batches of updates; monkeypatch
   `ingest_update` with a recorder → asserts updates ingested in order, offsets
   passed to `get_updates` are `None`, then `last_id + 1`; `delete_webhook` called
   once; loop stops when `stop` event is set.
2. Unit (no DB): `ingest_update` fake raises once then succeeds → the same update is
   retried and the offset does not skip it (patch `asyncio.sleep` to avoid waiting).
3. DB (skipped unless `TEST_DATABASE_URL`, reuse fixtures/helpers style of
   `tests/test_postgres_e2e.py`): feeding the same `/council …` update twice through
   `ingest_update` yields exactly one `job_queue` row and returns
   `"queued"` then `"duplicate"`; a non-CEO update returns `"ignored"` and creates
   nothing.

### 3.3 Definition of Done — Phase 1

- [ ] `council/webhook/intake.py`, `council/telegram/poller.py`, `council/run_local.py` exist as specified.
- [ ] `handle_webhook` uses `ingest_update`; all webhook tests still pass unchanged.
- [ ] `python -m council.telegram.poller` and `python -m council.run_local` start, and without a token print the clear `TELEGRAM_BOT_TOKEN` message and exit with non-zero code.
- [ ] `tests/test_polling.py` exists; all its tests pass.
- [ ] Full verification (section 6) passes, including the PostgreSQL suite.
- [ ] README "Quick Start" mentions the polling alternative in ≤ 6 lines (English): `python -m council.run_local` replaces steps 4–5 when there is no public URL.

---

## 4. Phase 2 — Free Windows setup for a non-technical owner

### 4.1 Goal

The owner, who is non-technical, can install and run the bot on a Windows 10/11 PC
by double-clicking two files and following one Vietnamese guide. Cost: 0 (except AI
API usage).

### 4.2 Required deliverables

**A. `setup.bat`** (repo root, Windows CMD, CRLF line endings):

1. `cd /d "%~dp0"` first.
2. Find Python ≥ 3.11: try `py -3 --version`, else `python --version`. If neither
   works or the version is < 3.11, print (Vietnamese) how to install Python from
   python.org **with "Add python.exe to PATH" ticked**, then `pause` and exit.
3. Create `.venv` if missing (`py -3 -m venv .venv` or `python -m venv .venv`).
4. `.venv\Scripts\python -m pip install --upgrade pip` then
   `.venv\Scripts\python -m pip install -e .`
5. If `.env` does not exist: copy `.env.example` to `.env`, print (Vietnamese)
   "Mở file .env, điền thông tin, lưu lại rồi chạy lại setup.bat", open it with
   `notepad .env`, `pause`, exit.
6. Run `.venv\Scripts\python -m council.db.migrate`. On failure print (Vietnamese)
   the three most likely causes: PostgreSQL not running, wrong password in
   `DATABASE_URL`, database not created — then `pause` and exit with error.
7. On success print (Vietnamese) "Cài đặt xong. Bấm đúp start.bat để chạy bot." and
   `pause`.
8. Every failure path MUST end with `pause` so the window does not vanish.
9. Re-running `setup.bat` MUST be safe (idempotent).

**B. `start.bat`** (repo root): `cd /d "%~dp0"`; if `.venv` missing tell the owner to
run `setup.bat` first; else run `.venv\Scripts\python -m council.run_local`; end with
`pause`. Print one Vietnamese line first: "Bot đang chạy. Đóng cửa sổ này để tắt bot."

**C. Ensure `.gitattributes`** contains `*.bat text eol=crlf` (create the file if it
does not exist).

**D. `docs/SETUP_WINDOWS.md`** — Vietnamese, for a non-technical reader. Required
sections in this order, each as numbered steps with exact clicks/text to type:

1. **Bạn cần gì** — Windows 10/11, internet, ~30 phút, a Telegram account, API key of
   at least **2** AI providers (Gemini has a free tier; say "kiểm tra điều kiện gói
   miễn phí trên trang Google AI Studio" — do not state exact quotas).
2. **Cài Python** — python.org download; tick **Add python.exe to PATH**.
3. **Cài PostgreSQL (miễn phí)** — EDB installer from postgresql.org for Windows;
   keep default port 5432; write down the password for user `postgres`; then create
   database `ai_council` using pgAdmin (exact clicks) **or** this one command in
   "SQL Shell (psql)": `CREATE DATABASE ai_council;`.
   Alternative box: **Neon** free cloud PostgreSQL — create project, copy the
   connection string, paste into `DATABASE_URL`, and note that Neon strings may end
   with `?sslmode=require` (verify the project's database URL format works with
   asyncpg; if it needs adjusting, implement the adjustment in
   `council/db/__init__.py::_async_url` and `alembic/env.py`, with a unit test, and
   document it).
4. **Tạo bot Telegram** — @BotFather → `/newbot` → copy token.
5. **Lấy ID Telegram của bạn** — message @userinfobot (or equivalent) → copy the
   numeric id. Explain this is what makes the bot obey only the owner.
6. **Tải dự án** — GitHub Desktop: File → Clone repository → `ai-council`.
7. **Điền file `.env`** — a table: variable → where it comes from → example.
   Required: `TELEGRAM_BOT_TOKEN`, `CEO_TELEGRAM_USER_ID`, `DATABASE_URL`, at least
   two of `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY`.
   Explain: `WEBHOOK_*` are **not needed** in local mode. Warn: if the database
   password contains special characters (`@ : / # ? %`), it must be URL-encoded —
   give the 5 encodings.
8. **Chạy setup.bat, rồi start.bat.**
9. **Dùng thử** — open the bot chat, send `/help`, then `/council Nên uống trà hay cà phê?`; expected messages.
10. **Tắt / bật lại** — closing the window stops the bot; messages sent while it is
    off are processed when started again (Telegram keeps updates ~24 h).
11. **Tự chạy khi bật máy (tùy chọn)** — shortcut to `start.bat` in
    `shell:startup`.
12. **Gặp lỗi?** — table: symptom → cause → fix. MUST include: window closes
    immediately; `python` not recognized; migrate fails (password / service
    stopped / database missing); bot does not reply (wrong token, wrong CEO id, bot
    not started, another process polling the same token → Telegram error 409);
    "paused: fewer than two agents have API keys"; quorum not reached (API credit).
13. **Nâng cấp sau này** — GitHub Desktop → Fetch → Pull, then run `setup.bat` again.

Link this guide from the top Vietnamese section of `README.md` with one line.

### 4.3 Definition of Done — Phase 2

- [ ] `setup.bat`, `start.bat`, `.gitattributes`, `docs/SETUP_WINDOWS.md` exist and match 4.2.
- [ ] Batch files are logically reviewed line by line for: quoting of paths with spaces (e.g. `C:\Users\Thanh MSI\…`), `%~dp0`, `errorlevel` checks after every command that can fail, `pause` on every exit path. If you can run Windows, actually run both files on a clean clone and record the result in the PR. If you cannot, say so explicitly in the PR — do not claim it was tested.
- [ ] Guide uses no unexplained jargon; every command is in a copyable code block.
- [ ] README links the guide.
- [ ] Full verification (section 6) passes.

---

## 5. Phase 3 — AI cost optimizations (ONLY after owner approval)

**Before starting Phase 3 you MUST ask the owner (in Vietnamese):**
"Phần 1 và 2 đã xong. Bạn có muốn mình làm Phần 3 (giảm chi phí gọi AI khoảng
1/3 đến 1/2, chất lượng gần như không đổi) không?" — and wait. If the owner does
not clearly say yes, stop after Phase 2 and write the report.

### 5.1 Required changes (all configurable, defaults shown)

| # | Change | Setting (`council/config.py` + `.env.example`) | Default |
|---|---|---|---|
| C1 | If **every requested agent** completed `INDEPENDENT_REVIEW` and all valid positions are identical, skip `CROSS_REVIEW` and `DEBATE`; go to `SYNTHESIS`. | `council_skip_review_when_unanimous: bool` | `True` |
| C2 | `SYNTHESIS` is run by **one** agent: the moderator (`MODERATOR_ID`); if it fails/times out, try the next agent in council order, then the next. If all fail → `QuorumNotReached`. | — | — |
| C3 | Max debate cycles configurable, clamped to `0..3` (N5 lock: never above 3). | `council_max_debate_cycles: int` | `2` |
| C4 | Output-token cap per call = `min(agent.max_tokens, setting)`. | `agent_max_output_tokens: int` | `1500` |

Implementation constraints:

- C1: a skip MUST be visible: add an audit log entry `REVIEW_SKIPPED_UNANIMOUS`
  (actor `system`, entity the discussion) and keep the CEO message label honest —
  it still shows `FULL COUNCIL RESULT (n/n agents)` based on the last multi-agent
  round. Critical discussions (`/council_critical`) MUST NOT skip (always run
  cross-review).
- C2: the synthesis round row in `rounds` MUST have `agents_requested` = the agents
  actually attempted, `completion_policy = 'ALL_RESPONDED'`, `quorum_threshold = 1`.
  `build_review()` MUST keep using the **last multi-agent round** for
  `recommendations`, `dissent`, `risks`, `questions`, `completed_agents`,
  `requested_agents` (not the single-agent synthesis round).
- C3/C4 are read from settings at runtime; `MAX_DEBATE_CYCLES` stays as the hard
  upper bound.

### 5.2 Test changes (explicitly allowed)

- `tests/test_mvp_integration.py::test_provider_timeout_is_visible_but_quorum_can_complete`:
  replace `assert all(r["partial"] for r in result.rounds)` with an assertion over
  all rounds **except** `SYNTHESIS`.
- `tests/test_mvp_integration.py::test_full_webhook_to_ceo_decision_flow`: construct
  the engine so it uses 3 debate cycles (keep the expected round list unchanged).
- `tests/test_postgres_e2e.py`: update expected `agent_runs` count (15 → 13 with one
  synthesis call) and `usage_costs`/cost totals accordingly.
- Add new tests: C1 unanimous skip (and no skip for critical), C2 fallback when the
  moderator fails, C3 clamp (e.g. 5 → 3, -1 → 0), C4 cap passed to the adapter.

### 5.3 Definition of Done — Phase 3

- [ ] C1–C4 implemented with settings documented in `.env.example` (commented, one English line each).
- [ ] New and adjusted tests pass; no other test assertion changed.
- [ ] README cost section (English, ≤ 8 lines) + one Vietnamese line in the top section: "Mặc định đã bật chế độ tiết kiệm chi phí".
- [ ] Full verification (section 6) passes.

---

## 6. Verification (run after every phase)

```bash
pip install -e ".[dev]"
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
# Real PostgreSQL (disposable DB; its public schema is DROPPED and recreated):
TEST_DATABASE_URL=postgresql://<user>:<pass>@localhost:5432/ai_council_test python -m pytest -q
```

All four MUST pass with **zero** failures and **zero** errors. Skipped tests are
acceptable only for the PostgreSQL suite when no database is available — and then
you MUST say so in the report; the phase is then **not** fully verified.

Manual smoke test (Phase 1, needs a real bot token and a PostgreSQL database):
run `python -m council.run_local`, send `/help` and `/status` from the CEO account,
confirm replies. Never paste the token into the PR or report.

---

## 7. When to stop and ask

Stop and report to the owner (Vietnamese, short) instead of guessing when:

- a rule in section 2 would have to be broken;
- `getUpdates`/`delete_webhook` behave differently than described here;
- a test outside the allowed list would need its assertion changed;
- PostgreSQL is unavailable so the DB suite cannot run (finish what you can, mark
  the gap clearly);
- anything would cost the owner money or touch their accounts (never create
  accounts, buy plans or enter payment details).

---

## 8. Final report (Vietnamese, to the owner, ≤ 15 lines)

1. Đã làm xong phần nào (1 / 2 / 3).
2. Kết quả kiểm tra: số bài test đạt / bỏ qua; đã chạy thử trên Windows hay chưa.
3. Link pull request.
4. Việc bạn (chủ dự án) cần làm tiếp: tối đa 3 bước, ngắn gọn.
5. Điều chưa chắc chắn / rủi ro còn lại (nếu có).
