"""Operational commands: `python -m app.cli seed` (development only) and `python -m app.cli migrate`."""
import subprocess
import sys

from app.core.db import session_factory


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "migrate":
        return subprocess.call([sys.executable, "-m", "alembic", "upgrade", "head"])
    if cmd == "seed":
        from app.services.seed import DEMO_PASSWORD, seed_demo
        with session_factory()() as db:
            ids = seed_demo(db)
        print(f"Seeded demo workspace {ids['workspace_id']} / project {ids['project_id']}.")
        print(f"Demo accounts (development only): admin@demo.example, manager@demo.example, member@demo.example "
              f"- password: {DEMO_PASSWORD}")
        return 0
    print("usage: python -m app.cli [migrate|seed]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
