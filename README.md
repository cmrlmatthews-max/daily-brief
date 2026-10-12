# Daily brief

A personal "AI homepage". It is rebuilt once a day and served by GitHub Pages.

Live at https://cmrlmatthews-max.github.io/daily-brief/

## How it works

1. GitHub Actions runs `brief.py` every morning.
2. The script reads the feeds listed in `sources.yaml`.
3. It sends the items to Claude twice (a third call picks the YouTube videos). Call 1 writes the front page: the lead, a few front stories, and 2 to 3 items per topic. Call 2 (the "desk") picks the 6 to 10 best items per topic, with a short note each, for the topic tabs. If call 2 fails, the run still succeeds: each tab then shows the newest items of each topic (up to 8) with no notes, and the log says `desk: fallback`.
4. Claude only returns item ids. The script maps them back to the real titles and links. Unknown ids are dropped, so links are never invented.
5. Team scores (the last result, plus the last 3 results and next 3 games per team), FDA recalls, and the Daily Stoic quote come straight from ESPN, the FDA feed and Project Gutenberg. No AI is involved. The Stoic quote is one whole sentence taken from the downloaded book and picked by the date, so it never changes during the day and is never written by a model.
6. The script writes `site/brief.json` (with `lead`, `front`, `sections`, `tabs`, `videos`, `recalls`, `stoic`, `scores`). The page `site/index.html` shows it.

## The page

The page is a vintage newspaper called CMRLM, in one light theme: aged newsprint, near-black ink, and a muted red-brown accent. The masthead is the image `site/img/masthead.png`. Team scores sit in a box at its left and the edition in a box at its right. It has one top story, a few front-page stories, and sections for these topics in this order: Tech & AI, Politics, World, Sports, Atlanta, Architecture, Science & Nature. The `tabs:` list in `sources.yaml` groups the topics into the News, Tech & AI, Sports, Atlanta and Science tabs.

The top story has a giant headline and 2 to 3 short `decks` under it. Claude writes the decks from the item title and summary only. If the lead is swapped for a story with a picture, the decks are left empty and the page copes.

The lead note is 120 to 150 words (4 to 6 sentences), written by Claude only from the item's title and summary. Front and section notes stay one short sentence.

Nothing on the page is left empty. After the page is built, after fonts load, after each picture loads or fails, and on resize, a small fill script in `site/index.html` evens out the blocks. It only sets CSS variables: it scales the type to fill the ears, makes the top-story, section and World columns end level (type size first, then line height, letter spacing, photo shape, and spacing), and fits the lead headline to its column in at most 4 lines. A picture that fails, loads blank, or takes over 8 seconds is removed, and the layout is refitted. It does nothing under 600px wide, where the page is one natural column.

Each item has a `video` field. It holds the 11-character YouTube id when the link is a YouTube watch link, otherwise `null`. The page never embeds a player. A video shows as a still sepia frame with a ▶ mark, and a tap opens the video on YouTube (the YouTube app on iPhone), so YouTube Premium applies and no ads play. All video comes from YouTube only.

## YouTube tab

Videos do not go in the news topics. They have their own tab, YouTube, built from the `youtube:` block in `sources.yaml`: channels in groups (News & Politics, Sports, Architecture & Building, Tech & Science, Make & Cook). The script reads each channel feed, leaves out Shorts, keeps only videos from the past 2 days, and keeps the newest 3 per channel. The videos are never sent to calls 1 and 2. A third Claude call (Haiku, the writer) picks the best 8 to 12, best first in each group, with a one-sentence note each. It writes the note only from the title and description. If that call fails, the tab shows the newest 2 videos of each group with no notes, and the log says `videos: fallback`. A bad channel is skipped and shows up in `source_errors`; it never stops the run. `site/brief.json` gets a `videos` list: `[{"group": "Sports", "items": [...]}]`, in the order of the config. The key is left out when there are no videos. Each item has the 11-character id in `video`, and a tap opens YouTube. The log shows counts only.

## Tabs

The tab bar under the dateline has two rows. Row 1 is the paper: Front Page, News, Tech & AI, Sports, Atlanta, Science, YouTube. Row 2 is private: Today and Mail. `#news`, `#sports` and the other tab ids in the address open those tabs, and the page remembers the last tab on each device. The fit script only measures Front Page. The topic tabs (set by `tabs:` in `sources.yaml`) come from a second Haiku call that picks 6 to 10 items per topic; if that call fails, each topic shows its newest items without notes.

The weather ear in the header (right of the masthead) and the full weather block on the Atlanta tab are public. The browser asks Open-Meteo for Atlanta (city level, set by `WX_PLACE` in `site/index.html`). If that fails, both are left out. The Sports tab shows the last 3 results and next 3 games for each team. The Front Page also has a Daily Stoic line (from the public-domain George Long translation of Marcus Aurelius on Project Gutenberg) and an FDA Recalls box; each is hidden when its source fails.

Today (calendar) and Mail (Gmail) are private, but the site is public. So `brief.py` builds them on the server, seals them with AES-256-GCM (key from your passphrase, PBKDF2-SHA256, 600,000 rounds, a fixed app salt, and a fresh random IV each run; the file is format v2, and the page still opens v1 files), and writes only `site/private.enc`. That file is gitignored and is never committed. The page opens it in your browser with the passphrase, in memory. Calendar and mail text never appear in the repo, in `brief.json`, or in the Actions logs. The logs show counts only, and error type names, never messages.

