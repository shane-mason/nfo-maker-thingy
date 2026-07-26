import argparse
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

MUSICBRAINZ_API_URL = "https://musicbrainz.org/ws/2/recording"
TMDB_API_URL = "https://api.themoviedb.org/3"
USER_AGENT = "nfothingy/1.0 ( user@example.com )"

# TMDB v3 API key. Required for the `movie` and `episode` subcommands.
# Get one for free at https://www.themoviedb.org/settings/api
TMDB_API_KEY = "058faffac6f7ae1061eafa4ef0b3e4b6"

RATE_LIMIT_SECONDS = 1.0
SCORE_TRUST_THRESHOLD = 90
SCORE_MIN_THRESHOLD = 60
TAG_MIN_COUNT = 1
MAX_GENRES = 3
MAX_ACTORS = 15
DEFAULT_RESULTS = 5

_last_request_time: float = 0.0


# Parenthetical/bracketed noise commonly appended to music video filenames
_TITLE_NOISE = re.compile(
    r'\s*[([][^)\]]*\b(?:official|video|audio|lyrics?|lyric|hd|4k|mv|vevo|visualizer|live|version)\b[^)\]]*[)\]]',
    re.IGNORECASE,
)


def _clean_title(title: str) -> str:
    return _TITLE_NOISE.sub("", title).strip()


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


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


def _format_duration(ms: int) -> str:
    total_seconds = ms // 1000
    m, s = divmod(total_seconds, 60)
    return f"{m}:{s:02d}"


def write_nfo(video_path: str | Path, tree: ET.ElementTree, overwrite: bool) -> Path | None:
    nfo_path = Path(video_path).with_suffix(".nfo")
    if nfo_path.exists() and not overwrite:
        print(f"  skipped (NFO exists): {nfo_path}", file=sys.stderr)
        return None
    tree.write(str(nfo_path), encoding="unicode", xml_declaration=True)
    return nfo_path


def _finalize_tree(root: ET.Element) -> ET.ElementTree:
    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    return tree


def _add_ratings(root: ET.Element, name: str, value, votes) -> None:
    """Emit a Kodi <ratings> block (0-10 scale)."""
    if value in (None, "", 0):
        return
    ratings = ET.SubElement(root, "ratings")
    rating = ET.SubElement(ratings, "rating")
    rating.set("name", name)
    rating.set("max", "10")
    rating.set("default", "true")
    ET.SubElement(rating, "value").text = f"{float(value):.1f}"
    if votes:
        ET.SubElement(rating, "votes").text = str(votes)


def _add_uniqueid(root: ET.Element, id_type: str, value, default: bool = False) -> None:
    if not value:
        return
    el = ET.SubElement(root, "uniqueid")
    el.set("type", id_type)
    if default:
        el.set("default", "true")
    el.text = str(value)


# ---------------------------------------------------------------------------
# Music videos (MusicBrainz -> <musicvideo>)
# ---------------------------------------------------------------------------


def parse_musicvideo_filename(path: str | Path) -> dict:
    stem = Path(path).stem
    # Match a dash with at least one space on either side, e.g. " - ", " -", "- "
    parts = re.split(r'\s+-\s*|\s*-\s+', stem, maxsplit=1)
    if len(parts) == 2:
        return {"artist": parts[0].strip(), "title": _clean_title(parts[1]), "raw_stem": stem}
    return {"artist": "", "title": _clean_title(stem), "raw_stem": stem}


def _pick_best_recording(recordings: list, artist: str, title: str) -> dict | None:
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
    best = _pick_best_recording(recordings, artist, title)
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


def build_musicvideo_nfo(metadata: dict) -> ET.ElementTree:
    root = ET.Element("musicvideo")

    def _add(tag: str, text: str | None) -> None:
        if text:
            ET.SubElement(root, tag).text = text

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

    return _finalize_tree(root)


