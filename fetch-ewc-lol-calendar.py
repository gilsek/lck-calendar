#!/usr/bin/env python3
import argparse
import importlib.util
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = "https://esportsworldcup.com/en/competitions/2026/league-of-legends"
DEFAULT_LOLESPORTS_FALLBACK_URL = "https://lolesports.com/ko-KR"
EWC_LOLESPORTS_LEAGUE_ID = "116838530616006090"
YEAR_IN_URL = re.compile(r"(/competitions/)(\d{4})(/)")


def fetch_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://esportsworldcup.com/en/schedule",
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def extract_next_f_payload(html_text: str) -> str:
    chunks = []
    for match in re.finditer(
        r"<script>self\.__next_f\.push\((.*?)\)</script>", html_text, flags=re.S
    ):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if len(payload) > 1 and isinstance(payload[1], str):
            chunks.append(payload[1])
    return "".join(chunks)


def parse_balanced_json(text: str, start: int):
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])
    raise ValueError("Could not parse balanced JSON from EWC page payload.")


def extract_structures(html_text: str) -> list[dict]:
    payload = extract_next_f_payload(html_text)
    key = '"initialStructures":'
    start = payload.find(key)
    if start < 0:
        raise ValueError("Could not find initialStructures in EWC page payload.")
    return parse_balanced_json(payload, start + len(key))


def fallback_urls(url: str, next_years: int) -> list[str]:
    urls = [url]
    match = YEAR_IN_URL.search(url)
    if not match:
        return urls

    base_year = int(match.group(2))
    for offset in range(1, next_years + 1):
        urls.append(YEAR_IN_URL.sub(rf"\g<1>{base_year + offset}\g<3>", url, count=1))
    return urls


def parse_dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def format_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def event_block_start_key(block: list[str]) -> str | None:
    for line in unfold_ics_lines("\n".join(block)):
        if line.startswith("DTSTART"):
            start = parse_ics_utc(line.split(":", 1)[1])
            return format_utc(start) if start else None
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


def normalize_existing_ics(text: str, calendar_name: str) -> str:
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    normalized_lines = []
    index = 0
    while index < len(raw_lines):
        if raw_lines[index].startswith("X-WR-CALNAME:"):
            normalized_lines.append(f"X-WR-CALNAME:{ics_escape(calendar_name)}")
            index += 1
            continue
        if raw_lines[index] != "BEGIN:VEVENT":
            normalized_lines.append(raw_lines[index])
            index += 1
            continue

        start_index = index
        while index < len(raw_lines) and raw_lines[index] != "END:VEVENT":
            index += 1
        if index >= len(raw_lines):
            normalized_lines.extend(raw_lines[start_index:])
            break

        block = raw_lines[start_index : index + 1]
        unfolded = unfold_ics_lines("\n".join(block))
        block_text = "\n".join(unfolded)
        best_of_match = re.search(r"Format: Bo([135])", block_text)
        start = None
        if best_of_match:
            for line in unfolded:
                if line.startswith("DTSTART"):
                    start = parse_ics_utc(line.split(":", 1)[1])
                    break
        if start and best_of_match:
            end = start + timedelta(hours=int(best_of_match.group(1)))
            for block_index, line in enumerate(block):
                if line.startswith("DTEND"):
                    block[block_index] = f"DTEND:{format_utc(end)}"
                    break
        for block_index, line in enumerate(block):
            if line.startswith("SUMMARY:"):
                block[block_index] = line.replace("SUMMARY:EWC LoL - ", "SUMMARY:EWC - ", 1)
                break
        normalized_lines.extend(block)
        index += 1
    return "\r\n".join(normalized_lines).rstrip("\r\n") + "\r\n"


def slot_name(slot: dict) -> str:
    competitor = slot.get("competitor") or {}
    team = competitor.get("team") or {}
    club = competitor.get("club") or {}
    for value in [
        team.get("short_name"),
        team.get("name"),
        club.get("short_name"),
        club.get("name"),
    ]:
        if value:
            return value

    source = slot.get("source") or {}
    return source.get("label") or "TBD"


def has_resolved_competitors(series: dict) -> bool:
    slots = series.get("slots") or []
    return len(slots) >= 2 and all(slot.get("competitor") for slot in slots[:2])


def series_title(series: dict) -> str:
    structure = series.get("structure") or {}
    label = structure.get("label") or structure.get("round_name") or "Match"
    slots = series.get("slots") or []
    names = [slot_name(slot) for slot in slots[:2]]
    while len(names) < 2:
        names.append("TBD")

    if has_resolved_competitors(series):
        return f"EWC - {names[0]} vs {names[1]}"
    return f"EWC - {label}"


