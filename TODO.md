# Hebrew Video Pipeline — TODO

## Caption quality
- [ ] Fine-tune `--margin-v` per video (default = height÷4; use `--margin-v <px>` to override)
- [ ] Fine-tune `--font-size` per video (default = 56)
- [ ] Validate RTL rendering on videos that mix Hebrew + numbers or Latin
- [ ] Consider bold vs. regular weight (currently bold; `Bold=-1` in ASS style)

## Audio enhancement
- [ ] Set up ElevenLabs API key (`export ELEVENLABS_API_KEY=sk_...`) and test `--enhance elevenlabs`
- [ ] Evaluate Adobe Podcast manual flow vs. ElevenLabs for quality comparison
- [ ] Explore Speechmatics or other backends if needed (slot in behind `transcribe()` → `List[Word]`)

## Silence cutting
- [ ] Test `--min-silence` values (current default 0.5s) on longer videos
- [ ] Test `--padding` values (current default 0.2s) — increase if words feel clipped

## Pipeline robustness
- [ ] Handle portrait vs. landscape automatically (margins/font-size already auto-scale)
- [ ] Add a `--dry-run` flag that prints cuts + cues without encoding
- [ ] Test on longer videos (5–15 min) for memory and performance

## Output
- [ ] Add output folder option (currently outputs next to input file)
- [ ] Batch mode: process all videos in `input_vids/` in one command

## Infrastructure
- [ ] Pin Python version (currently 3.9.6 system Python — consider pyenv)
- [ ] Consider GPU instance (e.g., RunPod) for faster transcription on long videos

## Stock B-roll
- [x] Pexels + Pixabay search with portrait filter
- [x] Two-prompt architecture (broad retrieval + strict scoring)
- [x] Haiku vision scoring of thumbnails
- [x] B-roll duration aligned to speech timestamps, scaled by subject complexity
- [x] Middle-window clip selection (avoids static first/last frames)
- [x] Toggle-able clip selection in UI (click again to deselect)
- [x] `weak_match` fallback when no portrait clips found
- [ ] Add Storyblocks or Artgrid as a third stock source (see README for how-to)
- [ ] Improve fallback: when 0 candidates, try both shorter query AND alternative keywords from `visual_anchor`
- [ ] Cache recent stock search results in-process (same query within one session)

## New features
- [ ] Hook card — AI-generated visual hook for the first 1–3 seconds, manually editable
- [ ] Auto-description — Claude reads captions and generates a social caption (configurable via `context.md`)
- [ ] Share to socials / export as Reel

## Security / API
- [ ] Add authentication layer to Modal API (currently security-through-obscurity on UUID keys)
- [ ] Server-side upload size cap per chunk and total (currently JS-only)
- [ ] Test coverage: stock B-roll flow, `/upload_chunk`, `/download/{key}`, error cases



add forbiden words filter to avoid instagram shadow banning the video
add capability to add cupations and split existing caption into separate lines in the caption editor