# ---------------------------------------------------------------------------
# TMDB shared
# ---------------------------------------------------------------------------


def _tmdb_get(path: str, params: dict | None = None) -> dict | None:
    if not TMDB_API_KEY:
        print(
            "  WARNING: TMDB_API_KEY is not set; edit the constant at the top of "
            "nfothingy.py",
            file=sys.stderr,
        )
        return None

    _wait_for_rate_limit()
    full_params = {"api_key": TMDB_API_KEY}
    if params:
        full_params.update(params)

    try:
        response = requests.get(
            f"{TMDB_API_URL}{path}",
            params=full_params,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"  WARNING: TMDB request failed: {exc}", file=sys.stderr)
        return None

    return response.json()


# ---------------------------------------------------------------------------
# Movies (TMDB -> <movie>)
# ---------------------------------------------------------------------------


def parse_movie_filename(path: str | Path) -> dict:
    stem = Path(path).stem

    # Prefer an explicitly bracketed/parenthesized year, e.g. "Title (2019)".
    m = re.search(r'[([](19\d{2}|20\d{2})[)\]]', stem)
    if m:
        title = stem[: m.start()].strip(" .-_")
        year = m.group(1)
    else:
        # Fall back to the first standalone year token (skip the leading token
        # so titles like "2001 A Space Odyssey" aren't mistaken for a year).
        tokens = re.split(r'[.\s_]+', stem)
        year = None
        cut = len(tokens)
        for i, tok in enumerate(tokens[1:], start=1):
            if re.fullmatch(r'19\d{2}|20\d{2}', tok):
                year, cut = tok, i
                break
        title = " ".join(tokens[:cut]).strip()

    return {"title": title, "year": year, "raw_stem": stem}


def _pick_best_movie(results: list, title: str, year: str | None) -> dict | None:
    if not results:
        return None

    norm = _normalize(title)

    def parts(r: dict) -> tuple[bool, bool]:
        title_ok = _normalize(r.get("title", "")) == norm
        release_year = (r.get("release_date") or "")[:4]
        year_ok = bool(year) and release_year == str(year)
        return title_ok, year_ok

    # Exact title (+ year when we have one).
    for r in results:
        title_ok, year_ok = parts(r)
        if title_ok and (year_ok or not year):
            return r
    # Exact title, any year.
    for r in results:
        if parts(r)[0]:
            return r
    # Year match with a loosely matching title.
    if year:
        for r in results:
            if parts(r)[1]:
                return r
    # Loose containment against the top result.
    top = _normalize(results[0].get("title", ""))
    if norm and (norm in top or top in norm):
        return results[0]
    return None


def fetch_movie(title: str, year: str | None) -> dict | None:
    params = {"query": title}
    if year:
        params["year"] = year

    data = _tmdb_get("/search/movie", params)
    if not data:
        return None

    best = _pick_best_movie(data.get("results", []), title, year)
    if best is None:
        return None

    details = _tmdb_get(
        f"/movie/{best['id']}", {"append_to_response": "credits,external_ids"}
    ) or best

    credits = details.get("credits", {})
    cast = credits.get("cast", [])[:MAX_ACTORS]
    actors = [
        {"name": c.get("name"), "role": c.get("character"), "order": c.get("order", 0)}
        for c in cast
        if c.get("name")
    ]
    directors = [
        c.get("name")
        for c in credits.get("crew", [])
        if c.get("job") == "Director" and c.get("name")
    ]

    release_date = details.get("release_date") or ""
    external = details.get("external_ids", {})

    return {
        "title": details.get("title"),
        "originaltitle": details.get("original_title"),
        "year": release_date[:4] or None,
        "premiered": release_date or None,
        "plot": details.get("overview"),
        "tagline": details.get("tagline"),
        "runtime": details.get("runtime") or None,
        "genres": [g["name"] for g in details.get("genres", [])],
        "directors": directors,
        "actors": actors,
        "rating": details.get("vote_average"),
        "votes": details.get("vote_count"),
        "tmdb_id": details.get("id"),
        "imdb_id": external.get("imdb_id") or details.get("imdb_id"),
    }


