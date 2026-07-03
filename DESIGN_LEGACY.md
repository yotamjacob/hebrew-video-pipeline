# Legacy design — "Yogalina classic"

The original site design (before the generic/publishable redesign of 2026-07-03) is
preserved in full at:

- **Git tag:** `design-yogalina-classic`
- **Branch:** `design/yogalina-classic`

## To revert the whole design

```bash
git checkout design-yogalina-classic -- site/
npx vercel deploy --prod
```

## What the classic design was

- **Hero:** full-width photo (`site/img/cover.webp`, Yogalina — School of Quiet Yoga)
  with a purple gradient overlay, title "Hebrew Video Pipeline", sub "Upload · Edit · Download".
- **Footer (all views):** `יוגלינה · School of Quiet Yoga`.
- **Title tag:** `Hebrew Video Pipeline · Yogalina`.
- **Tabs:** 🎬 Hebrew Pipeline / 🗂️ History / 📊 Statistics / 🚪 Logout.
- **Statistics tab:** visible — Yogalina's Metricool snapshot (`site/stats.json`,
  refreshed via `generate_stats.py`). The feature was removed entirely on 2026-07-03
  (commit history has the code, `generate_stats.py`, and the last `stats.json`);
  the tag below still contains the fully working version.
- Same purple palette as the current design (the redesign kept it).

`site/img/cover.webp` is intentionally kept in the repo even though the generic
design no longer references it.
