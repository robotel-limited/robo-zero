"""Rute pentru regulile învățate ale unui proiect (pagina /projects):
listare, editare, aprobare, respingere. Propunerile vin din project_digest.py;
doar cele aprobate intră în contextul agentului (project_context.py)."""

from fastapi import APIRouter, HTTPException

import project_rules_store as rules
import projects_store

router = APIRouter()


def _project_or_404(project_id: str) -> None:
    if projects_store.get(project_id) is None:
        raise HTTPException(status_code=404, detail="Proiect inexistent.")


def _rule_or_404(project_id: str, rule_id: int) -> dict:
    rule = rules.get_rule(rule_id)
    if rule is None or rule["project_id"] != project_id:
        raise HTTPException(status_code=404, detail="Regulă inexistentă.")
    return rule


@router.get("/api/projects/{project_id}/rules")
async def list_rules(project_id: str):
    _project_or_404(project_id)
    return rules.list_for(project_id)


@router.patch("/api/projects/{project_id}/rules/{rule_id}")
async def update_rule(project_id: str, rule_id: int, body: dict):
    _rule_or_404(project_id, rule_id)
    body = body or {}
    return rules.update_text(rule_id, text=body.get("text"), category=body.get("category"))


@router.post("/api/projects/{project_id}/rules/{rule_id}/approve")
async def approve_rule(project_id: str, rule_id: int):
    rule = _rule_or_404(project_id, rule_id)
    if rule["status"] != "pending":
        raise HTTPException(status_code=400, detail="Regula nu mai e în așteptare.")
    return rules.approve(rule_id)


@router.post("/api/projects/{project_id}/rules/{rule_id}/reject")
async def reject_rule(project_id: str, rule_id: int):
    rule = _rule_or_404(project_id, rule_id)
    if rule["status"] != "pending":
        raise HTTPException(status_code=400, detail="Regula nu mai e în așteptare.")
    return rules.reject(rule_id)
