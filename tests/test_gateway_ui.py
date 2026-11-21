import pathlib
import shutil
import subprocess
import unittest


class DashboardIsolationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"),"Node is required for dashboard isolation regressions")
    def test_switching_devices_cannot_reuse_previous_frame(self):
        result=subprocess.run([shutil.which("node"),str(pathlib.Path(__file__).with_name("gateway_ui_race.cjs"))],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("PASS",result.stdout)


if __name__=="__main__": unittest.main()
