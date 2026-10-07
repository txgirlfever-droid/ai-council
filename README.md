# AI Council MVP

## 🇻🇳 Đọc nhanh trong 2 phút

**Một câu:** bạn hỏi trên Telegram, **3 AI của 3 hãng** bàn bạc với nhau, rồi gửi bạn
bản tóm tắt để **bạn quyết định**.

### Sơ đồ

```mermaid
flowchart TD
    A["1. Bạn hỏi trên Telegram"]
    B["2. Bot ghi lại câu hỏi"]
    subgraph C["3. Ba AI bàn bạc"]
        direction LR
        C1["Codex<br/>điều phối"]
        C2["Claude<br/>phản biện"]
        C3["Gemini<br/>tìm phương án"]
    end
    P["Thiếu AI trả lời<br/>→ tạm dừng, báo bạn"]
    D["4. Gửi bạn bản tóm tắt"]
    E["5. Bạn bấm Duyệt / Từ chối / Hoãn"]
    F["6. Lưu vĩnh viễn"]

    A --> B --> C --> D --> E --> F
    C -. nếu thiếu .-> P

    classDef council fill:#EEEDFE,stroke:#534AB7,color:#3C3489
    classDef warn fill:#FAEEDA,stroke:#854F0B,color:#633806
    class C,C1,C2,C3 council
    class P warn
```

### Ba AI bàn bạc thế nào?

**Tự nghĩ → Góp ý cho nhau → Tranh luận (tối đa 3 vòng) → Tóm tắt.**
Ý kiến phản đối luôn được giữ lại, không bị giấu.

### Dùng thế nào?

| Gõ | Để làm gì |
|---|---|
| `/council câu hỏi` | Hỏi hội đồng |
| `/council_critical câu hỏi` | Câu quan trọng: cả 3 AI phải trả lời |
| `/status` | Xem các cuộc họp đang mở |
| `/help` | Xem hướng dẫn |

### An toàn

- ✅ Chỉ bạn ra lệnh được.
- ✅ Không bịa kết quả: thiếu AI trả lời thì tạm dừng và báo bạn.
- ✅ Có trần chi phí mỗi lần hỏi (mặc định 5 USD).
- ✅ Quyết định đã bấm không sửa, không xóa được.

### Cần chuẩn bị (một lần)

1. Bot Telegram (tạo qua **@BotFather**).
2. Khóa API của ít nhất **2** hãng AI.
3. Máy chủ chạy 24/7 có **PostgreSQL**.
4. Điền vào file `.env`, rồi làm theo **Quick Start** bên dưới.

**Đổi sang model AI mới?** Chỉ cần sửa một dòng trong `.env`, không phải sửa code.

---

## Technical details

> Telegram-based AI advisory council where CEO orchestrates discussions between multiple AI agents (Codex/Claude/Gemini), with PostgreSQL as source of truth.

## Architecture

See `docs/ARCHITECTURE_v0.2.1.md` for the full spec.

## Quick Start

```bash
# 1. Install (Python 3.11+, PostgreSQL 14+)
git clone <repo>
cd ai-council
pip install -e ".[dev]"

# 2. Configure: copy the example and fill in your tokens and keys
cp .env.example .env

# 3. Create the database tables
python -m council.db.migrate

# 4. Tell Telegram where your server is (needs WEBHOOK_URL and WEBHOOK_SECRET)
python -m council.webhook.register

# 5. Start the worker and the webhook (two terminals)
python -m council.worker
python -m council.webhook
```

An agent only joins the council when its provider key (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`) is set; at least two agents, one of them
not Codex, are needed for a quorum.

## Using the bot (CEO only)

| Command | What happens |
|---|---|
| `/council <question>` | The council reviews, cross-reviews, debates (up to 3 cycles) and posts a synthesis with **Approve / Reject / Defer** buttons. |
| `/council_critical <question>` | Same, but every agent must answer and Codex uses its escalated model. |
| `/status` | Open discussions, queue size, timeouts and cost limits. |
| `/help` | Command list. |

Pressing a decision button records an immutable decision (`DEC-…`) exactly once;
a second press is answered with the decision that already stands. If too few
agents answer, the discussion is **paused** and the CEO is told why — no result
is invented.

## Project Structure

```
council/
├── db/
│   ├── schema.sql          # Full PostgreSQL schema (Phase 1)
│   ├── migrate.py          # Migration runner
│   └── seeds.sql           # Default agent configs
├── webhook/
│   ├── handler.py          # Telegram webhook (async ACK)
│   ├── register.py         # Registers the webhook URL + secret with Telegram
│   └── service.py          # Deterministic idempotency keys/intake ledger
├── orchestrator/
│   ├── engine.py           # Discussion/round/debate/synthesis state machine
│   ├── prompts.py          # Role + round prompts with the JSON response contract
│   ├── quorum.py           # Quorum logic (N1)
│   └── service.py          # Worker job handlers: council runs + CEO decision gate
├── agents/
│   ├── base.py             # ProviderAdapter ABC
│   ├── openai_adapter.py   # OpenAI adapter
│   ├── anthropic_adapter.py# Anthropic adapter
│   ├── google_adapter.py   # Google adapter
│   └── runner.py           # Retry and provider-timeout boundary
├── worker/
│   ├── __main__.py         # Worker entry point
│   ├── worker.py           # Job queue consumer (SELECT FOR UPDATE SKIP LOCKED)
│   └── lease.py            # Lease transition rules
├── telegram/
│   ├── sender.py           # Telegram client boundary (swappable in tests)
│   ├── format.py           # CEO-facing message text
│   └── buttons.py          # Approve/Reject/Defer keyboard (64-byte callback data)
├── schemas/
│   ├── council_response.py # Structured JSON response schema (Correction #10)
├── costs/
│   └── tracker.py          # Cost tracking + soft/hard limits
└── audit/
    └── log.py              # Append-only audit log
tests/
├── test_idempotency.py     # Idempotency tests (MUST HAVE)
├── test_quorum.py          # Quorum logic tests
├── test_flow.py            # Full integration flow (mock providers)
├── test_mvp_integration.py # Webhook→rounds→CEO + timeout/race/lease
├── test_postgres_e2e.py    # Same flow on a real PostgreSQL (opt-in)
└── conftest.py             # Test fixtures
```

## Priority Order

**Correctness → Idempotency → Auditability → Failure recovery → Tests → Performance → Scale**

## MVP Scope

Redis, Kubernetes, Prometheus, partitioning, and distributed caching are explicitly deferred.

## Verification

```bash
python -m pytest -q
python -m ruff check .

# Full flow on a real, disposable PostgreSQL database (its public schema is reset):
TEST_DATABASE_URL=postgresql://council:secret@localhost:5432/ai_council_test python -m pytest -q
```

Provider calls are mocked by default. External API tests require explicit credentials and opt-in.
