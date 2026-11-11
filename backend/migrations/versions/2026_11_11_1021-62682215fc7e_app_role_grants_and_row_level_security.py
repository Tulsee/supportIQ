"""app role grants and row level security

Defense in depth for tenant isolation. The API connects as a least-privilege role
(``DATABASE_URL``) that owns nothing and cannot bypass RLS, so these policies
apply to every query it makes, even one that forgets its organization filter.

Policies read transaction-local settings applied by ``app.core.database``:
  app.current_organization_id  -> the organization the request acts in
  app.current_user_id          -> the authenticated user
  app.rls_bypass               -> 'on' only inside explicit system operations
                                  (token lookups, platform admin, maintenance)

Threat model: this protects against application bugs (a missing ``WHERE
organization_id = ...``), not against an attacker who can run arbitrary SQL as
the app role, since such an attacker could set ``app.rls_bypass`` themselves.

``users`` and ``refresh_tokens`` are platform/auth tables, not tenant data, and
are intentionally left without RLS.

Revision ID: 62682215fc7e
Revises: e26219b2cd3d
Create Date: 2026-11-11 10:21:08.389322

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "62682215fc7e"
down_revision: str | Sequence[str] | None = "e26219b2cd3d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BYPASS = "app_rls_bypass()"
IN_CURRENT_ORG = "organization_id = app_current_organization_id()"
TENANT_TABLES = ("organization_members", "organization_invitations")


def _app_role() -> str:
    """The runtime role, quoted for use in DDL. Must already exist."""
    role = context.config.attributes["app_role"]
    if not context.is_offline_mode():
        exists = (
            op.get_bind()
            .execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role})
            .scalar()
        )
        if not exists:
            raise RuntimeError(
                f"Database role {role!r} does not exist. Create it first, see "
                "infrastructure/docker/postgres/init/02-create-app-role.sql"
            )
    return op.get_context().dialect.identifier_preparer.quote(role)


def upgrade() -> None:
    role = _app_role()

    # --- Least-privilege grants ------------------------------------------------------
    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
    # Tables/sequences created by later migrations (run as this owner) get the same.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}"
    )
    # The app may read the schema version, never change it.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {role}")

    # --- Context accessors (unset or '' -> NULL / false) ------------------------------
    op.execute(
        """
        CREATE FUNCTION app_current_organization_id() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$ SELECT NULLIF(current_setting('app.current_organization_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app_current_user_id() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$ SELECT NULLIF(current_setting('app.current_user_id', true), '')::uuid $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app_rls_bypass() RETURNS boolean
        LANGUAGE sql STABLE
        AS $$ SELECT coalesce(current_setting('app.rls_bypass', true), '') = 'on' $$
        """
    )

    # --- organizations ------------------------------------------------------------------
    # Readable when it is the current organization or the user is a member of it.
    # Writable only as the current organization (creating one sets the context to
    # the new id first).
    current = f"{BYPASS} OR id = app_current_organization_id()"
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY organizations_select ON organizations FOR SELECT USING (
            {current}
            OR id IN (
                SELECT organization_id FROM organization_members
                WHERE user_id = app_current_user_id()
            )
        )
        """
    )
    op.execute(
        f"CREATE POLICY organizations_insert ON organizations FOR INSERT WITH CHECK ({current})"
    )
    op.execute(
        f"CREATE POLICY organizations_update ON organizations FOR UPDATE "
        f"USING ({current}) WITH CHECK ({current})"
    )
    op.execute(f"CREATE POLICY organizations_delete ON organizations FOR DELETE USING ({current})")

    # --- organization_members -------------------------------------------------------------
    # Readable within the current organization, plus the user's own memberships in
    # other organizations (to list "my organizations"). Writable only within the
    # current organization.
    tenant = f"{BYPASS} OR {IN_CURRENT_ORG}"
    op.execute("ALTER TABLE organization_members ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organization_members_select ON organization_members FOR SELECT "
        f"USING ({tenant} OR user_id = app_current_user_id())"
    )
    op.execute(
        f"CREATE POLICY organization_members_insert ON organization_members FOR INSERT "
        f"WITH CHECK ({tenant})"
    )
    op.execute(
        f"CREATE POLICY organization_members_update ON organization_members FOR UPDATE "
        f"USING ({tenant}) WITH CHECK ({tenant})"
    )
    op.execute(
        f"CREATE POLICY organization_members_delete ON organization_members FOR DELETE "
        f"USING ({tenant})"
    )

    # --- organization_invitations ---------------------------------------------------------
    # Only visible within the current organization. Token lookups by the invitee
    # (who is not a member yet) run as an explicit bypass.
    op.execute("ALTER TABLE organization_invitations ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY organization_invitations_tenant ON organization_invitations FOR ALL "
        f"USING ({tenant}) WITH CHECK ({tenant})"
    )


def downgrade() -> None:
    role = _app_role()

    op.execute("DROP POLICY organization_invitations_tenant ON organization_invitations")
    for action in ("select", "insert", "update", "delete"):
        op.execute(f"DROP POLICY organization_members_{action} ON organization_members")
        op.execute(f"DROP POLICY organizations_{action} ON organizations")
    for table in (*TENANT_TABLES, "organizations"):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION app_rls_bypass()")
    op.execute("DROP FUNCTION app_current_user_id()")
    op.execute("DROP FUNCTION app_current_organization_id()")

    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {role}"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {role}"
    )
    op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role}")
