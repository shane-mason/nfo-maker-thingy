# nfo-maker-thingy

Generates Kodi-compatible `.nfo` files for music videos. Parses artist and title from the filename, queries [MusicBrainz](https://musicbrainz.org/) for metadata, and writes the result as XML next to the original file.

## Requirements

Python 3.10+ and `requests`:

```bash
pip install requests
```

## Usage

```bash
python nfothingy.py "Artist - Song Title.mp4"
python nfothingy.py *.mp4
python nfothingy.py --overwrite *.mkv
```

By default, existing `.nfo` files are skipped. Pass `--overwrite` to replace them.

## Filename format

Files should be named `Artist Name - Song Title.ext`. The separator is a dash with at least one space on either side. Common noise suffixes are stripped automatically before the MusicBrainz query:

| Filename | Artist | Title queried |
|---|---|---|
| `Radiohead - Creep.mp4` | Radiohead | Creep |
| `Beck - Loser(Official Music Video).mp4` | Beck | Loser |
| `Counting Crows -Mr Jones.mp4` | Counting Crows | Mr Jones |
| `R.E.M. - Driver 8 [Official Video].mkv` | R.E.M. | Driver 8 |

If no separator is found, the whole filename stem is used as the title with no artist.

## Output

A `.nfo` file is written alongside the video with the same base name:

```
Radiohead - Creep.mp4
Radiohead - Creep.nfo
```

Example NFO contents:

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
  <genre>britpop</genre>
  <musicbrainzartistid>a74b1b7f-71a5-4011-9441-d0b5e4122711</musicbrainzartistid>
  <musicbrainztrackid>8f3e8c6b-...</musicbrainztrackid>
</musicvideo>
```

## MusicBrainz matching

Results are scored using the MusicBrainz relevance score, with bonuses for exact artist and title matches. If the best match scores below 60, the file is skipped and a warning is printed. Requests are rate-limited to one per second per MusicBrainz's API requirements.

## Configuration

Constants at the top of `nfothingy.py` can be adjusted:

| Constant | Default | Description |
|---|---|---|
| `USER_AGENT` | `nfothingy/1.0 (...)` | Sent with every MusicBrainz request |
| `SCORE_TRUST_THRESHOLD` | `90` | Score above which the top result is accepted without further comparison |
| `SCORE_MIN_THRESHOLD` | `60` | Score below which the file is skipped |
| `MAX_GENRES` | `3` | Maximum number of genre tags to include |
| `TAG_MIN_COUNT` | `1` | Minimum MusicBrainz tag vote count to include a genre |