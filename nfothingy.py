import argparse
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording"
USER_AGENT = "nfothingy/1.0 ( user@example.com )"
RATE_LIMIT_SECONDS = 1.0
SCORE_TRUST_THRESHOLD = 90
SCORE_MIN_THRESHOLD = 60
TAG_MIN_COUNT = 1
MAX_GENRES = 3
DEFAULT_RESULTS = 5

_last_request_time: float = 0.0


# Parenthetical/bracketed noise commonly appended to music video filenames
_TITLE_NOISE = re.compile(
    r'\s*[([][^)\]]*\b(?:official|video|audio|lyrics?|lyric|hd|4k|mv|vevo|visualizer|live|version)\b[^)\]]*[)\]]',
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    return _TITLE_NOISE.sub("", title).strip()


def parse_filename(path: str | Path) -> dict:

    stem = Path(path).stem
    # Match a dash with at least one space on either side, e.g. " - ", " -", "- "
    parts = re.split(r'\s+-\s*|\s*-\s+', stem, maxsplit=1)
    if len(parts) == 2:
        return {"artist": parts[0].strip(), "title": _clean_title(parts[1]), "raw_stem": stem}
    return {"artist": "", "title": _clean_title(stem), "raw_stem": stem}



def _wait_for_rate_limit() -> None:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_request_time = time.monotonic()



def _normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return "".join(c for c in text if c.isalnum() or c.isspace()).strip()


def _pick_best_result(recordings: list, artist: str, title: str) -> dict | None:
    if not recordings:
        return None

    norm_artist = _normalize(artist)
    norm_title = _normalize(title)

    def score(rec: dict) -> int:
        base = int(rec.get("score", 0))
        if base >= SCORE_TRUST_THRESHOLD:
            return base
        bonus = 0
        rec_title = _normalize(rec.get("title", ""))
        if rec_title == norm_title:
            bonus += 10
        artist_credits = rec.get("artist-credit", [])
        if artist_credits:
            rec_artist = _normalize(artist_credits[0].get("name", ""))
            if rec_artist == norm_artist:
                bonus += 5
        if rec.get("first-release-date"):
            bonus += 2
        return base + bonus

    best = max(recordings, key=score)
    if int(best.get("score", 0)) < SCORE_MIN_THRESHOLD:
        return None
    return best


def search_recording(artist: str, title: str) -> dict | None:
    _wait_for_rate_limit()

    query_parts = []
    if artist:
        query_parts.append(f'artist:"{artist}"')
    if title:
        query_parts.append(f'recording:"{title}"')
    query = " AND ".join(query_parts) if query_parts else title

    params = {
        "query": query,
        "fmt": "json",
        "limit": DEFAULT_RESULTS,
        "inc": "releases+artist-credits+tags",
    }

    try:
        response = requests.get(
            MUSICBRAINZ_API_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"  WARNING: MusicBrainz request failed: {exc}", file=sys.stderr)
        return None

    data = response.json()
    recordings = data.get("recordings", [])
    best = _pick_best_result(recordings, artist, title)
    if best is None:
        return None

    # Extract artist credits
    artist_credits = best.get("artist-credit", [])
    artists = [c["name"] for c in artist_credits if isinstance(c, dict) and "name" in c]
    artist_mbid = artist_credits[0]["artist"]["id"] if artist_credits and "artist" in artist_credits[0] else None

    # Extract tags as genres
    tags = best.get("tags", [])
    tags_sorted = sorted(
        (t for t in tags if t.get("count", 0) >= TAG_MIN_COUNT),
        key=lambda t: t["count"],
        reverse=True,
    )
    genres = [t["name"] for t in tags_sorted[:MAX_GENRES]]

    # Extract release info
    releases = best.get("releases", [])
    album = releases[0]["title"] if releases else None

    # Dates
    release_date = best.get("first-release-date", "") or ""
    year = release_date[:4] if release_date else None
    premiered = release_date if len(release_date) >= 8 else None

    # Duration
    length_ms = best.get("length")
    runtime = _format_duration(length_ms) if length_ms else None

    return {
        "title": best.get("title"),
        "artists": artists,
        "album": album,
        "year": year,
        "premiered": premiered,
        "runtime": runtime,
        "genres": genres,
        "artist_mbid": artist_mbid,
        "recording_mbid": best.get("id"),
    }



def _format_duration(ms: int) -> str:
    total_seconds = ms // 1000
    m, s = divmod(total_seconds, 60)
    return f"{m}:{s:02d}"


def build_nfo(metadata: dict) -> ET.ElementTree:
    root = ET.Element("musicvideo")

    def _add(tag: str, text: str | None) -> None:
        if text:
            el = ET.SubElement(root, tag)
            el.text = text

    _add("title", metadata.get("title"))

    for artist in metadata.get("artists") or []:
        _add("artist", artist)

    _add("album", metadata.get("album"))
    _add("year", metadata.get("year"))
    _add("premiered", metadata.get("premiered"))
    _add("runtime", metadata.get("runtime"))

    for genre in metadata.get("genres") or []:
        _add("genre", genre)

    _add("musicbrainzartistid", metadata.get("artist_mbid"))
    _add("musicbrainztrackid", metadata.get("recording_mbid"))

    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    return tree


def write_nfo(video_path: str | Path, tree: ET.ElementTree, overwrite: bool) -> Path | None:
    nfo_path = Path(video_path).with_suffix(".nfo")
    if nfo_path.exists() and not overwrite:
        print(f"  skipped (NFO exists): {nfo_path}", file=sys.stderr)
        return None
    tree.write(str(nfo_path), encoding="unicode", xml_declaration=True)
    return nfo_path



def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Kodi-compatible NFO files for music videos."
    )
    parser.add_argument("files", nargs="+", help="Music video file(s)")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing NFO files (default: skip)",
    )
    args = parser.parse_args()

    for file_path in args.files:
        print(f"Processing: {file_path}", file=sys.stderr)
        try:
            parsed = parse_filename(file_path)
            if not parsed["artist"] and not parsed["title"]:
                print("  skipped: could not parse filename", file=sys.stderr)
                continue

            metadata = search_recording(parsed["artist"], parsed["title"])
            if metadata is None:
                print(
                    f"  skipped: no confident MusicBrainz match for "
                    f'"{parsed["artist"]} - {parsed["title"]}"',
                    file=sys.stderr,
                )
                continue

            tree = build_nfo(metadata)
            nfo_path = write_nfo(file_path, tree, overwrite=args.overwrite)
            if nfo_path:
                print(f"  wrote: {nfo_path}", file=sys.stderr)

        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