def build_movie_nfo(m: dict) -> ET.ElementTree:
    root = ET.Element("movie")

    def _add(tag: str, text) -> None:
        if text not in (None, ""):
            ET.SubElement(root, tag).text = str(text)

    _add("title", m.get("title"))
    _add("originaltitle", m.get("originaltitle"))
    _add("year", m.get("year"))
    _add("plot", m.get("plot"))
    _add("tagline", m.get("tagline"))
    _add("runtime", m.get("runtime"))  # minutes, per Kodi convention
    _add("premiered", m.get("premiered"))

    for genre in m.get("genres") or []:
        _add("genre", genre)
    for director in m.get("directors") or []:
        _add("director", director)

    _add_ratings(root, "themoviedb", m.get("rating"), m.get("votes"))

    for a in m.get("actors") or []:
        actor = ET.SubElement(root, "actor")
        ET.SubElement(actor, "name").text = a["name"]
        if a.get("role"):
            ET.SubElement(actor, "role").text = a["role"]
        ET.SubElement(actor, "order").text = str(a.get("order", 0))

    _add_uniqueid(root, "tmdb", m.get("tmdb_id"), default=True)
    _add_uniqueid(root, "imdb", m.get("imdb_id"))

    return _finalize_tree(root)


# ---------------------------------------------------------------------------
# TV episodes (TMDB -> <episodedetails>)
# ---------------------------------------------------------------------------


_EPISODE_PATTERNS = [
    re.compile(
        r'^(?P<show>.*?)[\s._-]+[Ss](?P<season>\d{1,2})[\s._-]?[Ee](?P<episode>\d{1,3})'
        r'(?:[\s._-]+(?P<title>.*))?$'
    ),
    re.compile(
        r'^(?P<show>.*?)[\s._-]+(?P<season>\d{1,2})x(?P<episode>\d{1,3})'
        r'(?:[\s._-]+(?P<title>.*))?$'
    ),
]


def parse_episode_filename(path: str | Path) -> dict:
    stem = Path(path).stem
    for pattern in _EPISODE_PATTERNS:
        m = pattern.match(stem)
        if m:
            show = re.sub(r'[.\s_]+', ' ', m.group("show")).strip(" -")
            title = m.group("title")
            if title:
                title = re.sub(r'[.\s_]+', ' ', title).strip(" -") or None
            return {
                "show": show,
                "season": int(m.group("season")),
                "episode": int(m.group("episode")),
                "episode_title": title,
                "raw_stem": stem,
            }
    return {
        "show": "",
        "season": None,
        "episode": None,
        "episode_title": None,
        "raw_stem": stem,
    }


def _pick_best_tv(results: list, show: str) -> dict | None:
    if not results:
        return None
    norm = _normalize(show)
    for r in results:
        if _normalize(r.get("name", "")) == norm:
            return r
    return results[0]


def fetch_episode(show: str, season: int, episode: int) -> dict | None:
    data = _tmdb_get("/search/tv", {"query": show})
    if not data:
        return None

    best = _pick_best_tv(data.get("results", []), show)
    if best is None:
        return None

    ep = _tmdb_get(f"/tv/{best['id']}/season/{season}/episode/{episode}")
    if not ep or ep.get("success") is False:
        return None

    return {
        "title": ep.get("name"),
        "showtitle": best.get("name") or show,
        "season": season,
        "episode": episode,
        "plot": ep.get("overview"),
        "aired": ep.get("air_date"),
        "runtime": ep.get("runtime") or None,
        "rating": ep.get("vote_average"),
        "votes": ep.get("vote_count"),
        "tmdb_id": ep.get("id"),
    }


