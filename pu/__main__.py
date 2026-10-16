"""PU Agent command-line entry points.

  pu                       chat in the terminal (default)
  pu dashboard             open the local browser cockpit
  pu connections           list configured integrations and health
  pu brief                 run the multi-agent research brief
  pu worker                keep automatic partner briefing running
  pu skill install <url>   install a community skill
  pu connect gmail         authorize Gmail
  pu connect drive         authorize Google Drive
"""

from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return
    if not args:
        from pu.gateway.cli import main as cli_main
        cli_main()
    elif args[0] == "dashboard":
        from pu.ops.dashboard import main as dash_main
        dash_main()
    elif args[0] == "connections":
        from pu.integrations import cli_main
        sys.exit(cli_main())
    elif args[0] == "brief":
        from pu.ops.brief import main as brief_main
        brief_main()
    elif args[0] == "worker":
        from pu.ops.briefing import main as worker_main
        worker_main()
    elif args[0] == "skill" and len(args) >= 3 and args[1] == "install":
        from pu.memory.procedural.installer import install
        install(args[2])
    elif args[0] == "connect" and len(args) >= 2 and args[1] == "gmail":
        from pu.config import load_settings
        from pu.tools.gmail import connect as gmail_connect
        print(gmail_connect(load_settings().ensure_home()))
    elif args[0] == "connect" and len(args) >= 2 and args[1] == "drive":
        from pu.config import load_settings
        from pu.tools.google_drive import connect as drive_connect
        print(drive_connect(load_settings().ensure_home()))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
