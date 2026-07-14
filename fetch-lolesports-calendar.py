#!/usr/bin/env python3
import argparse
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen


LEAGUE_IDS = {
    "worlds": "98767975604431411",
    "msi": "98767991325878492",
    "first_stand": "113464388705111224",
    "lck": "98767991310872058",
    "ewc_lol": "116838530616006090",
}


def fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def iter_json_objects(text: str):
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{"data":\{"__typename":"Query"', text):
        try:
            obj, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        yield obj


def collect_events(
    html_text: str,
    league_ids: set[str],
    team_filters: set[str],
    team_filter_league_ids: set[str],
    start_after: datetime | None,
    start_before: datetime | None,
) -> list[dict]:
    events_by_id = {}
    unescaped = html.unescape(html_text)
    for obj in iter_json_objects(unescaped):
        esports = obj.get("data", {}).get("esports")
        if not isinstance(esports, dict):
            continue
        for event in esports.get("events") or []:
            if event.get("__typename") != "EventMatch":
                continue
            league = event.get("league") or {}
            if league_ids and league.get("id") not in league_ids:
                continue
            should_filter_team = team_filters and (
                not team_filter_league_ids or league.get("id") in team_filter_league_ids
            )
            if should_filter_team and not event_matches_team(event, team_filters):
                continue
            if event.get("startTime"):
                start = parse_dt(event["startTime"])
                if start_after and start < start_after:
                    continue
                if start_before and start > start_before:
                    continue
            events_by_id[event["id"]] = event
    return sorted(events_by_id.values(), key=lambda e: e.get("startTime", ""))


def event_completeness_score(event: dict) -> int:
    score = 0
    for team in event.get("matchTeams") or []:
        code = (team.get("code") or "").strip().upper()
        name = (team.get("name") or "").strip().upper()
        if code and code != "TBD":
            score += 2
        if name and name != "TBD":
            score += 1
    if event.get("state") and event.get("state") != "unstarted":
        score += 1
    return score


def merge_events(existing_events: dict[str, dict], events: list[dict]) -> None:
    for event in events:
        event_id = event["id"]
        existing = existing_events.get(event_id)
        if not existing or event_completeness_score(event) >= event_completeness_score(
            existing
        ):
            existing_events[event_id] = event


def event_matches_team(event: dict, team_filters: set[str]) -> bool:
    for team in event.get("matchTeams") or []:
        values = [
            team.get("code"),
            team.get("name"),
            team.get("slug"),
            team.get("id"),
        ]
        normalized = {str(value).strip().lower() for value in values if value}
        if normalized & team_filters:
            return True
    return False


def parse_dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold_ics_line(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts = []
    current = ""
    for char in line:
        if len((current + char).encode("utf-8")) > 75:
            parts.append(current)
            current = " " + char
        else:
            current += char
    parts.append(current)
    return "\r\n".join(parts)


def format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def unfold_ics_lines(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def parse_ics_utc(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    if value.endswith("Z"):
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    try:
        return datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def load_existing_future_events(path: str, keep_after: datetime) -> dict[str, list[str]]:
    ics_path = Path(path)
    if not path or not ics_path.exists():
        return {}

    text = ics_path.read_text(encoding="utf-8")
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    events = {}
    index = 0
    while index < len(raw_lines):
        if raw_lines[index] != "BEGIN:VEVENT":
            index += 1
            continue
        start_index = index
        while index < len(raw_lines) and raw_lines[index] != "END:VEVENT":
            index += 1
        if index >= len(raw_lines):
            break
        block = raw_lines[start_index : index + 1]
        unfolded = unfold_ics_lines("\n".join(block))
        uid = None
        event_end = None
        event_start = None
        for line in unfolded:
            if line.startswith("UID:"):
                uid = line.split(":", 1)[1]
            elif line.startswith("DTEND"):
                event_end = parse_ics_utc(line.split(":", 1)[1])
            elif line.startswith("DTSTART"):
                event_start = parse_ics_utc(line.split(":", 1)[1])
        comparable_time = event_end or event_start
        if uid and comparable_time and comparable_time >= keep_after:
            events[uid] = block
        index += 1
    return events


def event_title(event: dict) -> str:
    teams = event.get("matchTeams") or []
    names = [team.get("code") or team.get("name") or "TBD" for team in teams[:2]]
    while len(names) < 2:
        names.append("TBD")
    league_name = (event.get("league") or {}).get("name") or "LoL Esports"
    return f"{league_name}: {names[0]} vs {names[1]}"


def event_description(event: dict) -> str:
    league = event.get("league") or {}
    tournament = event.get("tournament") or {}
    match = event.get("match") or {}
    strategy = match.get("strategy") or {}
    best_of = ""
    if strategy.get("type") == "bestOf" and strategy.get("count"):
        best_of = f"Bo{strategy['count']}"
    lines = [
        f"League: {league.get('slug') or league.get('name', '')}",
        f"Tournament: {tournament.get('id', '')}",
        f"Stage: {event.get('blockName', '')}",
        f"Format: {best_of}",
        f"State: {event.get('state', '')}",
        f"LoL Esports event id: {event.get('id', '')}",
    ]
    return "\n".join(line for line in lines if not line.endswith(": "))


def build_ics(
    events: list[dict],
    calendar_name: str,
    duration_hours: int,
    preserved_events: dict[str, list[str]] | None = None,
) -> str:
    now = format_utc(datetime.now(timezone.utc))
    preserved_events = preserved_events or {}
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Codex//LoL Esports Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
    ]
    current_uids = set()
    for event in events:
        start = parse_dt(event["startTime"])
        end = start + timedelta(hours=duration_hours)
        uid = f"lolesports-{event['id']}@lolesports.com"
        current_uids.add(uid)
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now}",
                f"DTSTART:{format_utc(start)}",
                f"DTEND:{format_utc(end)}",
                f"SUMMARY:{ics_escape(event_title(event))}",
                f"DESCRIPTION:{ics_escape(event_description(event))}",
                f"URL:https://lolesports.com/ko-KR/leagues/{(event.get('league') or {}).get('slug', 'lck')}",
                "END:VEVENT",
            ]
        )
    for uid in sorted(preserved_events):
        if uid not in current_uids:
            lines.extend(preserved_events[uid])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an ICS calendar from official LoL Esports schedule data embedded in lolesports.com."
    )
    parser.add_argument(
        "--url",
        default="https://lolesports.com/ko-KR/leagues/lck",
        help="Comma-separated LoL Esports pages to scrape and merge.",
    )
    parser.add_argument("--output", default="lolesports-lck.ics")
    parser.add_argument(
        "--leagues",
        default="lck,msi,worlds,first_stand",
        help="Comma-separated league slugs or numeric LoL Esports league IDs.",
    )
    parser.add_argument(
        "--teams",
        default="",
        help="Comma-separated team codes, names, slugs, or IDs. Example: T1",
    )
    parser.add_argument(
        "--team-filter-leagues",
        default="",
        help=(
            "Comma-separated league slugs or IDs where --teams should apply. "
            "When omitted, --teams applies to every selected league."
        ),
    )
    parser.add_argument("--duration-hours", type=int, default=4)
    parser.add_argument("--calendar-name", default="LoL Esports - LCK + Internationals")
    parser.add_argument("--from-days", type=int, default=-7)
    parser.add_argument("--to-days", type=int, default=120)
    parser.add_argument(
        "--merge-existing-ics",
        default="",
        help="Existing ICS file whose future VEVENTs should be preserved if missing from the latest scrape.",
    )
    args = parser.parse_args()

    selected_ids = set()
    for item in [part.strip() for part in args.leagues.split(",") if part.strip()]:
        selected_ids.add(LEAGUE_IDS.get(item, item))
    team_filters = {
        part.strip().lower() for part in args.teams.split(",") if part.strip()
    }
    team_filter_league_ids = set()
    for item in [
        part.strip() for part in args.team_filter_leagues.split(",") if part.strip()
    ]:
        team_filter_league_ids.add(LEAGUE_IDS.get(item, item))

    now = datetime.now(timezone.utc)
    start_after = now + timedelta(days=args.from_days) if args.from_days is not None else None
    start_before = now + timedelta(days=args.to_days) if args.to_days is not None else None

    events_by_id = {}
    for url in [part.strip() for part in args.url.split(",") if part.strip()]:
        html_text = fetch_text(url)
        merge_events(
            events_by_id,
            collect_events(
                html_text,
                selected_ids,
                team_filters,
                team_filter_league_ids,
                start_after,
                start_before,
            ),
        )
    events = sorted(events_by_id.values(), key=lambda event: event.get("startTime", ""))
    preserved_events = load_existing_future_events(args.merge_existing_ics, now)
    ics = build_ics(events, args.calendar_name, args.duration_hours, preserved_events)
    Path(args.output).write_text(ics, encoding="utf-8", newline="")
    preserved_count = len(
        [
            uid
            for uid in preserved_events
            if uid not in {f"lolesports-{event['id']}@lolesports.com" for event in events}
        ]
    )
    print(
        f"Wrote {len(events)} scraped events and preserved {preserved_count} existing events to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
