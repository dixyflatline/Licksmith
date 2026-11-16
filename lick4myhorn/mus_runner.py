"""Run the `muscriptor` command line, plus one "[progress] done/total" line on stderr each time a 5-second chunk is finished.

The stock command only prints timing lines before decoding starts and then nothing until the whole recording is done,
so the Transcribe page could not say where it was. The model does emit progress events (one per chunk); the command
just drops them. This wrapper lets them through to the log. If the hook cannot be installed (a different Muscriptor
version) the command still runs unchanged and the page falls back to a time estimate.

Usage: python -m lick4myhorn.mus_runner transcribe <audio> --output ... (same arguments as `muscriptor`)
"""
from __future__ import annotations

import sys


def _hook() -> bool:
    try:
        import muscriptor.transcription_model as tm
        from muscriptor.events import ProgressEvent

        orig = tm.decode_model_tokens

        def decode_with_progress(*a, **k):
            for e in orig(*a, **k):
                if isinstance(e, ProgressEvent):
                    print(f"[progress] {e.completed}/{e.total}", file=sys.stderr, flush=True)
                yield e

        tm.decode_model_tokens = decode_with_progress
        print("[progress] ready", file=sys.stderr, flush=True)  # the page knows chunk counts will follow
        return True
    except Exception as e:  # noqa: BLE001 - never stop the transcription over a progress display
        print(f"[progress] unavailable ({type(e).__name__})", file=sys.stderr, flush=True)
        return False


def run() -> int:
    _hook()
    from muscriptor.main import main

    sys.argv[0] = "muscriptor"
    return main() or 0


if __name__ == "__main__":
    sys.exit(run())