Use a long passphrase. The sealed file is public, so its strength is the only lock. "Remember on this device" keeps one derived, non-extractable key in this browser's IndexedDB, never the passphrase. A script on another page of the same site can use that key while the page is open, but it cannot copy it out or learn the passphrase. The Lock button erases it. If you change the passphrase, each device asks for it again once.

Add these repo secrets (names only). All are optional:

- `BRIEF_PASSPHRASE`: turns the private tabs on. Without it nothing private is fetched or written.
- `GCAL_ICS_URL`: the calendar's secret iCal address. Without it Today says the calendar is not connected.
- `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`: a Google OAuth client and a refresh token with the read-only `gmail.readonly` scope. Without them Mail says Gmail is not connected.

Mail uses one more Claude call. It sees sender, subject, and snippet only (never full bodies), and it is told the text is untrusted. It returns ids, categories, and one-line summaries. The script builds every Gmail link itself from the thread id.

`python brief.py --dry-run` builds a demo `site/private.enc` from `fixtures/sample_private.json` (fake data, no network) with the passphrase `demo passphrase`, unless `BRIEF_PASSPHRASE` is set.

## Print a new edition

The foot of the page has a "Print a New Edition" button. It opens the Apple Shortcut named `Print Daily Brief` (set by `SHORTCUT_NAME` in `site/index.html`), which starts the GitHub run. The page then checks `brief.json` every 15 seconds. When a newer edition arrives, it reloads. After 12 minutes it gives up and links to the runs on GitHub. The Shortcut comes back to the page with `?printed=<time>`, and the page keeps waiting from there. The scheduled 2:17 AM run first runs `python brief.py --check-today`. If a paper was already printed that day (New York time), it skips the build and the deploy. Manual runs always build.

## Models

Call 1 (front page and spoken script) uses `front_model` (Sonnet). If it fails, the run tries once more with `model` (Haiku). Call 2 (topic tabs) and the private tabs use `model`. The Sunday week review uses `week_model` (Opus).

## Back issues, likes, and the Sunday edition

The `data` branch holds `archive/DATE.json` (one paper per day) and `likes.jsonl` (the public likes log). The build reads it from `./data`. The `save` job commits each new paper to `archive/`. The page shows back issues at `?date=YYYY-MM-DD` and lists them in the footer.

Tap ☆ on a story to star it. Stars stay on the phone until you tap "Send N likes". That button runs the Apple Shortcut `Send Daily Brief Likes`, which starts the "Save likes" workflow (`likes.yml`, `likes.py`). Each run gives both Claude calls up to 30 recent likes as a guide to taste.

On Sundays, Opus reads the week's papers and likes. It writes "The Week" tab: a summary, the top stories, your most-liked stories, and suggestions for sources and the reader profile. Nothing changes on its own. You approve each suggestion by hand.

The "Listen to Today's Paper" button runs the Apple Shortcut `Read Daily Brief`, which plays the audio of the paper (see Audio below). Setup: [SETUP-LIKES-AUDIO.md](SETUP-LIKES-AUDIO.md).

## Audio

Each morning the paper also gets a spoken version. The workflow has three jobs for this:

1. `build` makes the paper as before and uploads it as artifacts (`paper` for the back issue, `site` for Pages).
2. `audio` runs on its own runner with no secrets and read-only access. It reads the `listen` text of `brief.json` and speaks it with Kokoro (voice `am_michael`, an open voice model that runs on the CPU, no API key). It writes `listen.mp3` (mono, 24 kHz, 64k, written by `soundfile`, no ffmpeg needed) and `audio.json`, and uploads them as the artifact `audio`. The job has `continue-on-error`, a 12 minute limit, and the speech step has a 6 minute limit.
3. `assemble` runs only the standard library, no repo code. It checks the audio files (a real MP3 of 10 KB to 15 MB, a url like `listen.mp3?v=<time>`, 1 to 3600 seconds). If they pass, it copies `listen.mp3` into the site and adds `"audio": {"url": "listen.mp3?v=<time>", "seconds": 159.5, "voice": "Kokoro Michael"}` to `site/brief.json`. Then it uploads the site to Pages, and `deploy` publishes it.

If the audio job fails, times out or makes bad files, the paper is still published, with no `audio` key. The `listen` text on the page is never changed. The back issue is saved from the `paper` artifact, so it never has an `audio` key, and `listen.mp3` is not kept.

The settings are in the `audio:` block of `sources.yaml`. `pronounce:` lists words that the voice says wrongly, as `Written: Spoken` pairs (whole words, case-sensitive). They change only the text that goes to the voice. Built in: `Jr.`, `Sr.`, `U.S.` and `&`. Put quotes around `No` and `Yes`.

The two model files (about 205 MB) come from the `model-files-v1.0` release of `thewh1teagle/kokoro-onnx`. They are downloaded into a temp folder, checked against pinned sha256 values (also in `audio.py`), and only then moved to `.kokoro` and cached. A partial or bad download is never cached. Packages for this job are pinned in `requirements-audio.txt`; the main build does not need them.

The `Read Daily Brief` Shortcut plays the MP3 when `brief.json` has an `audio` key. Otherwise it speaks the `listen` text, as before.

Local run (needs `pip install -r requirements-audio.txt` and the two model files):

```
python brief.py --dry-run
KOKORO_DIR=/path/to/models python audio.py                               # writes site/listen.mp3 and the audio key
KOKORO_DIR=/path/to/models python audio.py --brief site/brief.json --out out   # the workflow way
```

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
