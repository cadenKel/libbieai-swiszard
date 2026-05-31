def run_mcp_server():
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here))
    from server import mcp
    mcp.run()
