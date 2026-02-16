[![PyPI](https://img.shields.io/pypi/v/linkedin-mcp-scheduler-ldraney)](https://pypi.org/project/linkedin-mcp-scheduler-ldraney/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

# linkedin-mcp-scheduler

Schedule and manage LinkedIn posts through conversation with an AI agent. Posts publish reliably on time — locally or in a container.

## User Story

You're chatting with your AI agent. You say:

> "Schedule that post for tomorrow at 9am"

Done. Then later:

> "Show me my queue"

You see everything — what's pending, what published, what failed. You say:

> "Actually push that one to Thursday and change the second paragraph"

The agent edits it in-place and reschedules. No cancelling, no re-creating, no UUIDs to copy-paste.

> "That failed post from yesterday — retry it now"

Retried. Published. You never opened LinkedIn.

This is what linkedin-mcp-scheduler provides: a standalone MCP server with a reliable publishing daemon, full CRUD on your post queue, and a conversational UX designed for AI agents to manage your LinkedIn presence.

## Why a Separate Project

[linkedin-mcp](https://github.com/ldraney/linkedin-mcp) wraps the LinkedIn API as MCP tools — posting, engagement, auth. It has basic scheduling, but scheduling is a different problem:

- It needs a **daemon** that runs continuously and publishes due posts. The API wrapper doesn't.
- It needs **persistent state** (a database) with its own lifecycle. The API wrapper is stateless.
- It needs to be **containerizable** — running in k8s with a volume-backed DB, HTTP transport, and env-based credentials. The API wrapper runs fine as a local stdio MCP.
- The UX surface area is large enough to be its own product: queue management, editing, rescheduling, retries, media scheduling, recurring posts.

## Architecture

```
┌──────────────────────────────────────┐
│  Container (or local)                │
│                                      │
│  MCP Server (HTTP/SSE)               │
│    - schedule, list, edit, cancel     │
│    - reschedule, retry, queue summary │
│     ↕ reads/writes                   │
│  SQLite DB  ← persistent volume      │
│     ↕ reads/writes                   │
│  Publisher Daemon (poll loop)         │
│    - publishes due posts via SDK      │
│    - retries failures                 │
│                                      │
└──────────────────────────────────────┘
```

Both processes share one SQLite database. In a container, the DB lives on a persistent volume. Locally, it defaults to `~/.linkedin-mcp-scheduler/scheduled.db`.

### Deployment Modes

- **Local**: `uv run linkedin-mcp-scheduler` — stdio MCP for Claude Code, daemon as a background process or launchd job
- **Container**: docker-compose with HTTP transport, volume-mounted DB, env-var credentials — ready to lift into k8s

## MCP Tools

### Core (from linkedin-mcp, to be extracted)
| Tool | Description |
|------|-------------|
| `schedule_post` | Schedule a text post (+ optional URL) for future publication |
| `list_scheduled_posts` | List scheduled posts, filterable by status |
| `get_scheduled_post` | Get details of a specific scheduled post |
| `cancel_scheduled_post` | Cancel a pending scheduled post |

### Planned
| Tool | Description |
|------|-------------|
| `update_scheduled_post` | Edit content, URL, or visibility of a pending post in-place |
| `reschedule_post` | Change the scheduled time of a pending post |
| `retry_failed_post` | Retry a failed post, optionally at a new time |
| `queue_summary` | Formatted overview: counts by status, next due, recent failures |
| `schedule_post_with_image` | Schedule a post with an image attachment |
| `schedule_post_with_document` | Schedule a post with a document attachment |
| `schedule_post_with_poll` | Schedule a post with a poll |

## Status

Early stage — establishing scope, setting up the project, and creating issues for the build-out.
