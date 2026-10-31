from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from rotalog.core.contracts import Page
from rotalog.core.data_mode import is_synthetic_data
from rotalog.core.database import transactional_session
from rotalog.identity.schemas import UserResponse
from rotalog.organizations.models import Membership, Organization


def create_organization(
    session: Session, *, name: str, demo_run_id: UUID, kind: str
) -> Organization:
    organization = Organization(name=name, demo_run_id=demo_run_id, kind=kind)
    session.add(organization)
    session.flush()
    return organization


def grant_initial_membership(
    session: Session, *, user_id: UUID, organization_id: UUID, role: str
) -> None:
    """Cadastro na mesma transação; não substitui o futuro fluxo de convite."""
    session.add(Membership(user_id=user_id, organization_id=organization_id, role=role))
    session.flush()


def list_suppliers(
    actor: UserResponse, factory: sessionmaker[Session], page: int, page_size: int
) -> Page[dict]:
    from rotalog.identity.service import authorize

    with transactional_session(factory) as session:
        authorize(session, actor)
        query = select(Organization).where(
            Organization.demo_run_id == actor.demo_run_id,
            Organization.kind == "SUPPLIER",
        )
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        return Page(
            items=[
                {
                    "id": str(item.id),
                    "name": item.name,
                    "is_synthetic": is_synthetic_data(),
                }
                for item in session.scalars(
                    query.order_by(Organization.name, Organization.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
