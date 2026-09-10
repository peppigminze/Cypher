"""
Project registry (project spec section 28) - lets Cypher resolve
"arbeite an meinem Cypher-Projekt" to real context (path, tech stack,
repo, notes) instead of guessing. Managed by the human user via the
PWA; the agent only ever reads it (see tools/project_tools.py).
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database.db import get_db
from app.database.models import Project
from app.schemas import ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=List[ProjectOut])
def list_projects(db: Session = Depends(get_db), _user=Depends(require_user)) -> list[Project]:
    stmt = select(Project).order_by(Project.name.asc())
    return list(db.execute(stmt).scalars().all())


@router.post("", response_model=ProjectOut)
def create_project(
    payload: ProjectCreate, db: Session = Depends(get_db), _user=Depends(require_user)
) -> Project:
    existing = db.query(Project).filter(Project.name == payload.name).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Project '{payload.name}' already exists.")

    project = Project(
        name=payload.name,
        path=payload.path,
        description=payload.description,
        repository=payload.repository,
        notes=payload.notes,
    )
    project.technologies = payload.technologies
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    _user=Depends(require_user),
) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(project, field, value)

    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}")
def delete_project(
    project_id: int, db: Session = Depends(get_db), _user=Depends(require_user)
) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    db.delete(project)
    db.commit()
    return {"id": project_id, "deleted": True}
