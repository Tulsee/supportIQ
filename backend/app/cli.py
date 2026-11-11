"""Management commands.

    uv run python -m app.cli grant-superadmin admin@example.com
    uv run python -m app.cli revoke-superadmin admin@example.com
    uv run python -m app.cli purge-expired
    uv run python -m app.cli export-openapi --output openapi.json

Super admin can only be bootstrapped here (there is no API to grant it from
scratch); afterwards super admins can manage each other via /api/v1/admin.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.core.database import SessionLocal, engine
from app.core.exceptions import NotFoundError
from app.services.maintenance import purge_expired
from app.services.platform_admin import PlatformAdminService


async def _set_superadmin(email: str, value: bool) -> int:
    async with SessionLocal() as session:
        try:
            user = await PlatformAdminService(session).set_superadmin(email.lower(), value)
        except NotFoundError as exc:
            print(exc.message, file=sys.stderr)
            return 1
    print(f"{user.email}: super admin = {user.is_superadmin}")
    return 0


async def _purge_expired() -> int:
    async with SessionLocal() as session:
        result = await purge_expired(session)
    print(
        f"Deleted {result.refresh_tokens} expired refresh tokens "
        f"and {result.invitations} stale invitations; "
        f"marked {result.stalled_ingestions} stalled document ingestions as failed"
    )
    return 0


def _export_openapi(output: Path | None) -> int:
    """Write the API contract (no server or database needed). The frontend's typed
    client is generated from it, and CI checks that the two stay in sync."""
    from app.main import app  # imported lazily: only this command needs the app

    document = json.dumps(app.openapi(), indent=2) + "\n"
    if output is None:
        sys.stdout.write(document)
    else:
        output.write_text(document, encoding="utf-8")
    return 0


async def _run(args: argparse.Namespace) -> int:
    try:
        if args.command == "grant-superadmin":
            return await _set_superadmin(args.email, True)
        if args.command == "revoke-superadmin":
            return await _set_superadmin(args.email, False)
        return await _purge_expired()
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    grant = commands.add_parser("grant-superadmin", help="Make a registered user a super admin")
    grant.add_argument("email")
    revoke = commands.add_parser("revoke-superadmin", help="Remove super admin from a user")
    revoke.add_argument("email")
    commands.add_parser("purge-expired", help="Delete expired refresh tokens and stale invitations")
    export = commands.add_parser("export-openapi", help="Write the OpenAPI document as JSON")
    export.add_argument("--output", type=Path, help="File to write (default: stdout)")
    args = parser.parse_args(argv)
    if args.command == "export-openapi":
        return _export_openapi(args.output)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
