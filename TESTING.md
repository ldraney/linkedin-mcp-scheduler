# QA Testing Checklist

Run this checklist before merging significant changes. Unit tests (`uv run pytest`) verify logic, but this checklist verifies the MCP server works as an actual MCP server.

## Prerequisites

```bash
# Ensure dependencies are installed
uv sync

# Set up a test database (don't use production DB)
export DB_PATH="/tmp/linkedin-mcp-qa-test.db"
rm -f "$DB_PATH"
```

## 1. Server starts and tools are discoverable

```bash
DB_PATH="/tmp/linkedin-mcp-qa-test.db" uv run python -c "
from linkedin_mcp_scheduler.server import mcp
tools = sorted(mcp._tool_manager._tools.keys())
assert len(tools) == 8, f'Expected 8 tools, got {len(tools)}: {tools}'
print('PASS: 8 tools registered')
for t in tools:
    print(f'  - {t}')
"
```

Expected: 8 tools — `cancel_scheduled_post`, `get_scheduled_post`, `list_scheduled_posts`, `queue_summary`, `reschedule_post`, `retry_failed_post`, `schedule_post`, `update_scheduled_post`

## 2. MCP protocol roundtrip (stdio)

```bash
DB_PATH="/tmp/linkedin-mcp-qa-test.db" uv run python -c "
import json, asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def test():
    params = StdioServerParameters(
        command='uv',
        args=['run', 'linkedin-mcp-scheduler'],
        env={'DB_PATH': '/tmp/linkedin-mcp-qa-test.db'}
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

## 3. Exercise each tool

Run through the full CRUD cycle via MCP protocol:

| Step | Tool | Verify |
|------|------|--------|
| 1 | `queue_summary` | Returns empty queue (counts = {}) |
| 2 | `schedule_post` | Returns postId, status=pending |
| 3 | `list_scheduled_posts` | count=1, post appears |
| 4 | `get_scheduled_post` | Returns correct post by ID |
| 5 | `update_scheduled_post` | Commentary changes, success=true |
| 6 | `reschedule_post` | scheduled_time changes in post object |
| 7 | `cancel_scheduled_post` | status=cancelled |
| 8 | `schedule_post` (new) | Create another, then force-fail via DB |
| 9 | `retry_failed_post` | status resets to pending in post object |
| 10 | `queue_summary` | Correct counts (1 pending, 1 cancelled) |

## 4. Daemon starts and stops cleanly

```bash
DB_PATH="/tmp/linkedin-mcp-qa-test.db" uv run linkedin-mcp-scheduler-daemon &
DAEMON_PID=$!
sleep 3
kill $DAEMON_PID
wait $DAEMON_PID 2>/dev/null
echo "PASS: Daemon started and stopped cleanly"
```

Expected output:
```
Publisher daemon started (poll interval: 60s)
Received signal 15, shutting down gracefully...
Publisher daemon stopped.
```

## 5. Unit tests still pass

```bash
uv run pytest -v
```

All tests should pass. If any fail after your changes, fix them before merging.

## When to run this checklist

- Before merging PRs that change tool implementations, server setup, or database schema
- After upgrading the `mcp` dependency
- After changing entry points or the server startup path
- Before releasing a new version
