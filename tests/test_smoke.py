import os
import tempfile
import unittest

from forkline import Tracer, replay
from forkline.store import SQLiteStore


class SmokeTest(unittest.TestCase):
    def test_tracing_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "forkline.db")
            store = SQLiteStore(path=db_path)
            tracer = Tracer(store=store)

            with tracer:
                with tracer.step("plan"):
                    tracer.record_event("input", {"prompt": "hello"})
                with tracer.step("execute"):
                    tracer.record_event("output", {"result": "world"})

            loaded = store.load_run(tracer.run_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(2, len(loaded.steps))
            self.assertEqual(1, len(loaded.steps[0].events))
            self.assertEqual(1, len(loaded.steps[1].events))

            replayed = replay(tracer.run_id, store)
            self.assertEqual(loaded, replayed)

    def test_nested_steps_restore_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "forkline.db")
            store = SQLiteStore(path=db_path)
            tracer = Tracer(store=store)

            with tracer:
                with tracer.step("outer"):
                    tracer.record_event("outer-start", {"value": 1})
                    with tracer.step("inner"):
                        tracer.record_event("inner", {"value": 2})
                    tracer.record_event("outer-end", {"value": 3})

            loaded = store.load_run(tracer.run_id)
            self.assertIsNotNone(loaded)
            self.assertEqual(2, len(loaded.steps))
            self.assertEqual("outer", loaded.steps[0].name)
            self.assertEqual(2, len(loaded.steps[0].events))
            self.assertEqual("inner", loaded.steps[1].name)
            self.assertEqual(1, len(loaded.steps[1].events))


if __name__ == "__main__":
    unittest.main()
