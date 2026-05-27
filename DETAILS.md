# KPI Details

---

## Demo Session Metrics

Metrics surfaced in the Streamlit dashboard. All have real data from Devin sessions.

### AI Agent Effectiveness

| Metric | Definition | Why |
|---|---|---|
| Commits per issue closed | Total commits on the agent's branch from open → PR merge | Fewer = Devin got it right faster |
| CI pass rate (first attempt) | % of Devin PRs where the first CI run passed | Failed CI = Devin shipped broken code |
| Human intervention rate | % of Devin PRs where a human pushed additional commits | High rate = Devin isn't self-sufficient on that task class |
| Agent cycle time | Issue created → PR opened by Devin | Core throughput signal |
| Reopened rate | % of Devin-closed issues reopened within 14 days | Best proxy for fix quality |
| Cost per fix | Devin session cost (USD) for issues that produced a merged PR | Business case metric |
| Agent efficiency score | `(1 / commits) * ci_pass * (1 - reopened) * (1 - human_intervened)` | Single composite score per session |

### CI/CD Health

| Metric | Definition | Why |
|---|---|---|
| Build success rate | % of CI runs on Devin PRs concluding `success` | Whether Devin's code actually passes the test suite |
| Build duration | Avg time from workflow trigger → completion on Devin PRs | Slow CI masks Devin's actual speed |

### Issue Health

| Metric | Definition | Why |
|---|---|---|
| Issue resolution time | Issue `opened` → `closed`, segmented by complexity label | Primary throughput signal |
| Reopened rate | % of Devin-closed issues reopened within 14 days | Surfaces which complexity tiers Devin closes incompletely |

### Velocity

| Metric | Definition | Why |
|---|---|---|
| Cycle time | Devin's first commit → PR merged | End-to-end speed per fix |
| PR size | Lines added + deleted per Devin PR | Proxy for whether fixes are surgical or broad rewrites |

---

## Future Metrics

Defined in schema, not yet surfaced. Require production deployments or more data volume.

### DORA

| Metric | Requires |
|---|---|
| Deployment frequency | Production deploy pipeline |
| Lead time for changes | Production deploy pipeline |
| Change failure rate | Production deploy pipeline + hotfix detection |
| MTTR | Production incident labeling |

### CI/CD

| Metric | Requires |
|---|---|
| Flaky test rate | Test result XML parsing across retries |
| Change failure rate | Production deploy pipeline |

### Issue Health

| Metric | Requires |
|---|---|
| Issue age distribution | Higher issue volume over longer time window |
| P0/P1 response time | Severity label convention + on-call integration |
| Stale issue count | 30-day observation window |

### Code Quality

| Metric | Requires |
|---|---|
| Lead time | Production deploy pipeline |
| Code churn | Git blame batch analysis |
| Review turnaround | Human reviewers in the loop |
| Bus factor | Multi-contributor history (90 days) |

### Community

| Metric | Requires |
|---|---|
| First-time contributor success rate | Human contributors |
| Time to first review for new contributors | Human contributors + reviewers |
| Contributor dropout rate | Human contributors |
