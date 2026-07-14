#!/usr/bin/env python3
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen


DEFAULT_URL = "https://esportsworldcup.com/en/competitions/2026/league-of-legends"


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
        return f"EWC LoL - {names[0]} vs {names[1]}"
    return f"EWC LoL - {label}"


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
    for phase, series in series_items:
        start = parse_dt(series["scheduled_start"] or series["actual_start"])
        end = start + timedelta(hours=duration_hours)
        uid = f"ewc-lol-{series['id']}@esportsworldcup.com"
        current_uids.add(uid)
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
        if uid not in current_uids:
            lines.extend(preserved_events[uid])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create an ICS calendar from the official EWC League of Legends page."
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", default="ewc-lol.ics")
    parser.add_argument("--duration-hours", type=int, default=3)
    parser.add_argument("--calendar-name", default="EWC LoL")
    parser.add_argument("--from-days", type=int, default=-7)
    parser.add_argument("--to-days", type=int, default=30)
    parser.add_argument(
        "--merge-existing-ics",
        default="",
        help="Existing ICS file whose future VEVENTs should be preserved if missing from the latest scrape.",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    start_after = now + timedelta(days=args.from_days) if args.from_days is not None else None
    start_before = now + timedelta(days=args.to_days) if args.to_days is not None else None

    html_text = fetch_text(args.url)
    structures = extract_structures(html_text)
    series_items = collect_series(structures, start_after, start_before)
    preserved_events = load_existing_future_events(args.merge_existing_ics, now)
    ics = build_ics(
        series_items,
        args.calendar_name,
        args.duration_hours,
        args.url,
        preserved_events,
    )
    Path(args.output).write_text(ics, encoding="utf-8", newline="")
    current_uids = {f"ewc-lol-{series['id']}@esportsworldcup.com" for _, series in series_items}
    preserved_count = len([uid for uid in preserved_events if uid not in current_uids])
    print(
        f"Wrote {len(series_items)} scraped series and preserved {preserved_count} existing events to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
