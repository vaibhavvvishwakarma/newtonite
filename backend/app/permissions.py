"""Authorization: who may do what.

ALL permission decisions live in this one file and are called by the service
layer on every request. The frontend hides buttons too, but that is only for
convenience; the server never trusts it.

Roles are per team:

    viewer  - can read items and history of the team
    member  - viewer + create, edit, comment, claim, move items they own
    lead    - member + assign anyone, approve/reject, move any item

Permission matrix (scoped to the item's team):

    action                         viewer  member         lead
    -----------------------------  ------  -------------  ----
    read item / list / history     yes     yes            yes
    create item                    -       yes            yes
    edit fields                    -       yes            yes
    comment                        -       yes            yes
    claim unassigned item          -       yes            yes
    release item                   -       own only       yes
    assign to someone else         -       -              yes
    move status (normal steps)     -       own only       yes
    approve / reject               -       -              yes
"""
from .errors import ApiError, forbidden
from .security import CurrentUser

ROLE_RANK = {"viewer": 1, "member": 2, "lead": 3}


def role_in_team(user: CurrentUser, team_id: int) -> str | None:
    return user.roles.get(team_id)


def require_team_access(user: CurrentUser, team_id: int, min_role: str = "viewer") -> str:
    """Raise 403 unless the user has at least `min_role` in this team. Returns the role."""
    role = role_in_team(user, team_id)
    if role is None:
        raise forbidden("You are not a member of this team")
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise forbidden(f"This action needs the '{min_role}' role in this team (you are '{role}')")
    return role


def is_lead(user: CurrentUser, team_id: int) -> bool:
    return role_in_team(user, team_id) == "lead"


def require_owner_or_lead(user: CurrentUser, item: dict, action: str) -> None:
    """Members may act only on items assigned to them; leads may act on any item in their team."""
    require_team_access(user, item["team_id"], "member")
    if is_lead(user, item["team_id"]):
        return
    if item["assignee_id"] != user.id:
        raise forbidden(f"Only the assignee or a team lead can {action} this item")


def require_assignable(target_role: str | None) -> None:
    if target_role is None or ROLE_RANK[target_role] < ROLE_RANK["member"]:
        raise ApiError(422, "invalid_assignee", "Items can only be assigned to members or leads of the item's team")
