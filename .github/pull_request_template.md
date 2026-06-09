## Summary

<!-- 1–3 sentences: what changed and why -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / docs / tooling
- [ ] Test coverage

## How to verify

<!-- Commands run, endpoints hit, or screenshots -->

```bash
timeout 300 python -m pytest app/tests/ -q --maxfail=1 -x
```

## Checklist

- [ ] No secrets, `.env`, or `data/*.sqlite3` in the diff
- [ ] Tests added or updated when behavior changes
- [ ] API JSON shapes unchanged (or migration documented)
- [ ] External HTTP mocked in tests

## Related issues

<!-- Fixes #123 -->
