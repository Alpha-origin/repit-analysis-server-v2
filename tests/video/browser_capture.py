"""Record synthetic WebM samples with a real browser MediaRecorder (canvas only, no camera, no people).

    uv run --with playwright python tests/video/browser_capture.py <output-dir> [--channel chrome]

Playwright is a QA-only tool and deliberately not a project dependency. The output directory is passed to
``tests/video/test_browser_media.py`` through VIDEO_BROWSER_FIXTURES_DIR.
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import threading
from pathlib import Path

PAGE = """<!doctype html><meta charset=utf-8><canvas id=c width=640 height=360></canvas><script>
async function record(mimeType, millis) {
  const canvas = document.getElementById('c');
  const context = canvas.getContext('2d');
  let frame = 0;
  const timer = setInterval(() => {
    frame += 1;
    context.fillStyle = `hsl(${frame * 7 % 360} 70% 50%)`;
    context.fillRect(0, 0, 640, 360);
    context.fillStyle = '#fff';
    context.font = '48px sans-serif';
    context.fillText('frame ' + frame, 40, 200);
  }, 1000 / 30);
  const stream = canvas.captureStream(30);
  const recorder = new MediaRecorder(stream, {mimeType});
  const chunks = [];
  recorder.ondataavailable = (event) => chunks.push(event.data);
  const stopped = new Promise((resolve) => recorder.onstop = resolve);
  recorder.start(250);
  await new Promise((resolve) => setTimeout(resolve, millis));
  recorder.stop();
  await stopped;
  clearInterval(timer);
  const buffer = await new Blob(chunks, {type: mimeType}).arrayBuffer();
  let binary = '';
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}
</script>"""

MIME_TYPES = {"browser-vp8.webm": "video/webm;codecs=vp8", "browser-vp9.webm": "video/webm;codecs=vp9"}


def serve() -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            payload = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    from playwright.sync_api import sync_playwright  # noqa: PLC0415 — QA-only optional tool

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--channel", default="chrome")
    parser.add_argument("--millis", type=int, default=2500)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    server = serve()
    manifest: dict[str, object] = {"samples": {}}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel=args.channel, headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            manifest["browser"] = f"{args.channel} {browser.version}"
            for name, mime in MIME_TYPES.items():
                supported = page.evaluate("(mime) => MediaRecorder.isTypeSupported(mime)", mime)
                entry: dict[str, object] = {"mimeType": mime, "supported": supported}
                if supported:
                    data = base64.b64decode(page.evaluate("([m, ms]) => record(m, ms)", [mime, args.millis]))
                    (args.output / name).write_bytes(data)
                    entry["bytes"] = len(data)
                manifest["samples"][name] = entry  # type: ignore[index]
            browser.close()
    finally:
        server.shutdown()
    (args.output / "capture-manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))  # noqa: T201


if __name__ == "__main__":
    main()
