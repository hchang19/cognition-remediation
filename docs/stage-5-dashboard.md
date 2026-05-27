# Stage 5 — Streamlit Dashboard

Read-only view over SQLite. Queries on every refresh — no background updates needed.

## Files

```
dashboard/app.py
```

Run: `streamlit run dashboard/app.py`

---

## Layout

### Top Strip — KPIs (6 columns)

| Metric | SQL |
|---|---|
| Total issues | `SELECT COUNT(*) FROM issues` |
| Sessions started | `SELECT COUNT(*) FROM sessions` |
| PRs opened | `SELECT COUNT(*) FROM sessions WHERE pr_number IS NOT NULL` |
| Success rate | `completed / (completed + failed + blocked)` |
| Total cost | `SELECT SUM(cost_usd) FROM sessions WHERE status = 'completed'` |
| Blocked | `SELECT COUNT(*) FROM sessions WHERE status = 'blocked'` |

---

### Session Timeline — Line Chart

Time series of events by day: issues created / sessions started / PRs opened.

```sql
SELECT DATE(timestamp) as day, event_type, COUNT(*) as count
FROM events
WHERE event_type IN ('issue.created', 'session.started', 'pr.opened')
GROUP BY day, event_type
ORDER BY day
```

Render with `st.line_chart`.

---

### Complexity Breakdown — Bar Chart

Success rate segmented by `complexity` label.

```sql
SELECT i.complexity,
       SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) as succeeded,
       COUNT(*) as total
FROM sessions s
JOIN issues i ON s.issue_id = i.issue_id
WHERE s.status IN ('completed', 'failed', 'blocked')
GROUP BY i.complexity
```

Render with `st.bar_chart`.

---

### Per-Issue Detail Table

One row per issue. All key metrics in a single scannable table.

Columns:
```
Issue title | Complexity | Status | Session URL | PR | CI pass | Commits | Human intervened | Duration | Cost (USD) | Efficiency score
```

```sql
SELECT
    i.title,
    i.complexity,
    s.status,
    s.session_url,
    s.pr_number,
    s.ci_first_pass,
    s.commits_count,
    s.human_intervened,
    s.duration_seconds,
    s.cost_usd,
    -- efficiency score: NULL if session not terminal
    CASE WHEN s.commits_count > 0 AND s.status = 'completed'
         THEN (1.0 / s.commits_count) * s.ci_first_pass
              * (1 - s.human_intervened)
              * (1 - CASE WHEN i.reopened_at IS NOT NULL THEN 1 ELSE 0 END)
         ELSE NULL END as efficiency_score
FROM issues i
LEFT JOIN sessions s ON i.issue_id = s.issue_id
ORDER BY i.created_at DESC
```

`session_url` rendered as a clickable link. `pr_number` rendered as a link to the fork PR.

Render with `st.dataframe`.

---

## Notes

- Add `st.button("Refresh")` — queries are cheap on a small SQLite file, manual refresh is fine
- `st.set_page_config(layout="wide")` for the full-width table
- Use `st.metric` for the top strip values with delta indicators where applicable
