# Daily brief

A personal "AI homepage". It is rebuilt once a day and served by GitHub Pages.

Live at https://cmrlmatthews-max.github.io/daily-brief/

## How it works

1. GitHub Actions runs `brief.py` every morning.
2. The script reads the feeds listed in `sources.yaml`.
3. It sends the items to Claude. Claude picks the best ones and writes a short note for each.
4. Claude only returns item ids. The script maps them back to the real titles and links. Unknown ids are dropped, so links are never invented.
5. Team scores come straight from ESPN. No AI is involved.
6. The script writes `site/brief.json`. The page `site/index.html` shows it.

## The page

The page is a vintage newspaper called CMRLM, in one light theme: aged newsprint, near-black ink, and a muted red-brown accent. The masthead is the image `site/img/masthead.png`. Team scores sit in a box at its left and the edition in a box at its right. It has one top story, a few front-page stories, and four sections in this order: Tech & AI, Sports, Architecture, World.

The top story has a giant headline and 2 to 3 short `decks` under it. Claude writes the decks from the item title and summary only. If the lead is swapped for a story with a picture, the decks are left empty and the page copes.

The lead note is 120 to 150 words (4 to 6 sentences), written by Claude only from the item's title and summary. Front and section notes stay one short sentence.

Nothing on the page is left empty. After the page is built, after fonts load, after each picture loads or fails, and on resize, a small fill script in `site/index.html` evens out the blocks. It only sets CSS variables: it scales the type to fill the ears, makes the top-story, section and World columns end level (type size first, then line height, letter spacing, photo shape, and spacing), and fits the lead headline to its column in at most 4 lines. A picture that fails, loads blank, or takes over 8 seconds is removed, and the layout is refitted. It does nothing under 600px wide, where the page is one natural column.

Each item has a `video` field. It holds the 11-character YouTube id when the link is a YouTube watch link, otherwise `null`. When a section's feature story has a video id, the page plays it muted and looping in the photo frame, using youtube-nocookie. With reduced motion on, the thumbnail is shown instead.

## Images

Feeds, ESPN, and GitHub give most pictures. For chosen stories that still have none, the script reads the `og:image` of the article page. Claude never sees pictures. It only learns whether one exists. Photos are shown in sepia with a slow zoom. The feature photos of Tech & AI and Architecture also get a halftone dot screen. A picture that fails to load is removed, and the story shows as text.

If a run fails, the old page stays live.

## Set up

1. Add a repo secret named `ANTHROPIC_API_KEY`.
2. In Settings > Pages, set the source to GitHub Actions.
3. Run the "Daily brief" workflow once by hand.

## Edit sources

Open `sources.yaml`. Add, remove, or disable sources. Each source has a `name`, a `type`, and a `url`. Types are `rss`, `html_anthropic`, `github_search`, and `espn_news`. Set `enabled: false` to switch one off. Set `no_image: true` to never show an image for a source. Set `title_suffix_strip` to cut trailing text such as `" - AP News"` from titles. `exclude_title_patterns` drops opinion pieces from every source. Change `reader_profile` to change what Claude cares about.

## Run locally

```
pip install -r requirements.txt
ANTHROPIC_API_KEY=... python brief.py
```

Try it without network or an API key:

```
python brief.py --dry-run
```

Fetch feeds only, and write `out/items.json`:

```
python brief.py --fetch-only
```

Run the tests:

```
python -m pytest -q
```

Then serve the page:

```
cd site && python -m http.server
```
