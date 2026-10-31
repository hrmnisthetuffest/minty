# The Second Brain

Everything Minty is told, reads or works out can be kept as **knowledge**: sourced, connected, checked or not, and shown as one interactive map in the control panel
(**Memory → Second Brain**). Minty uses the same store: it captures, recalls, links, edits and checks it through five tools, a hint in front of every turn that names something
it knows, and a proposal to the inbox whenever a mission finishes. The map is not a picture over a notes database; it is built, at the moment you ask, from the records Minty
itself reads.

## What already existed, and what this does with it

| Existing | What it is | What the Second Brain does with it |
|---|---|---|
| The notes folder (`vault/`) | Markdown files, git history, FTS, an inbox with proposals. **Off by default** (`vault.enabled`) and off for the author's daily copy | Not replaced. Its notes and their links appear in the map when it is on (read-only, by reference). Its n-gram similarity (`vault.gate.HashEmbedder`) and rank fusion are reused for search |
| The world model (`mind/world.py`) | People, organisations, projects, decisions, with the provenance of each fact | The home of those things. A person, organisation or project is **never copied** into the Second Brain; knowledge links to `world:NAME`, and `know_capture(kind="person")` is refused with a pointer to `world_note`. Decisions stay in `world_decide` |
| Research notebooks, memory gists | Findings by topic; one-line remembered facts | Left alone. Not yet bridged (see the end) |

### Choices that were challenged

* **A standalone store, not the notes folder.** The notes folder is the natural home for long-form writing, but it is gated on a setting and holds Markdown files with a fixed
  header; typed relationships, immutable originals, an inbox with states and per-item provenance do not fit a front-matter file. A second store is only justified because the
  first is optional and shaped differently, so the map reads both.
* **No graph database, no vector database, no new dependency.** The needs are a few thousand records, typed links, full-text search and "things that look alike". SQLite
  (already the base of every store here) does all of it: FTS5 for words, a character n-gram hash for spelling and word forms, a link table for edges. Both were measured rather than
  assumed (the map at about 840 nodes and 2 100 links is built in 0.16 s). If a local embedding model is ever installed it plugs into the same ranking.
* **One edge layer across stores, by reference.** A link is `item:ID`, `source:ID`, `world:NAME`, `vault:ID`, `decision:ID`, `mission:ID`, `flow:ID` or `playbook:NAME`. Nothing
  is copied, so nothing can drift apart or show up twice.
* **Deterministic capture, with the model where it is already in the loop.** Splitting a brain dump, guessing a kind, finding known names and proposing links are rules, so they
  work with no model, cost nothing and cannot invent text. When Minty itself is in the conversation it does the thinking and files the result with `know_capture(title, kind, ...)`.

## The data model (`knowledge.sqlite`, next to `world.sqlite`)

* **sources**: the originals. A typed thought, a page's text, a PDF, a transcript. Written once; the same bytes twice are one source. A file original is kept byte for byte,
  content-addressed, mode 0600, in `knowledge-originals/`. The only later change is filling in the text read out of a file whose first read failed.
* **items**: the knowledge. `kind` (note, idea, concept, finding, observation, lesson, question, hypothesis, opportunity, reference, insight, article, document, dump), `title`,
  `body` (the words as written), `summary` (generated, marked, never a replacement), `authored` (**you** / **minty** / **outside**), `status` (unverified / verified / disputed /
  outdated), created / updated / last reviewed, project, tags, `private`, archived. Which sources an item rests on is its **evidence**, with the quoted passage.
* **links**: `src`, `rel`, `dst`, why, `status` (**confirmed** / **suggested** / rejected), who made it (you / minty / auto) and a score for suggestions. Relations include supports,
  contradicts, answers, part of, derived from, about, uses, led to, inspired by; others are accepted.
* **captures**: the inbox: the raw input, channel, origin, status (pending / processing / review / filed / failed / archived), what processing found, errors and attempts.
* **history**: every change to anything, with the actor and what it was (an edited capture keeps its old text).

## Rules that are enforced in code (and tested, with the guard removed to prove the test fails)

1. A turn that read outside content (mail, a page, a file) files only to the **inbox**; what it files is `outside` and `unverified`, its links are only **suggestions**, and it
   cannot verify anything or confirm a link.
