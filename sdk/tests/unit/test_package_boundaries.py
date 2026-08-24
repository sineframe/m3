def test_canonical_package_boundaries_import():
    from mcp_pal.api import create_app
    from mcp_pal.harness.base import HarnessResult, HarnessRunner, RunSpec
    from mcp_pal.persistence.database import Base, make_engine
    from mcp_pal.persistence.models import Run
    from mcp_pal.services.run_manager import RunManager
    assert create_app and HarnessResult and HarnessRunner and RunSpec and Base and make_engine and Run and RunManager
