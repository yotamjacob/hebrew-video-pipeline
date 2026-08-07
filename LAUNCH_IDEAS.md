# Launch Improvement Report - פייפליין
*Market research + feature recommendations, 2026-08-07. No code was changed; this is a menu, not a plan of record.*

---

## TL;DR - my top 5 picks

| # | Feature | Effort | Why it wins |
|---|---------|--------|-------------|
| 1 | **Caption style presets** (one-tap named looks) | Light | Biggest perceived-value-per-line-of-code in the market; every competitor sells "viral templates" |
| 2 | **Auto keyword highlight** (karaoke highlights only the *money words*) | Light-medium | Submagic's most-loved retention feature; we already have 90% of the plumbing |
| 3 | **Auto-zoom punch-ins at emphasis moments** | Medium | The single "wow" feature reviewers name most; our B-roll AI already finds the exact moments |
| 4 | **English translated captions** (burned or SRT) | Medium | Turns every Hebrew creator into a bilingual channel; nobody serves this niche well |
| 5 | **Burned-in progress bar** | Light | Trendy retention gimmick, almost free with our ASS pipeline |

Plus one marketing insight that costs zero code: **our pricing/retention model is already the answer to the market's loudest complaints** - say it out loud (see "Positioning").

---

## 1. Market snapshot

### The competition and their open wounds
The 2026 short-form tool market (Opus Clip, Submagic, CapCut, Captions.ai, Choppity, Kapwing) is converging on the same feature set: AI captions with word-level animation, auto-zooms, emoji, templates, auto-clipping. The loudest user complaints, per 2026 reviews and comparison posts:

- **Pricing resentment**: per-processing-minute credits that "climb with volume", confusing credit/refund mechanics, hidden cancellation flows, and **projects deleted when a subscription lapses** (Opus Clip deletes free-tier files after 3 days). Solo creators repeatedly say the $9-24/mo tools plus scheduling plus analytics stack up.
- **Watermarks** on free tiers everywhere; Submagic killed its free tier entirely in May 2026.
- **Processing failures and slowdowns** at peak times.

### The Hebrew gap (our moat - it's real)
- **CapCut has no Hebrew auto-captions** - Israeli creator groups actively complain about it.
- **Opus Clip has an open, unanswered feature request** for Hebrew/RTL.
- Submagic/VEED claim Hebrew among "50-123 languages", but that's generic multilingual Whisper - not a Hebrew-fine-tuned model (ivrit-ai), no RTL punctuation repair, no bidi-correct burning. Reviewers explicitly warn Hebrew users to "review right-to-left punctuation and alignment before export."

Nobody in the market has our stack: Hebrew-tuned ASR + LLM proofread with phonetic guard + pixel-verified RTL rendering + Hebrew hook generation. **The moat isn't "we support Hebrew", it's "we're the only tool where Hebrew isn't an afterthought."**

### What makes tools "hot" right now
Per 2026 trend roundups: retention-first editing; word-level caption animation (we now have both modes); **keyword highlighting, auto-zoom punch-ins, and emoji at emotional moments** are the three named retention features "creators love"; platform-native formats; "record → tool does the rest" workflows (we just shipped record-in-app).

---

## 2. Feature recommendations

### Quick wins (each roughly a session, mostly frontend)

**A. Caption style presets - one-tap looks.**
Bundle font + size + colors + outline + background + mode (classic/word/karaoke) + highlight color into ~6 named presets with visual swatches: e.g. "נקי" (clean white), "קריוקי צהוב" (yellow karaoke), "מילה-מילה" (big word-by-word), "כתבה" (news box style), etc. All the state already flows through one `caption_style` payload that persists, restores, and burns - a preset is just a stored payload + a picker row. *This is what competitors screenshot in every ad.* Also store "my last style" as an implicit preset (already true) and let users save one custom preset.

**B. Auto keyword highlight (karaoke v2).**
Current karaoke highlights every word as spoken. Submagic's beloved variant highlights only *keywords* (numbers, names, emotional words) in a distinct color. We literally already extract `intensity_markers` (specific Hebrew words that signal intensity) in the B-roll moment analysis - or a cheap standalone Sonnet/Haiku call on the transcript returns keyword indices. Render = same inline `\c` tags, just applied to keyword tokens in classic mode (line shows, keywords colored) or layered on karaoke. One new toggle.

**C. Burned-in progress bar.**
A thin animated bar across the top of the video ("fake progress" retention trick, ubiquitous in reels). In ASS this is one vector rectangle with a time-based width - a handful of events, no ffmpeg changes, preview parity via the shared builder. Toggle + color in Options or caption style panel.

**D. Post text generator on the export screen.**
We already generate post captions inside the Metricool scheduling modal (fresh videos only). Surface the same capability as "Copy post text" (title + caption + hashtags, Hebrew) right on the burn-success banner - most users don't schedule via Metricool, they paste into Instagram manually. One Sonnet call on the transcript we already hold; reuses the existing endpoint.

**E. Hook template gallery.**
Users can save a hook style today, but there's no starting inventory. Ship 5-8 curated hook style presets (colors/positions/sizes tuned to look like known viral formats). Pure frontend data.

### Medium features (1-3 sessions, backend + frontend)

**F. Auto-zoom punch-ins.** *The headline feature if you want one for launch marketing.*
Subtle zoom-ins (5-10%) at emphasis moments, cut back at the next sentence. The moment-detection is **already built** - the B-roll analyzer scores `intensity_score` per moment; high-intensity moments without a B-roll pick are exactly where a punch-in belongs. Render: ffmpeg `zoompan`/crop-scale segments in `burn_captions_fn` (same pass as captions), preview as a CSS transform on the player during those windows. Offer styles: smooth / snap. Keep it conservative by default (2-4 zooms/minute max). This is the feature reviewers name first when explaining why Submagic "feels alive."

