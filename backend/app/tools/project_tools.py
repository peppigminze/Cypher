"""
Project context tool (project spec section 28). Read-only - the agent
never creates/edits projects itself, only the human user does via the
PWA (app/api/projects.py). Lets the model resolve "arbeite an meinem
Cypher-Projekt" to real path/tech-stack/repo context instead of
guessing or hallucinating details.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Project
from app.tools.base import Tool, ToolResult, ToolSecurity


class ListProjectsTool(Tool):
    name = "list_projects"
    description = (
        "List the user's known projects (name, path, description, tech stack, "
        "repository, notes) - use this to resolve a project the user refers to by name."
    )
    parameters = {"type": "object", "properties": {"name": {"type": "string"}}}
    security = ToolSecurity.SAFE

    async def execute(self, db: Session, name: str | None = None, **_) -> ToolResult:
        stmt = select(Project).order_by(Project.name.asc())
        if name:
            stmt = stmt.where(Project.name.ilike(f"%{name}%"))
        projects = db.execute(stmt).scalars().all()
        data = [
            {
                "name": p.name,
                "path": p.path,
                "description": p.description,
                "technologies": p.technologies,
                "repository": p.repository,
                "notes": p.notes,
            }
            for p in projects
        ]
        return ToolResult(success=True, data=data)