def build_episode_nfo(e: dict) -> ET.ElementTree:
    root = ET.Element("episodedetails")

    def _add(tag: str, text) -> None:
        if text not in (None, ""):
            ET.SubElement(root, tag).text = str(text)

    _add("title", e.get("title"))
    _add("showtitle", e.get("showtitle"))
    _add("season", e.get("season"))
    _add("episode", e.get("episode"))
    _add("plot", e.get("plot"))
    _add("aired", e.get("aired"))
    _add("runtime", e.get("runtime"))  # minutes, per Kodi convention

    _add_ratings(root, "themoviedb", e.get("rating"), e.get("votes"))
    _add_uniqueid(root, "tmdb", e.get("tmdb_id"), default=True)

    return _finalize_tree(root)


# ---------------------------------------------------------------------------
# Per-type processing
# ---------------------------------------------------------------------------


def _process_musicvideo(file_path: str, overwrite: bool) -> None:
    parsed = parse_musicvideo_filename(file_path)
    if not parsed["artist"] and not parsed["title"]:
        print("  skipped: could not parse filename", file=sys.stderr)
        return

    metadata = search_recording(parsed["artist"], parsed["title"])
    if metadata is None:
        print(
            f"  skipped: no confident MusicBrainz match for "
            f'"{parsed["artist"]} - {parsed["title"]}"',
            file=sys.stderr,
        )
        return

    nfo_path = write_nfo(file_path, build_musicvideo_nfo(metadata), overwrite)
    if nfo_path:
        print(f"  wrote: {nfo_path}", file=sys.stderr)


def _process_movie(file_path: str, overwrite: bool) -> None:
    parsed = parse_movie_filename(file_path)
    if not parsed["title"]:
        print("  skipped: could not parse filename", file=sys.stderr)
        return

    metadata = fetch_movie(parsed["title"], parsed["year"])
    if metadata is None:
        label = parsed["title"] + (f" ({parsed['year']})" if parsed["year"] else "")
        print(f'  skipped: no confident TMDB match for "{label}"', file=sys.stderr)
        return

    nfo_path = write_nfo(file_path, build_movie_nfo(metadata), overwrite)
    if nfo_path:
        print(f"  wrote: {nfo_path}", file=sys.stderr)


def _process_episode(file_path: str, overwrite: bool) -> None:
    parsed = parse_episode_filename(file_path)
    if not parsed["show"] or parsed["season"] is None:
        print(
            "  skipped: could not parse show/season/episode from filename",
            file=sys.stderr,
        )
        return

    metadata = fetch_episode(parsed["show"], parsed["season"], parsed["episode"])
    if metadata is None:
        print(
            f'  skipped: no confident TMDB match for "{parsed["show"]} '
            f'S{parsed["season"]:02d}E{parsed["episode"]:02d}"',
            file=sys.stderr,
        )
        return

    nfo_path = write_nfo(file_path, build_episode_nfo(metadata), overwrite)
    if nfo_path:
        print(f"  wrote: {nfo_path}", file=sys.stderr)


_HANDLERS = {
    "musicvideo": _process_musicvideo,
    "movie": _process_movie,
    "episode": _process_episode,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Kodi-compatible NFO files for music videos, movies, "
        "and TV episodes."
    )
    subparsers = parser.add_subparsers(dest="type", required=True, metavar="type")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("files", nargs="+", help="Media file(s)")
    common.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing NFO files (default: skip)",
    )

    subparsers.add_parser(
        "musicvideo", parents=[common], help="Music videos (MusicBrainz)"
    )
    subparsers.add_parser("movie", parents=[common], help="Movies (TMDB)")
    subparsers.add_parser("episode", parents=[common], help="TV episodes (TMDB)")

    args = parser.parse_args()
    handler = _HANDLERS[args.type]

    for file_path in args.files:
        print(f"Processing: {file_path}", file=sys.stderr)
        try:
            handler(file_path, args.overwrite)
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
