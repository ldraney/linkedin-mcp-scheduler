# Testing

## Test layers

| Layer | Command | What it tests | Mocks |
|-------|---------|---------------|-------|
| Unit | `uv run pytest` | DB CRUD, tool logic, daemon dispatch | LinkedIn SDK only (in daemon tests) |
| Integration | `uv run pytest -m integration` | Real LinkedIn API, real MCP protocol | None on publish path |

## Running tests

```bash
# Unit tests (fast, no credentials needed, runs by default)
uv run pytest -v

# Integration tests (hits real LinkedIn API, needs keychain credentials)
uv run pytest -m integration -v

# Everything
uv run pytest -m '' -v
```

## Integration tests

Integration tests (`tests/test_integration.py`) create **real posts on LinkedIn** and delete them after each test. They require valid OAuth credentials in the OS keychain.

What they cover:
- SDK smoke: `create_post` and `create_post_with_link` against real API
- Daemon publish: `DB.add() -> run_once() -> LinkedIn API -> DB.mark_published()`
- Bad credentials: real SDK with invalid token -> daemon marks failed, doesn't crash
- Full stack: `MCP stdio client -> schedule_post tool -> DB -> daemon -> LinkedIn API -> cleanup`

They are skipped automatically when credentials are missing. They include rate-limit delays (3s between tests) because LinkedIn throttles post creation.

## Manual QA checklist

Run this before merging significant changes.

### 1. Server starts and tools are discoverable

```bash
DB_PATH="/tmp/qa-test.db" uv run python -c "
from linkedin_mcp_scheduler.server import mcp
tools = sorted(mcp._tool_manager._tools.keys())
assert len(tools) == 8, f'Expected 8 tools, got {len(tools)}: {tools}'
print('PASS: 8 tools registered')
for t in tools:
    print(f'  - {t}')
"
```

### 2. MCP protocol roundtrip

```bash
DB_PATH="/tmp/qa-test.db" uv run python -c "
import json, asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def test():
    params = StdioServerParameters(
        command='uv',
        args=['run', 'linkedin-mcp-scheduler'],
        env={'DB_PATH': '/tmp/qa-test.db'}
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 8
            r = await session.call_tool('queue_summary', {})
            data = json.loads(r.content[0].text)
            assert 'summary' in data
            print('PASS: MCP stdio roundtrip works')

asyncio.run(test())
"
```

### 3. Daemon starts and stops

```bash
DB_PATH="/tmp/qa-test.db" uv run linkedin-mcp-scheduler-daemon &
DAEMON_PID=$!
sleep 3
kill $DAEMON_PID
wait $DAEMON_PID 2>/dev/null
# Expected: "Publisher daemon started" ... "Publisher daemon stopped."
```

### When to run

- Before merging PRs that change tool implementations, server setup, or database schema
- After upgrading the `mcp` or `ldraney-linkedin-sdk` dependency
- After changing entry points or the server startup path
