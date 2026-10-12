"""Exercise real native custody without WNOWAIT, as on Darwin Python 3.10."""

import os
import runpy
import sys

helper = sys.argv.pop(1)
if hasattr(os, "waitid"):
    del os.waitid
runpy.run_path(helper, run_name="__main__")
