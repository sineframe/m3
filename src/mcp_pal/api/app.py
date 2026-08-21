import os, shutil, subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session
from ..config import Settings, get_settings
from ..persistence.database import Base, get_db, init_db, make_engine
from ..persistence.models import McpProfile, McpProfileRevision, Run, RunEvent, now, uid
from .schemas import ProfileCreate, RevisionCreate, RunClone, RunCreate, RunOut
from ..domain.validation import ProfileValidationError, referenced_environment_variables, selected_server_config, validate_mcp_config
from ..services.run_manager import RunManager

def _iso(v): return v.isoformat() if v else None
REQUIRED_CLI_FLAGS=("--print","--bare","--output-format","--verbose","--strict-mcp-config","--mcp-config","--no-session-persistence")
def profile_json(p: McpProfile, include_json=False):
    revisions = sorted(p.revisions, key=lambda r: r.revision_number)
    out = {"id":p.id,"name":p.name,"description":p.description,"archived":p.archived,"current_revision_id":p.current_revision_id,"created_at":_iso(p.created_at),"updated_at":_iso(p.updated_at),"revisions":[{"id":r.id,"revision_number":r.revision_number,"created_at":_iso(r.created_at), **({"mcp_json":r.mcp_json} if include_json else {})} for r in revisions]}
    if include_json and revisions:
        current = next((r for r in revisions if r.id == p.current_revision_id), revisions[-1])
        try: out["validation"] = validate_mcp_config(current.mcp_json)
        except ProfileValidationError as e: out["validation"] = {"valid": False, "errors": e.errors, "warnings": e.warnings}
    return out

