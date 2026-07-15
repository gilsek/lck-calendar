# LoL Esports Calendar

Creates an iCalendar feed for LCK plus LoL Esports international events from the official LoL Esports site.

Included leagues:

- LCK
- MSI
- Worlds
- First Stand
- EWC

The main GitHub Actions workflow refreshes the LCK/T1 feeds every 30 minutes and publishes them with GitHub Pages.
The EWC workflow refreshes every 15 minutes during the configured EWC LoL event window and runs monthly outside that window for discovery.
The current EWC high-frequency window is configured for July 15-19, 2026 UTC.
After the current EWC page has no events in range, the parser also tries the same League of Legends slug on the next year's URL.

Calendar feed path:

```text
https://<github-user>.github.io/<repo-name>/lolesports-lck.ics
```

T1 LCK plus all international events feed path:

```text
https://<github-user>.github.io/<repo-name>/lolesports-t1-lck.ics
```

EWC feed path, parsed directly from the official Esports World Cup League of Legends page:

```text
https://<github-user>.github.io/<repo-name>/ewc-lol.ics
```

Manual local update:

```powershell
powershell -ExecutionPolicy Bypass -File .\update-calendar.ps1
```
