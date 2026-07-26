# nfo-maker-thingy

Generates Kodi-compatible `.nfo` files for **music videos, movies, and TV episodes**. Parses metadata hints from the filename, queries an online database, and writes the result as XML next to the original file.

Each media type is a subcommand with its own metadata source and NFO schema:

| Subcommand | Source | NFO root | API key |
|---|---|---|---|
| `musicvideo` | [MusicBrainz](https://musicbrainz.org/) | `<musicvideo>` | none |
| `movie` | [TMDB](https://www.themoviedb.org/) | `<movie>` | required |
| `episode` | [TMDB](https://www.themoviedb.org/) | `<episodedetails>` | required |

## Requirements

Python 3.10+ and `requests`:

```bash
pip install requests
```

For the `movie` and `episode` subcommands, set the `TMDB_API_KEY` constant near the top of `nfothingy.py`. A free v3 API key is available at <https://www.themoviedb.org/settings/api>.

## Usage

A media type is **required** as the first argument:

```bash
python nfothingy.py musicvideo "Artist - Song Title.mp4" *.mp4
python nfothingy.py movie "The Matrix (1999).mkv" *.mkv
python nfothingy.py episode "Breaking Bad - S01E02 - Cat in the Bag.mkv" *.mkv
```

By default, existing `.nfo` files are skipped. Pass `--overwrite` to replace them:

```bash
python nfothingy.py movie --overwrite *.mkv
```

## Filename formats

### Music videos

`Artist Name - Song Title.ext`. The separator is a dash with at least one space on either side. Common noise suffixes are stripped before the MusicBrainz query.

| Filename | Artist | Title queried |
|---|---|---|
| `Radiohead - Creep.mp4` | Radiohead | Creep |
| `Beck - Loser(Official Music Video).mp4` | Beck | Loser |
| `R.E.M. - Driver 8 [Official Video].mkv` | R.E.M. | Driver 8 |

If no separator is found, the whole stem is used as the title with no artist.

### Movies

`Title (Year).ext` is the most reliable form. A bracketed/parenthesized year always wins; otherwise the first standalone year token is used.

| Filename | Title | Year |
|---|---|---|
| `The Matrix (1999).mkv` | The Matrix | 1999 |
| `2001 A Space Odyssey (1968).mkv` | 2001 A Space Odyssey | 1968 |
| `The.Godfather.1972.1080p.mkv` | The Godfather | 1972 |

> Note: a title containing a year alongside a release year (e.g. `Blade.Runner.2049.2017`) is ambiguous without brackets — the first year token wins. Use `Blade Runner 2049 (2017)` for a reliable match.

### TV episodes

`Show - S01E02 - Title.ext`, `Show.S01E02.ext`, or `Show 1x02.ext`. The show name, season, and episode number are extracted; the real episode title, plot, and air date come from TMDB.

| Filename | Show | Season | Episode |
|---|---|---|---|
| `Breaking Bad - S01E02 - Cat in the Bag.mkv` | Breaking Bad | 1 | 2 |
| `The.Office.S03E10.HDTV.mkv` | The Office | 3 | 10 |
| `Firefly 1x05.mkv` | Firefly | 1 | 5 |

## Output

A `.nfo` file is written alongside the media file with the same base name.

### `<musicvideo>`

```xml
<?xml version='1.0' encoding='us-ascii'?>
<musicvideo>
  <title>Creep</title>
  <artist>Radiohead</artist>
  <album>Pablo Honey</album>
  <year>1992</year>
  <premiered>1992-09-21</premiered>
  <runtime>3:58</runtime>
  <genre>alternative rock</genre>
  <musicbrainzartistid>a74b1b7f-...</musicbrainzartistid>
  <musicbrainztrackid>8f3e8c6b-...</musicbrainztrackid>
</musicvideo>
```

### `<movie>`

```xml
<?xml version='1.0' encoding='us-ascii'?>
<movie>
  <title>The Matrix</title>
  <originaltitle>The Matrix</originaltitle>
  <year>1999</year>
  <plot>A computer hacker learns the truth about his reality.</plot>
  <tagline>Free your mind.</tagline>
  <runtime>136</runtime>
  <premiered>1999-03-30</premiered>
  <genre>Action</genre>
  <genre>Science Fiction</genre>
  <director>Lana Wachowski</director>
  <director>Lilly Wachowski</director>
  <ratings>
    <rating name="themoviedb" max="10" default="true">
      <value>8.2</value>
      <votes>24000</votes>
    </rating>
  </ratings>
  <actor>
    <name>Keanu Reeves</name>
    <role>Neo</role>
    <order>0</order>
  </actor>
  <uniqueid type="tmdb" default="true">603</uniqueid>
  <uniqueid type="imdb">tt0133093</uniqueid>
</movie>
```

Movie and episode `<runtime>` values are in **minutes** (Kodi's convention), unlike the music-video `M:SS` string.

### `<episodedetails>`

```xml
<?xml version='1.0' encoding='us-ascii'?>
<episodedetails>
  <title>Cat in the Bag...</title>
  <showtitle>Breaking Bad</showtitle>
  <season>1</season>
  <episode>2</episode>
  <plot>Walt and Jesse have bodies to dispose of.</plot>
  <aired>2008-01-27</aired>
  <runtime>48</runtime>
  <ratings>
    <rating name="themoviedb" max="10" default="true">
      <value>8.5</value>
      <votes>3000</votes>
    </rating>
  </ratings>
  <uniqueid type="tmdb" default="true">62103</uniqueid>
</episodedetails>
```

Only per-episode files are generated; Kodi can scrape the show-level `tvshow.nfo` itself.

## Matching

- **Music videos** are scored using the MusicBrainz relevance score plus bonuses for exact artist/title matches; below a threshold the file is skipped with a warning.
- **Movies** prefer an exact title match with a matching year, falling back to title-only, then year-only, then a loose containment check against the top result.
- **Episodes** match the show by exact normalized name (falling back to the top TMDB result), then fetch the specific season/episode.

Requests are rate-limited to one per second.

## Configuration

Constants at the top of `nfothingy.py` can be adjusted:

| Constant | Default | Description |
|---|---|---|
| `TMDB_API_KEY` | `""` | TMDB v3 API key; required for `movie`/`episode` |
| `USER_AGENT` | `nfothingy/1.0 (...)` | Sent with every request |
| `SCORE_TRUST_THRESHOLD` | `90` | Music video score accepted without further comparison |
| `SCORE_MIN_THRESHOLD` | `60` | Music video score below which the file is skipped |
| `MAX_GENRES` | `3` | Maximum genre tags for music videos |
| `MAX_ACTORS` | `15` | Maximum cast members included in movie NFOs |
| `TAG_MIN_COUNT` | `1` | Minimum MusicBrainz tag vote count to include a genre |
