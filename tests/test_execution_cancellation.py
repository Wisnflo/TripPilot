import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import trippilot.execution.supervisor as manager


class CancellationTest(unittest.TestCase):
    def test_batch_cancel_terminates_running_child(self):
        manager.reset_cancellation()
        result=[]
        with tempfile.TemporaryDirectory() as directory:
            worker=threading.Thread(target=lambda:result.append(manager.supervise(
                [sys.executable,'-c','import time; time.sleep(10)'],directory,10)))
            worker.start();limit=time.monotonic()+2
            while time.monotonic()<limit:
                with manager._LOCK:active=bool(manager._ACTIVE)
                if active:break
                time.sleep(0.01)
            manager.terminate_all();worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertTrue(result[0]['cancelled'])
            self.assertLess(result[0]['wall_seconds'],2)
        manager.reset_cancellation()


if __name__=='__main__':unittest.main()
