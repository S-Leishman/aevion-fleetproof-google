"""Record the FleetProof demo video against the live Cloud Run service.

Drives the real deployed UI. Nothing is mocked or staged: every receipt,
verdict, and integrity result shown is produced by the running service.

Usage:
    python scripts/record_demo.py <service-url> [out-dir]

Produces a .webm in out-dir. Convert to mp4 separately with ffmpeg.
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

VIEWPORT = {"width": 1440, "height": 810}

CAPTION_CSS = """
#demo-caption {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 2147483647;
  background: linear-gradient(transparent, rgba(3,7,18,.94) 38%);
  color: #f8fafc; font: 500 25px/1.45 'Segoe UI', system-ui, sans-serif;
  padding: 60px 64px 40px; text-align: center; pointer-events: none;
  opacity: 0; transition: opacity .45s ease; text-wrap: balance;
}
#demo-caption.on { opacity: 1; }
#demo-caption b { color: #7dd3fc; font-weight: 650; }
#demo-caption i { color: #fca5a5; font-style: normal; font-weight: 650; }
"""

CAPTION_JS = """(text) => {
  let el = document.getElementById('demo-caption');
  if (!el) {
    el = document.createElement('div');
    el.id = 'demo-caption';
    document.body.appendChild(el);
  }
  if (text === null) { el.classList.remove('on'); return; }
  el.innerHTML = text;
  el.classList.add('on');
}"""


def caption(page: Page, text: str | None, hold_ms: int = 0) -> None:
    page.evaluate(CAPTION_JS, text)
    if hold_ms:
        page.wait_for_timeout(hold_ms)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: record_demo.py <service-url> [out-dir]")
        return 2
    url = sys.argv[1].rstrip("/")
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else ".aevion_demo")
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--force-color-profile=srgb"])
        context = browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(out_dir),
            record_video_size=VIEWPORT,
            device_scale_factor=1,
        )
        page = context.new_page()
        page.goto(url, wait_until="load", timeout=60000)
        page.add_style_tag(content=CAPTION_CSS)
        page.wait_for_timeout(1500)

        # --- Act 1: what this is -------------------------------------------
        caption(
            page,
            "<b>Aevion FleetProof</b> — a control tower where an AI model may "
            "propose work, but a deterministic policy gate decides what is "
            "allowed to actually happen.",
            5200,
        )
        caption(
            page,
            "Live on Cloud Run. Routing by <b>Gemini 3.5 Flash</b> via the "
            "Google GenAI SDK. Receipts persisted in <b>Firestore</b>.",
            4600,
        )

        # --- Act 2: launch a mission ---------------------------------------
        caption(page, "We give the fleet an objective.", 2200)
        page.fill("#objective", "")
        page.type(
            "#objective",
            "Inspect the south corridor and publish a public safety notice",
            delay=42,
        )
        page.wait_for_timeout(900)
        caption(page, None)
        page.click("#btn-create")
        page.wait_for_timeout(2600)

        caption(
            page,
            "The model plans the steps. It does <i>not</i> get to authorize "
            "them.",
            4200,
        )

        # --- Act 3: benign steps execute -----------------------------------
        caption(page, "First step: read telemetry. Low consequence.", 2600)
        page.click("#btn-advance")
        page.wait_for_timeout(3000)
        caption(page, "Executed, and receipted.", 2400)

        caption(page, "Second step: draft the notice. Still reversible.", 2800)
        page.click("#btn-advance")
        page.wait_for_timeout(3000)
        caption(page, "Executed.", 1800)

        # --- Act 4: the gate holds -----------------------------------------
        caption(
            page,
            "Third step is different. Publishing is <i>public and "
            "irreversible</i>.",
            4000,
        )
        page.click("#btn-advance")
        page.wait_for_timeout(3200)
        caption(page, None)

        page.evaluate(
            "() => document.getElementById('gate-panel')"
            "?.scrollIntoView({behavior:'smooth', block:'center'})"
        )
        page.wait_for_timeout(1600)
        caption(
            page,
            "<i>HOLD.</i> The gate refused it. The effect did not run — and no "
            "model output can talk it into running.",
            5400,
        )
        caption(
            page,
            "This is the part that matters: the decision is made by "
            "deterministic policy, not by the model that proposed the action.",
            5200,
        )

        # --- Act 5: human authority ----------------------------------------
        caption(
            page, "Only a human holds the authority to release it.", 3000
        )
        page.click("#btn-approve")
        page.wait_for_timeout(3200)
        caption(page, "Approved by a named human. Now it executes.", 3600)

        # --- Act 6: the evidence chain -------------------------------------
        page.evaluate(
            "() => document.getElementById('timeline')"
            "?.scrollIntoView({behavior:'smooth', block:'center'})"
        )
        page.wait_for_timeout(1800)
        caption(
            page,
            "Every step left a hash-chained receipt: what was proposed, what "
            "policy decided, who approved, what actually ran.",
            5400,
        )

        caption(page, "And the chain is checkable.", 2400)
        page.click("#btn-replay")
        page.wait_for_timeout(3400)
        page.evaluate(
            "() => document.getElementById('integrity')"
            "?.scrollIntoView({behavior:'smooth', block:'center'})"
        )
        page.wait_for_timeout(1400)
        caption(
            page,
            "<b>VERIFIED.</b> Replayed from Firestore. Tampering with any "
            "receipt breaks the chain and shows up here.",
            5200,
        )

        # --- Close ----------------------------------------------------------
        caption(
            page,
            "Models propose. Deterministic policy governs. Humans authorize. "
            "Evidence proves it.",
            5000,
        )
        caption(page, "<b>Aevion FleetProof</b>", 3200)
        caption(page, None, 900)

        context.close()
        browser.close()

    videos = sorted(out_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime)
    if not videos:
        print("ERROR: no video produced")
        return 1
    print(f"recorded: {videos[-1]}")
    print(f"bytes: {videos[-1].stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