**G. English translated captions (reach multiplier).**
One Sonnet call translates the caption lines; then either (a) SRT download in English - nearly free, or (b) *burned* English captions under the Hebrew audio - same caption pipeline, LTR lines (the bidi machinery already handles mixed text; English-only lines are the easy case). Hebrew spoken + English captions is a proven format for Israeli creators chasing international reach, and no competitor does the Hebrew→English pair with decent Hebrew ASR. Could be a paid-tier differentiator later.

**H. Referral credits.**
"Give 2, get 2": a referral code in the share/settings area; the backend grants credits on referred signup (the durable-grant pattern from Play billing applies - unique keys in `hebpipe-purchases`, summed not incremented). Growth loop at launch time, cheap to build, and credits are our native currency.

**I. B-roll from the user's own gallery.**
Alongside stock results, "upload your own clip" per moment (we already accept a `video_key` for B-roll and pin sources for re-edits). Creators consistently say stock feels generic; personal B-roll is what they actually want. Mostly frontend + a small upload path reuse.

### Flag for caution (looks light, isn't)

- **Emoji captions**: huge in the market, but libass color-emoji rendering is genuinely painful (bitmap font support varies; parity with the browser preview would break our WYSIWYG guarantee), and the app has a deliberate no-emoji design language. If ever: render emojis as PNG overlays, not text. Don't do this pre-launch.
- **Auto-clipping long videos into shorts** (Opus's core): real value but heavy (scene understanding, reframing, multi-output UX). Post-launch, phase-worthy.
- **Auto-reframe 16:9→9:16 with face tracking**: heavy CV pipeline; skip for now.
- **Sound effects at zooms**: cheap-ish (ffmpeg audio mix + small CC0 pack) but easy to make cringe; only worth doing *with* auto-zoom (F), defaulted off.

---

## 3. Positioning - free "hot topic" material (zero code)

The market's complaints are our press release:

1. **"Your videos aren't hostage."** Opus deletes free files in 3 days and locks projects behind lapsed subscriptions. We keep every export 30 days, no subscription required, re-editable from History. Say this exact contrast.
2. **"No minutes math."** Credit-per-video (long videos = 2 credits, that's it) vs per-processing-minute meters people can't predict. Our pricing page should show the competitor mental math falling apart.
3. **"No watermark. Ever. Including free."** Free tiers with watermarks are the #1 stated reason creators churn tools. Ours has none - currently unstated anywhere.
4. **"עברית זה לא 'עוד שפה'"** - the Hebrew-first story: fine-tuned Hebrew ASR, AI proofread that respects what you actually said (the phonetic guard is a great technical story), RTL that renders correctly down to the comma, Hebrew hooks. CapCut's missing Hebrew captions is an active complaint in Israeli creator groups - that's the exact audience and the exact moment.
5. **Launch content idea**: a side-by-side reel - the same Hebrew clip captioned in CapCut/Submagic vs פייפליין (their broken punctuation/order vs ours) - is cheap to make and speaks to the pain directly.

---

## 4. Small pre-launch polish (noticed while working, all light)

- The upload hint still says "max 500 MB" in the UI copy (`upload.hint`) - the cap is 1 GB now.
- The guide's upload section also still says 500MB (guide.upload.body, both languages).
- Consider showing the *style mode* (word/karaoke) in the confirm-burn modal summary so users notice the new feature exists.
- A tiny "NEW" badge on the caption-style select for a few weeks would advertise the two new modes to existing users.
- App Store/Play listing screenshots don't yet show word-by-word/karaoke modes (assuming current listing assets) - those two are the most screenshot-friendly features you have.

---

## 5. Suggested order of attack

1. **A (presets) + polish items** - one session, instantly demo-able, feeds listing screenshots.
2. **B (keyword highlight) + C (progress bar)** - one session, completes the "viral captions" story.
3. **F (auto-zoom)** - the marquee launch feature; budget a full session including parity testing.
4. **D (post text) + E (hook gallery)** - filler-sized wins between bigger items.
5. **G (translated captions)** - when you want the reach/expansion story; H (referrals) at launch day.

## Sources
- [Choppity - Best Opus Clip Alternatives 2026](https://www.choppity.com/blog/best-opus-clip-alternatives/)
- [Ssemble - Opus Clip Review 2026](https://www.ssemble.com/blog/opus-clip-review-2026)
- [Vugola - Submagic Alternatives 2026](https://www.vugolaai.com/blog/submagic-alternative)
- [ngram - Opus Clip vs Submagic](https://www.ngram.com/blog/opus-clip-vs-submagic)
- [OpusClip feature request - Hebrew/RTL](https://opusclip.canny.io/feature-requests/p/hebrew-and-rtl-usage)
- [Israeli Premiere group - CapCut Hebrew captions unavailable](https://www.facebook.com/groups/httpsmaosim1991.wixsite.compremierisrael/posts/1606192933316118/)
- [Submagic - Hebrew subtitles page](https://www.submagic.co/auto-subtitle-generator/hebrew-subtitles)
- [OpusClip - Short-form trends 2026](https://www.opus.pro/blog/short-form-video-trends-reshaping-creator-marketing-2026)
- [Rare Connections - Submagic review](https://www.rareconnections.io/submagic-review)
- [Max Productive - Submagic Review 2026](https://max-productive.ai/ai-tools/submagic/)
