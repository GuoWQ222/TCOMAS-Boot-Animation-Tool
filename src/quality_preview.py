"""Serialize local preview conversions and discard superseded slider requests."""
import queue
import threading
from convert_media import cached_build_asset


class QualityPreview:
    def __init__(self, events):
        self.events = events
        self.requests = queue.Queue()
        self.thread = threading.Thread(target=self._work, daemon=True)
        self.thread.start()

    def request(self, generation, source, quality, fill_screen=False):
        self.requests.put((generation, str(source), int(quality), bool(fill_screen)))

    def _work(self):
        while True:
            request = self.requests.get()
            if request is None:
                return
            while True:
                try:
                    request = self.requests.get_nowait()
                    if request is None:
                        return
                except queue.Empty:
                    break
            generation, source, quality, fill_screen = request
            try:
                data = cached_build_asset(source, quality, allow_oversize=True, fill_screen=fill_screen)
                self.events.put(('quality_result', generation, source, quality, fill_screen, data, None))
            except Exception as error:
                self.events.put(('quality_result', generation, source, quality, fill_screen, None, str(error)))

    def close(self):
        self.requests.put(None)
