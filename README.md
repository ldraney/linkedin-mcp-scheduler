# linkedin-mcp-scheduler

A daemon and refined MCP toolset for scheduling and managing LinkedIn posts through conversational AI.

Built on top of [linkedin-mcp](https://github.com/ldraney/linkedin-mcp) — this project extracts and extends the scheduling functionality into a standalone, reliable system.

## The Problem

linkedin-mcp has basic scheduling tools that write to a local SQLite database, but:

- **No daemon** — posts only publish if you manually run `linkedin-mcp-scheduler`. Miss the window, miss the post.
- **No edit-in-place** — to change a pending post you must cancel and re-create it.
- **No media support** — scheduled posts are text-only (+ optional URL). Images, documents, polls, and multi-image posts can't be scheduled.
- **No recurring posts** — every post is one-shot.
- **No visibility into history** — published/failed posts sit in the DB with no easy way to review or retry.

## Goals

1. **Reliable publishing daemon** — a background process (or cron-friendly runner) that checks for due posts and publishes them, with retry logic and failure notifications.
2. **Full CRUD on the queue** — schedule, list, edit, reschedule, cancel, and retry posts conversationally.
3. **Media scheduling** — support images, documents, polls, videos, and multi-image posts in the schedule queue.
4. **Queue visibility** — clear, formatted queue views with filtering and status summaries.
5. **Conversational UX** — optimized for the workflow of drafting, scheduling, reviewing, and adjusting posts through chat with an AI agent.

## Current MCP Tools (from linkedin-mcp)

| Tool | Description |
|------|-------------|
| `schedule_post` | Schedule a text post (+ optional URL) for future publication |
| `list_scheduled_posts` | List scheduled posts, filterable by status |
| `get_scheduled_post` | Get details of a specific scheduled post by UUID |
| `cancel_scheduled_post` | Cancel a pending scheduled post |

## Planned MCP Tools

| Tool | Description |
|------|-------------|
| `update_scheduled_post` | Edit the content, URL, or visibility of a pending post |
| `reschedule_post` | Change the scheduled time of a pending post |
| `retry_failed_post` | Retry a failed post (optionally with a new time) |
| `schedule_post_with_image` | Schedule a post with an image attachment |
| `schedule_post_with_document` | Schedule a post with a document attachment |
| `schedule_post_with_poll` | Schedule a post with a poll |
| `queue_summary` | Get a formatted summary of the queue (counts by status, next due post, etc.) |

## Architecture

```
linkedin-mcp-scheduler/
├── daemon/           # Background publisher process
├── tools/            # MCP tool definitions (extended CRUD + media)
├── db/               # SQLite schema and migrations
└── README.md
```

The scheduler daemon runs independently and publishes due posts. The MCP tools provide the conversational interface for managing the queue. Both share the same SQLite database at `~/.linkedin-mcp/scheduled.db`.

## Status

Early stage — defining scope and building out from the existing linkedin-mcp scheduler foundation.