def series_description(phase: dict, series: dict, source_url: str) -> str:
    structure = series.get("structure") or {}
    match_format = series.get("format") or {}
    best_of = ""
    if match_format.get("type") == "BEST_OF" and match_format.get("best_of"):
        best_of = f"Bo{match_format['best_of']}"
    slot_lines = [
        f"Slot {slot.get('slot')}: {slot_name(slot)}" for slot in series.get("slots") or []
    ]
    lines = [
        f"Phase: {phase.get('name', '')}",
        f"Round: {structure.get('round_name', '')}",
        f"Bracket: {structure.get('bracket_type', '')} {structure.get('bracket_path', '')}".strip(),
        f"Format: {best_of}",
        f"State: {series.get('state', '')}",
        f"EWC series id: {series.get('id', '')}",
        f"Source: {source_url}",
        *slot_lines,
    ]
    return "\n".join(line for line in lines if not line.endswith(": "))


def series_duration_hours(series: dict, fallback_hours: int) -> int:
    best_of = (series.get("format") or {}).get("best_of")
    if best_of == 1:
        return 1
    if best_of == 3:
        return 3
    if best_of == 5:
        return 5
    return fallback_hours


def load_lolesports_module():
    module_path = Path(__file__).with_name("fetch-lolesports-calendar.py")
    spec = importlib.util.spec_from_file_location("lolesports_calendar", module_path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def lolesports_team_slot(team: dict, slot_number: int) -> dict:
    code = (team.get("code") or "").strip()
    name = (team.get("name") or "").strip()
    label = code or name or "TBD"
    if label.upper() == "TBD":
        return {"slot": slot_number, "source": {"label": "TBD"}, "competitor": None}
    return {
        "slot": slot_number,
        "source": {"label": label},
        "competitor": {
            "team": {
                "name": name or label,
                "short_name": code or name or label,
            }
        },
    }


def lolesports_event_to_series(event: dict) -> tuple[dict, dict]:
    match = event.get("match") or {}
    strategy = match.get("strategy") or {}
    best_of = strategy.get("count") if strategy.get("type") == "bestOf" else None
    block_name = event.get("blockName") or "Match"
    phase_name = {
        "그룹": "Group Stage",
        "8강": "Quarterfinal",
        "4강": "Semifinal",
        "결승": "Grand Final",
        "3위 결정전": "3rd place",
    }.get(block_name, block_name)
    teams = event.get("matchTeams") or []
    slots = [lolesports_team_slot(team, index + 1) for index, team in enumerate(teams[:2])]
    while len(slots) < 2:
        slots.append({"slot": len(slots) + 1, "source": {"label": "TBD"}, "competitor": None})

    phase = {"name": phase_name}
    series = {
        "id": f"lolesports-{event['id']}",
        "state": event.get("state") or match.get("state") or "",
        "scheduled_start": event.get("startTime"),
        "actual_start": None,
        "format": {"type": "BEST_OF", "best_of": best_of},
        "structure": {
            "label": phase_name,
            "round_name": phase_name,
            "bracket_type": "",
            "bracket_path": "",
        },
        "slots": slots,
    }
    return phase, series


def collect_lolesports_fallback_series(
    urls: str,
    start_after: datetime | None,
    start_before: datetime | None,
) -> list[tuple[dict, dict]]:
    lolesports = load_lolesports_module()
    events_by_id = {}
    for url in [part.strip() for part in urls.split(",") if part.strip()]:
        html_text = lolesports.fetch_text(url)
        lolesports.merge_events(
            events_by_id,
            lolesports.collect_events(
                html_text,
                {EWC_LOLESPORTS_LEAGUE_ID},
                set(),
                set(),
                start_after,
                start_before,
            ),
        )
    series_items = [
        lolesports_event_to_series(event)
        for event in sorted(events_by_id.values(), key=lambda item: item.get("startTime", ""))
    ]
    return [item for item in series_items if has_resolved_competitors(item[1])]


def collect_series(
    structures: list[dict],
    start_after: datetime | None,
    start_before: datetime | None,
) -> list[tuple[dict, dict]]:
    collected = []
    for structure in structures:
        phase = structure.get("phase") or {}
        for series in structure.get("series") or []:
            start_value = series.get("scheduled_start") or series.get("actual_start")
            if not start_value:
                continue
            start = parse_dt(start_value)
            if start_after and start < start_after:
                continue
            if start_before and start > start_before:
                continue
            collected.append((phase, series))
    return sorted(collected, key=lambda item: item[1].get("scheduled_start", ""))


def build_ics(
    series_items: list[tuple[dict, dict]],
    calendar_name: str,
    duration_hours: int,
    source_url: str,
    preserved_events: dict[str, list[str]] | None = None,
) -> str:
    now = format_utc(datetime.now(timezone.utc))
    preserved_events = preserved_events or {}
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Codex//EWC LoL Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(calendar_name)}",
    ]
    current_uids = set()
    current_start_counts = Counter()
    for phase, series in series_items:
        start = parse_dt(series["scheduled_start"] or series["actual_start"])
        end = start + timedelta(hours=series_duration_hours(series, duration_hours))
        uid = f"ewc-lol-{series['id']}@esportsworldcup.com"
        current_uids.add(uid)
        current_start_counts[format_utc(start)] += 1
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now}",
                f"DTSTART:{format_utc(start)}",
                f"DTEND:{format_utc(end)}",
                f"SUMMARY:{ics_escape(series_title(series))}",
                f"DESCRIPTION:{ics_escape(series_description(phase, series, source_url))}",
                f"URL:{source_url}",
                "END:VEVENT",
            ]
        )
    for uid in sorted(preserved_events):
        if uid in current_uids:
            continue
        start_key = event_block_start_key(preserved_events[uid])
        if start_key and current_start_counts[start_key] > 0:
            current_start_counts[start_key] -= 1
            continue
        lines.extend(preserved_events[uid])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def count_preserved_events(
    series_items: list[tuple[dict, dict]], preserved_events: dict[str, list[str]]
) -> int:
    current_uids = set()
    current_start_counts = Counter()
    for _, series in series_items:
        start = parse_dt(series["scheduled_start"] or series["actual_start"])
        current_uids.add(f"ewc-lol-{series['id']}@esportsworldcup.com")
        current_start_counts[format_utc(start)] += 1

    preserved_count = 0
    for uid in sorted(preserved_events):
        if uid in current_uids:
            continue
        start_key = event_block_start_key(preserved_events[uid])
        if start_key and current_start_counts[start_key] > 0:
            current_start_counts[start_key] -= 1
            continue
        preserved_count += 1
    return preserved_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an ICS calendar from the official EWC League of Legends page."
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", default="ewc-lol.ics")
    parser.add_argument("--duration-hours", type=int, default=3)
    parser.add_argument("--calendar-name", default="EWC")
    parser.add_argument("--lolesports-fallback-url", default=DEFAULT_LOLESPORTS_FALLBACK_URL)
    parser.add_argument("--from-days", type=int, default=-7)
    parser.add_argument("--to-days", type=int, default=30)
    parser.add_argument(
        "--fallback-next-years",
        type=int,
        default=1,
        help=(
            "Try the same competition slug with later year URLs when the primary "
            "URL has no events in the selected date window."
        ),
    )
    parser.add_argument(
        "--merge-existing-ics",
        default="",
        help="Existing ICS file whose future VEVENTs should be preserved if missing from the latest scrape.",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    start_after = now + timedelta(days=args.from_days) if args.from_days is not None else None
    start_before = now + timedelta(days=args.to_days) if args.to_days is not None else None

    series_items = []
    source_url = args.url
    first_successful_result = None
    failures = []
    for candidate_url in fallback_urls(args.url, args.fallback_next_years):
        try:
            html_text = fetch_text(candidate_url)
            structures = extract_structures(html_text)
            candidate_series = collect_series(structures, start_after, start_before)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            failures.append(f"{candidate_url}: {error}")
            continue

        if first_successful_result is None:
            first_successful_result = (candidate_url, candidate_series)
        if candidate_series:
            source_url = candidate_url
            series_items = candidate_series
            break

    if not series_items and first_successful_result is not None:
        source_url, series_items = first_successful_result
    if first_successful_result is None:
        try:
            series_items = collect_lolesports_fallback_series(
                args.lolesports_fallback_url,
                start_after,
                start_before,
            )
            if series_items:
                source_url = args.lolesports_fallback_url
                print(
                    f"Could not load any EWC LoL source URL. Using {len(series_items)} LoL Esports fallback events."
                )
        except (HTTPError, URLError, TimeoutError, ValueError, RuntimeError) as error:
            failures.append(f"{args.lolesports_fallback_url}: {error}")

        if not series_items:
            existing_path = Path(args.merge_existing_ics)
            if args.merge_existing_ics and existing_path.exists():
                Path(args.output).write_text(
                    normalize_existing_ics(
                        existing_path.read_text(encoding="utf-8"), args.calendar_name
                    ),
                    encoding="utf-8",
                    newline="",
                )
                print(
                    f"Could not load any EWC LoL source URL. Copied existing ICS to {args.output}"
                )
                for failure in failures:
                    print(f"Skipped fallback URL: {failure}")
                return 0
            for failure in failures:
                print(f"Skipped fallback URL: {failure}")
            raise RuntimeError(
                "Could not load any EWC LoL source URL:\n" + "\n".join(failures)
            )

    preserved_events = load_existing_future_events(args.merge_existing_ics, now)
    ics = build_ics(
        series_items,
        args.calendar_name,
        args.duration_hours,
        source_url,
        preserved_events,
    )
    Path(args.output).write_text(ics, encoding="utf-8", newline="")
    preserved_count = count_preserved_events(series_items, preserved_events)
    print(
        f"Wrote {len(series_items)} scraped series from {source_url} and preserved {preserved_count} existing events to {args.output}"
    )
    for failure in failures:
        print(f"Skipped fallback URL: {failure}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
