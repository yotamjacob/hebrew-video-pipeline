# Hebrew Video Pipeline — Agent Guide

## Source of truth

Read `CLAUDE.md` before changing this project. It contains the authoritative
architecture map, implementation invariants, test commands, deployment rules,
and non-obvious production details. Keep it accurate when a change affects
those details.

Also consult:

- `README.md` for the product overview and standard commands.
- `TODO.md` for the active backlog.
- `PLAY_RELEASE.md` for Android release work.

## Default workflow

The priority is to diagnose and fix bugs quickly without weakening safety or
regression coverage.

1. Inspect the smallest relevant module and its existing tests.
2. Reproduce the bug or establish the failing behavior.
3. Implement the smallest complete fix.
4. Add or update a regression test when practical.
5. Run tests proportional to the changed surface.
6. Report what changed, what was verified, and whether deployment is still
   pending.

Preserve unrelated working-tree changes. The owner's standing preference is to
commit each completed, tested change and push it directly to `main` so GitHub
stays in sync throughout development. Do not open pull requests or feature
branches unless requested. Vercel and Modal deployments still require an
explicit request.

End every change handoff with the exact frontend `APP_VERSION` and its state,
for example: `Frontend version: v1.10.14 (local only; production unchanged)`.
This lets the user match the reported work to the version shown in the app
footer.

## Test policy

Use the commands documented in `CLAUDE.md`:

- Backend changes: run
  `source .venv/bin/activate && python -m pytest test_stock_helpers.py tests/backend/`
- Frontend changes: run `npx playwright test`
- Cross-cutting changes: run both suites.
- `python test_api.py` uses the live Modal backend and consumes GPU resources.
  Ask the user immediately before running it or performing any other
  GPU-consuming operation. The user has authorized GPU spending after that
  confirmation.

Prefer focused tests during iteration, then run the appropriate full suite
before deployment.

## GitHub and Vercel

When the user asks to publish a completed fix:

1. Confirm the intended diff and ensure relevant tests pass.
2. Commit only the intended files with a descriptive message.
3. Push directly to `main` by default. Use a feature branch or pull request
   only when the user explicitly requests one.
4. For every frontend deployment, bump `APP_VERSION` in `site/app.js`, run the
   frontend tests, and deploy from the repository root with
   `npx vercel deploy --prod`.
5. Report the commit/PR, deployed frontend version, and production URL.

Backend-only changes do not require an `APP_VERSION` bump. Deploy them with
`modal deploy app_modal.py` only when requested.

Never expose or commit local credentials, `.env`, Modal/Vercel tokens, Firebase
configuration, or Android signing material.
