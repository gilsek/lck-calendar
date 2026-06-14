# LoL Esports Calendar

Creates an iCalendar feed for LCK plus LoL Esports international events from the official LoL Esports site.

Included leagues:

- LCK
- MSI
- Worlds
- First Stand

The GitHub Actions workflow refreshes the feed every 6 hours and publishes it with GitHub Pages.

Calendar feed path:

```text
https://<github-user>.github.io/<repo-name>/lolesports-lck.ics
```

T1 LCK-only feed path:

```text
https://<github-user>.github.io/<repo-name>/lolesports-t1-lck.ics
```

Manual local update:

```powershell
powershell -ExecutionPolicy Bypass -File .\update-calendar.ps1
```
