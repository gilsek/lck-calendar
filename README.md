# LoL Esports Calendar

Creates an iCalendar feed for LCK plus LoL Esports international events from the official LoL Esports site.

Included leagues:

- LCK
- MSI
- Worlds
- First Stand

- EWC LoL

The GitHub Actions workflow refreshes the feeds every 30 minutes and publishes them with GitHub Pages.

Calendar feed path:

```text
https://<github-user>.github.io/<repo-name>/lolesports-lck.ics
```

T1 LCK plus all international events feed path:

```text
https://<github-user>.github.io/<repo-name>/lolesports-t1-lck.ics
```

EWC LoL feed path, parsed directly from the official Esports World Cup League of Legends page:

```text
https://<github-user>.github.io/<repo-name>/ewc-lol.ics
```

Manual local update:

```powershell
powershell -ExecutionPolicy Bypass -File .\update-calendar.ps1
```