def create_app(settings: Settings | None = None, engine_override=None, session_factory=None) -> FastAPI:
    settings = settings or get_settings(); eng = engine_override or make_engine(settings.database_url)
    factory = session_factory or __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(eng)
    # Any process that was alive before restart cannot be resumed.
    db=factory(); db.query(Run).filter(Run.status.in_(["queued","running"])).update({Run.status:"failed", Run.error_message:"Interrupted by backend restart", Run.finished_at:now()}, synchronize_session=False); db.commit(); db.close()
    manager=RunManager(factory, settings)
    @asynccontextmanager
    async def lifespan(application):
        yield
        manager.shutdown()
    app=FastAPI(title="MCP Testing Platform", version="0.1.0", description="Agent-facing API for local MCP testing", lifespan=lifespan)
    app.state.settings, app.state.session_factory, app.state.manager = settings, factory, manager
    def db_dep():
        d=factory()
        try: yield d
        finally: d.close()
    router=APIRouter(prefix="/api/v1")
    @router.get("/health")
    def health():
        d=factory(); db_ok=True
        try: d.execute(__import__("sqlalchemy").text("SELECT 1"))
        except Exception: db_ok=False
        finally: d.close()
        executable=shutil.which(settings.claude_executable) or (os.path.isfile(settings.claude_executable) and os.access(settings.claude_executable,os.X_OK))
        flags_ok=False; missing=list(REQUIRED_CLI_FLAGS)
        if executable:
            try:
                probe=subprocess.run([settings.claude_executable,"--help"],capture_output=True,text=True,timeout=5,check=False)
                help_text=probe.stdout+probe.stderr
                missing=[f for f in REQUIRED_CLI_FLAGS if f not in help_text]; flags_ok=probe.returncode == 0 and not missing
            except (OSError, subprocess.SubprocessError): pass
        checks={"api_key":bool(settings.anthropic_api_key),"database":db_ok,"claude_executable":bool(executable),"required_cli_flags":{"ok":flags_ok,"missing":missing}}
        ready=checks["api_key"] and db_ok and bool(executable) and flags_ok
        return {"status":"ok" if ready else "degraded","ready":ready,"checks":checks}
    @router.get("/capabilities")
    def capabilities(): return {"harnesses":["claude-code"],"models":settings.model_ids(),"tool_modes":["mcp_only","mcp_read_only","full"],"limits":{"timeout_seconds":settings.run_timeout_seconds,"max_turns":settings.claude_max_turns,"max_budget_usd":settings.claude_max_budget_usd}}
    @router.get("/profiles")
    def profiles(include_archived: bool=False, d:Session=Depends(db_dep)):
        q=d.query(McpProfile)
        if not include_archived: q=q.filter_by(archived=False)
        return [profile_json(p) for p in q.order_by(McpProfile.name).all()]
    @router.post("/profiles", status_code=201)
    def create_profile(body: ProfileCreate, d:Session=Depends(db_dep)):
        p=McpProfile(name=body.name,description=body.description); d.add(p); d.flush(); r=McpProfileRevision(profile_id=p.id,revision_number=1,mcp_json=body.mcp_json); d.add(r); d.flush(); p.current_revision_id=r.id; d.commit(); d.refresh(p); return profile_json(p,True)
    @router.get("/profiles/{profile_id}")
    def get_profile(profile_id:str, d:Session=Depends(db_dep)):
        p=d.get(McpProfile,profile_id)
        if not p: raise HTTPException(404,"Profile not found")
        return profile_json(p,True)
    @router.post("/profiles/{profile_id}/revisions", status_code=201)
    def add_revision(profile_id:str, body:RevisionCreate, d:Session=Depends(db_dep)):
        p=d.get(McpProfile,profile_id)
        if not p: raise HTTPException(404,"Profile not found")
        if p.archived: raise HTTPException(422,"Profile is archived")
        n=max([r.revision_number for r in p.revisions] or [0])+1; r=McpProfileRevision(profile_id=p.id,revision_number=n,mcp_json=body.mcp_json); d.add(r); d.flush(); p.current_revision_id=r.id; d.commit(); return profile_json(p,True)
    @router.post("/profiles/{profile_id}/archive")
    def archive(profile_id:str,d:Session=Depends(db_dep)):
        p=d.get(McpProfile,profile_id)
        if not p: raise HTTPException(404,"Profile not found")
        p.archived=True; d.commit(); return profile_json(p)
    @router.post("/profiles/{profile_id}/restore")
    def restore(profile_id:str,d:Session=Depends(db_dep)):
        p=d.get(McpProfile,profile_id)
        if not p: raise HTTPException(404,"Profile not found")
        p.archived=False; d.commit(); return profile_json(p)
    def run_json(r, d=None):
        out=RunOut.model_validate(r).model_dump(mode="json")
        now_value=datetime.now(timezone.utc)
        start=r.started_at or r.created_at; end=r.finished_at or now_value
        if start and start.tzinfo is None: start=start.replace(tzinfo=timezone.utc)
        if end and end.tzinfo is None: end=end.replace(tzinfo=timezone.utc)
        out["elapsed_seconds"]=max(0.0,(end-start).total_seconds()) if start and end else 0.0
        out["queue_position"]=None
        if d is not None and r.status == "queued":
            out["queue_position"]=d.query(Run).filter(Run.status == "queued", Run.created_at < r.created_at).count()+1
        return out
    @router.post("/runs", status_code=202)
    def create_run(body:RunCreate,d:Session=Depends(db_dep)):
        if body.model not in settings.model_ids(): raise HTTPException(422,"Model is not configured")
        rev=d.get(McpProfileRevision,body.profile_revision_id)
        if not rev: raise HTTPException(404,"Profile revision not found")
        p=d.get(McpProfile,rev.profile_id)
        if p.archived: raise HTTPException(422,"Profile is archived")
        try: selected_server_config(rev.mcp_json,body.enabled_server)
        except ProfileValidationError as e: raise HTTPException(422,str(e))
        r=Run(profile_revision_id=rev.id,enabled_server=body.enabled_server,harness=body.harness,model=body.model,tool_mode=body.tool_mode,prompt=body.prompt,expected_output=body.expected_output,timeout_seconds=settings.run_timeout_seconds,max_turns=settings.claude_max_turns,max_budget_usd=settings.claude_max_budget_usd)
        d.add(r); d.commit(); d.refresh(r); manager.submit(r.id); return run_json(r,d)
    @router.get("/runs")
    def runs(status_filter:str|None=Query(None, alias="status"), profile_id:str|None=None, model:str|None=None, limit:int=Query(100,ge=1,le=500), offset:int=Query(0,ge=0), d:Session=Depends(db_dep)):
        q=d.query(Run)
        if status_filter:q=q.filter_by(status=status_filter)
        if profile_id:
            revision_ids=[x[0] for x in d.query(McpProfileRevision.id).filter_by(profile_id=profile_id).all()]
            q=q.filter(Run.profile_revision_id.in_(revision_ids or ["__none__"]))
        if model: q=q.filter_by(model=model)
        return [run_json(r,d) for r in q.order_by(desc(Run.created_at)).offset(offset).limit(limit).all()]
    @router.get("/runs/{run_id}")
    def get_run(run_id:str,d:Session=Depends(db_dep)):
        r=d.get(Run,run_id)
        if not r: raise HTTPException(404,"Run not found")
        return run_json(r,d)
    @router.get("/runs/{run_id}/events")
    def events(run_id:str,d:Session=Depends(db_dep)):
        if not d.get(Run,run_id): raise HTTPException(404,"Run not found")
        return [{"sequence":e.sequence,"timestamp":_iso(e.timestamp),"event_type":e.event_type,"payload":e.payload,"raw_event":e.raw_event} for e in d.query(RunEvent).filter_by(run_id=run_id).order_by(RunEvent.sequence).all()]
    @router.post("/runs/{run_id}/clone", status_code=202)
    def clone(run_id:str, body:RunClone|None=None,d:Session=Depends(db_dep)):
        old=d.get(Run,run_id)
        if not old: raise HTTPException(404,"Run not found")
        o=body or RunClone(); rev_id=o.profile_revision_id or old.profile_revision_id
        if o.use_latest_revision:
            original_profile = d.get(McpProfileRevision, old.profile_revision_id)
            profile = d.get(McpProfile, original_profile.profile_id) if original_profile else None
            rev_id = profile.current_revision_id if profile else rev_id
        rev=d.get(McpProfileRevision,rev_id)
        if not rev: raise HTTPException(404,"Profile revision not found")
        profile=d.get(McpProfile,rev.profile_id)
        if profile and profile.archived: raise HTTPException(422,"Profile is archived")
        model=o.model or old.model
        if model not in settings.model_ids(): raise HTTPException(422,"Model is not configured")
        prompt=o.prompt if o.prompt is not None else old.prompt; expected=o.expected_output if o.expected_output is not None else old.expected_output
        if not prompt.strip() or not expected.strip(): raise HTTPException(422,"Prompt and expected output must be nonblank")
        r=Run(parent_run_id=old.id,profile_revision_id=rev.id,enabled_server=o.enabled_server or old.enabled_server,harness=o.harness or old.harness,model=model,tool_mode=o.tool_mode or old.tool_mode,prompt=prompt,expected_output=expected,timeout_seconds=settings.run_timeout_seconds,max_turns=settings.claude_max_turns,max_budget_usd=settings.claude_max_budget_usd)
        try:selected_server_config(rev.mcp_json,r.enabled_server)
        except ProfileValidationError as e: raise HTTPException(422,str(e))
        d.add(r);d.commit();d.refresh(r);manager.submit(r.id);return run_json(r,d)
    @router.post("/runs/{run_id}/cancel")
    def cancel(run_id:str,d:Session=Depends(db_dep)):
        r=d.get(Run,run_id)
        if not r: raise HTTPException(404,"Run not found")
        manager.cancel(run_id); d.refresh(r); return run_json(r,d)
    @router.delete("/runs/{run_id}", status_code=204)
    def delete_run(run_id:str,d:Session=Depends(db_dep)):
        r=d.get(Run,run_id)
        if not r: raise HTTPException(404,"Run not found")
        if r.status in ("queued", "running"): raise HTTPException(409,"Active runs cannot be deleted; cancel and wait first")
        d.query(RunEvent).filter_by(run_id=run_id).delete(synchronize_session=False); d.delete(r); d.commit(); return Response(status_code=204)
    @router.delete("/runs", status_code=204)
    def clear_runs(confirm:bool=False, body:dict|None=Body(None), d:Session=Depends(db_dep)):
        confirm = confirm or bool(body and body.get("confirm"))
        if not confirm: raise HTTPException(400,"Pass confirm=true to clear history")
        active=d.query(Run).filter(Run.status.in_(["queued", "running"])).count()
        if active: raise HTTPException(409,"Active runs must be cancelled and finished before clearing history")
        for r in d.query(Run).all():
            d.query(RunEvent).filter_by(run_id=r.id).delete(synchronize_session=False); d.delete(r)
        d.commit(); return Response(status_code=204)
    @router.get("/runs/{run_id}/report")
    def report(run_id:str,d:Session=Depends(db_dep)):
        r=d.get(Run,run_id)
        if not r: raise HTTPException(404,"Run not found")
        rev=d.get(McpProfileRevision,r.profile_revision_id); ev=d.query(RunEvent).filter_by(run_id=run_id).order_by(RunEvent.sequence).all()
        from ..domain.events import derive_mcp_summary
        return {"run":run_json(r,d),"profile_revision":{"id":rev.id,"revision_number":rev.revision_number,"mcp_json":rev.mcp_json},"assertions":{"lifecycle":r.status,"mcp":{"status":r.mcp_assertion},"semantic":{"status":r.semantic_assertion,"reason":r.semantic_reason}},"events":[{"sequence":e.sequence,"timestamp":_iso(e.timestamp),"event_type":e.event_type,"payload":e.payload,"raw_event":e.raw_event} for e in ev],"stderr":r.stderr,"mcp_summary":derive_mcp_summary([e.raw_event for e in ev],r.enabled_server),"high_risk":r.tool_mode == "full","warning":"SQLite and reports contain unredacted trace data"}
    app.include_router(router)
    return app

app=create_app()