2. Something is "verified" only by you, or by Minty when a source is attached and the turn read nothing from outside.
3. Changing the words of a verified item makes it unchecked again; the old words stay in the history. An item is never silently re-labelled as yours.
4. A summary sits beside the text. The original is one click away in the inspector.
5. A link you rejected does not come back by itself (an automatic suggestion or Minty's own link is refused; only you can make it again).
6. Page or file text that reads like orders to a model is held for your approval and flagged; it still keeps its original so you can look.
7. A private turn files private; private items, originals, links and inbox entries are absent from the map, search, hints, tools and the attention list unless you ask in the panel.
8. No tool deletes anything. Archiving hides and keeps. Approving an inbox entry and removing one for good are the owner's, in the panel.
9. Pages are fetched only through the safe page reader (address judged first, plain text, no cookies or scripts); there is no other way in for the network or a file path.

## Capture

Drop text, a link or a file in the panel's **Inbox** (or say it to Minty). Processing keeps the original, splits a brain dump into single thoughts, guesses each one's kind
("Idea:", "Lesson learned:", a question mark, "I think ..."), links the people and organisations the world model knows (an unknown capitalised name is only *proposed*), turns a repeat
into another piece of evidence for the first instead of a copy, proposes similar knowledge as suggested links with the words they share, writes an extractive summary beside long text,
and files it. Things you put in are filed at once; anything from outside, anything that looks like instructions, and anything Minty wrote on its own wait for your approval. A failure
keeps the input, says why, and can be retried. A **finished mission** is proposed to the inbox with its goal, outcome and which criteria were verified, linked back to the mission.

Supported inputs: typed text and brain dumps; a voice transcript (channel `voice`); a link; a PDF; a text file. Images are kept as originals; reading the text in them needs OCR,
which is not connected here, so they fail loudly rather than being filed empty.

## It fills itself

You do not have to say "add this to my second brain".

* **What you say.** After every answer, what *you* said is looked at (never Minty's reply). It is filed when it looks like something worth keeping: a labelled line ("Idea:", "Lesson:", "Question:"),
  a lesson, a guess or an idea in your own phrasing ("I think ...", "what if we ...", "next time ..."), a decision ("we decided ..."), a plan or fact about the work that mentions what a project is made of
  (suppliers, prices, customers, a launch, margins), a "note that ..." line, or a long message that is plainly thinking aloud. It goes through the ordinary capture path (split into thoughts, linked,
  de-duplicated) and is marked "filed automatically" in the inspector, so you can archive what was not worth keeping.
* **What it never files:** Minty's own answers; commands and questions to Minty ("play ...", "what's the weather"); chatter ("ok", "thanks"); facts about you (your birthday, your family: the memory
  tools keep those); anything that looks like a secret (a password, a key, a token, a password inside a URL, anything the secret scanner would hide); anything that reads like orders to a model; forwarded
  messages (someone else's words); anything said in a private conversation; and a turn that already filed or remembered it by calling a tool itself. At most 12 an hour (`knowledge.auto_per_hour`);
  `knowledge.auto_chat = false` switches it off.
* **It is rules, not a model, on purpose:** it costs nothing, adds no delay, and every rule is a test. The price is recall: a thought that uses none of the cues is missed (the owner can still say it to
  Minty, which files it with `know_capture`, or drop it in the Inbox). A model could judge better; it would cost a model call per message, and the free tiers this runs on are rate limited.
* **Research.** When a research finishes, it becomes points: one hub ("Research: topic"), one point per claim the note itself used (8, 16 or 30 by depth), each resting on the stored text of the page it came
  from and the exact quote, and the other sources that agree. The claims are the research's own (every quote is checked to be word for word in the page). Points are outside and unverified however many
  sources agree; two points whose numbers disagree are marked disputed and suggested as a conflict. A claim that reads like orders to a model is left out. Switch off with `knowledge.auto_research = false`.
  Research that finished before this existed is not brought in.

## How connections are decided

There is no meaning model: nothing here knows that "espresso" and "coffee" are related. A connection exists for one of these reasons, and each says which in the inspector (who made it, and why).

| Kind | Made when | Status |
|---|---|---|
| **Provenance** (rests on a source; part of its research hub; derived from a mission) | The thing was filed from that source, research or mission: a fact about how it got here | confirmed |
| **You** (the panel's connect box, confirm and dismiss) | You said so | confirmed |
| **Minty** (`know_link`, or links given to `know_capture`) | Minty said so in a turn that read nothing from outside | confirmed |
| **Names** ("about Acme") | A person, organisation or project the world model knows is named in the text, as whole words. From your own words: confirmed. From outside text (a page, a research): only suggested | confirmed or suggested |
| **Similar** ("relates to") | Two things share at least 2 subject words (3 unless their spelling is also alike) after plurals, tenses and a silent e are folded, filler words ("every", "always", "thing" ...) are ignored and accents are dropped. Score = half the character n-gram similarity plus half the shared words (out of 5); it must reach 0.25. The three best are proposed | suggested only |
| **Contradiction** | Confirmed only if you or Minty linked it, or the world model holds two values for one fact. Otherwise a *guess*: same subject (3 shared words) but a number or a negation differs; or a research's cross-check found different numbers | suggested, shown as "a guess" |

Suggestions are drawn dotted, never count towards a dot's size, and are listed apart (marked suggested) rather than used to answer a question. A suggestion you dismiss does not come back by itself. A duplicate is only called a duplicate when
the words are the same once case and punctuation are ignored, or the spelling is almost identical (cosine 0.9) *and* the numbers agree and both or neither is negated ("rent is 900" and "rent is 950" are
two claims). Clusters on the map come from the links (each dot takes the label most of its neighbours have, projects seed the labels); they are for looking, not a fact.

## Using it

* Tools (group `know`): `know_capture`, `know_recall`, `know_link`, `know_edit`, `know_check`.
* The turn hint: when a request names something the Second Brain holds (two shared subject words), a short line with the title, who wrote it and whether it is checked goes in front
  of the model, sharing the world model's 700 characters. Missions get it too.
* The older memory tools (`memory_audit`, `memory_recall`) no longer stop at "Nothing in memory matches that" when the second brain holds something about the same words (one shared word is enough when the owner
  asks directly; private items only in a private turn). A model asked "what do I know about ..." goes to those first.
* `know_check` lists the **gaps** (an open question nothing answers, a hypothesis with no evidence, a claim with no source, something built on that came only from outside and is
  unchecked, an isolated item, a verified fact not reviewed for months) and the **conflicts**: verified when linked as contradicting or when the world model holds two values, otherwise
  *a guess from the wording* (a number or a negation differs), which you can confirm or dismiss.

## The map (Second Brain page)

Size is how many confirmed connections a thing has; glow is how recently it changed; the ring says who wrote it (solid you, dashed Minty, dotted from outside); a green dot is checked,
red is disputed; dotted lines are suggestions nobody confirmed; red flowing lines are contradictions. Clusters are found from the links and from projects and labelled. Scroll or pinch to
zoom (labels appear as you go in), drag to pan or to move a dot, click to open it (everything else dims, its neighbours light up), double-click to zoom into its neighbourhood, `/` to
search (matches light up on the map), `F` to fit. Legend chips hide kinds; the slider replays the map as it was and the play button watches it grow. The layout is remembered per
browser so the map is where you left it. The inspector shows the original apart from the summary, the evidence, every connection (confirm or dismiss suggestions, connect to anything
by searching for it), the history, and what might be related. The **Needs attention** tab holds the health numbers, conflicts, gaps and the inbox.

Routes (`/api/knowledge/...`): `graph`, `node`, `search`, `attention`, `captures` (add, upload, edit, process, approve, archive, remove), `items` (add, edit, verify, archive), `links`
(make, confirm, reject). There is no route that deletes an item.

## What was checked, and how

* 80 tests for the store, capture, reasoning, tools, the map and the routes. For the store, capture, tools, map, routes and the later fixes each safety rule was switched off in turn and a test
  failed every time (mutation check); six gaps this showed in the tests were closed. The full suite (about 6 270 tests) passes.
* In a real browser, against a scratch brain of about 200 and about 840 items (created for the check and deleted): the map, selecting, search, filters, the inbox (a real brain dump became
  an idea, a question and a lesson, linked to Acme and Jane, and appeared on the map), the attention tab, the private toggle (a private item appears only when asked), the time slider,
  the empty state and a phone-width layout. Two real problems were found and fixed this way (a collapsed layout, a legend covering the map on a phone).
* With a real model, on a scratch copy of the daily setup (deleted afterwards): the model filed a brain dump through `know_capture`, and a question asked with the conversation history cleared
  ("what do I already know about charging cafes, and about getting better prices from suppliers?") was answered with all three captured thoughts. Getting there found five real faults that
  no synthetic test had: a retried turn made nine copies of one idea (the title form had no duplicate check); a whole three-thought dump passed as one titled item was filed as one idea;
  "prices" did not match "price"; the hint sat glued to the end of the previous prompt section and was too polite; and the old memory tools told the model there was nothing while the second
  brain held the answer. All five are fixed and tested. The answer ultimately arrived through the memory tools' new pointer to the second brain, not through the turn hint alone: this model
  reached for the memory tools first.
* The backup now includes the Second Brain, its originals, the world model and the workflows (the last two were missing before).

## Not done, or not verified

* The second half of the brief (from section 2.2, "Make capture intelligent") was cut off when it was sent; this covers what came before it.
* Capture by a model in the background (splitting and linking with judgment, not rules) is not wired; rules only, unless Minty is in the conversation. Automatic filing from conversation is rule based and will miss thoughts that use none of its cues; it files what you said, never what Minty answered or found (Minty's research findings come through the research harvest).
* Research that finished before this existed is not brought in. Telegram documents, photos and voice notes still take their old route (the notes folder when it is on); they are not sent to the Second Brain yet. Images need OCR here.
* Research notebooks and remembered facts are not bridged into the map. Importing the notes folder as items, merging two duplicates from the panel, and per-item sharing levels beyond
  "private" are not built.
* Frame rate on a very large map was only measured in a software-rendered pane (about 12 to 19 frames a second at about 840 nodes); halos and animation are switched off while a big map
  is moved. Nothing was measured on a real GPU or on the owner's real knowledge over months.
* The live capture turn never completed its closing answer in the scratch runs (the free model tier's 8 000-tokens-a-minute limit and slow fallbacks); the filing itself was read back from the
  store. One model on one question is not a guarantee: another model may use the tools differently. The quality of rule-based kind guessing on the owner's real writing is untested.
