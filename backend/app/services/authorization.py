"""Role checks shared by every tenant-scoped service.

Endpoints resolve *who* the caller is and *which organization* they act in (see
``app.api.deps``); services decide *what* that membership may do by calling
``require_role`` before touching data.
"""

from app.core.exceptions import PermissionDeniedError
from app.models import OrganizationMember, OrgRole

STAFF_ROLES = (OrgRole.ADMIN, OrgRole.AGENT)


def require_role(membership: OrganizationMember, *allowed: OrgRole) -> None:
    if membership.role not in allowed:
        raise PermissionDeniedError("Your role in this organization does not allow this action")
