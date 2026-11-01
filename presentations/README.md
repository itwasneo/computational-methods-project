# Project presentations

## Current progress — 10 slides

- [PDF deck](tsp_progress_presentation.pdf): ready for presentation or sharing.
- [HTML source](tsp_progress_presentation.html): open directly in a browser; works offline.

Use **← / →**, **Page Up / Page Down**, or **Space** to navigate; **Home / End** jump to the first/last slide. Press **F** for full screen. The on-screen controls offer the same actions and printing. To regenerate the PDF, print the HTML using its page size, no margins, and no browser headers/footers; each slide is one 16:9 page.

### Evidence snapshot

- Merged foundation: `master` at `f4a76eb` — shared contracts, algorithm specifications, Python scaffold and numerical smoke test.
- Unmerged container work: PR #53 at `682bcf8`, including `docs/containers.md`, the definition and verification program.
- Owner-supplied successful local image build: source `f6e1fcde83988e76292bdff83fc6f736a9c19ee0`; 10 packaged tests plus the separate container correctness verifier passed. This does not establish finished-SIF runtime mounts or Aion/Iris compatibility.
- The separate SA prototype in open PR #50 is acknowledged, not counted as an integrated or validated baseline.

The deck distinguishes implemented software, specified behavior and planned experiments. No comparative solver measurements or HPC speedups are claimed. The deployment diagram labels completed and pending stages explicitly.

## Original proposal

[Original team presentation](tsp_team_presentation.html) is preserved unchanged as a historical proposal, not a current implementation-status report.
