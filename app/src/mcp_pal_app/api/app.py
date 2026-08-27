import os, re, shutil, subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import version as distribution_version
from pathlib import Path
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import desc
from sqlalchemy.orm import Session
from mcp_pal_app.settings import Settings, get_settings
from mcp_pal_app.persistence.database import Base, get_db, init_db, make_engine
from mcp_pal_app.persistence.models import McpProfile, McpProfileRevision, Run, RunEvent, RunTrace, RunHarnessSnapshot, HarnessProfile, HarnessProfileRevision, HarnessProbe, now, uid
from mcp_pal_app.persistence.seeds import ensure_builtin_profiles
from .schemas import ProfileCreate, RevisionCreate, RunClone, RunCreate, RunOut, HarnessProfileCreate, HarnessRevisionCreate
from mcp_pal.domain.validation import ProfileValidationError, referenced_environment_variables, selected_server_config, validate_mcp_config
from mcp_pal.harness.manifest import validate_manifest, ManifestValidationError
from mcp_pal_app.services.run_manager import RunManager
from mcp_pal.trace.claude import transport_for_server
from mcp_pal.trace.redaction import RedactionConfig, redact_for_api
from mcp_pal.storage import SQLiteExecutionStore
from .v2 import install_v2


def _api_projection(value, *, path="$"):
    """Apply the shared redaction policy at the HTTP response boundary."""
    # Build this at request time so credentials injected for a deterministic
    # worker/test process are covered without retaining them in app state.
    return redact_for_api(value, config=RedactionConfig.from_environment(), path=path)

def _iso(v): return v.isoformat() if v else None

def _package_version() -> str:
    return distribution_version("mcp-pal")

REQUIRED_CLI_FLAGS=("--print","--bare","--output-format","--verbose","--strict-mcp-config","--mcp-config","--no-session-persistence")
REQUIRED_OPENCODE_FLAGS=("run","--format","--model","--thinking","--pure")

def _probe(executable, flags, command=()):
    resolved=shutil.which(executable) or (os.path.isfile(executable) and os.access(executable,os.X_OK))
    missing=list(flags)
    if resolved:
        try:
            probe=subprocess.run([executable,*command,"--help"],capture_output=True,text=True,timeout=5,check=False)
            help_text=probe.stdout+probe.stderr; missing=[flag for flag in flags if flag not in help_text]
            return bool(probe.returncode == 0 and not missing), missing, True
        except (OSError,subprocess.SubprocessError): pass
    return False,missing,bool(resolved)

def _saved_opencode_auth(executable, providers=None):
    try:
        probe=subprocess.run([executable,"auth","list"],capture_output=True,text=True,timeout=5,check=False)
        text=(probe.stdout+probe.stderr).lower()
        if probe.returncode != 0: return False
        if providers is None:
            return "credentials" in text and any(line.strip() and not line.lower().startswith(("credentials","┌","└","│")) for line in text.splitlines())
        aliases={"opencode-go":"opencode go","opencode":"opencode"}
        return any(re.search(r"(?<![a-z])" + re.escape(aliases.get(provider.lower(),provider.lower().replace("-", " ").replace("_", " "))) + r"(?![a-z])", text) for provider in providers)
    except (OSError,subprocess.SubprocessError): return False

def _opencode_provider_env(provider, settings):
    provider=provider.lower(); normalized=provider.replace("-", "_").upper()
    configured={
        "opencode": settings.opencode_api_key,
        "opencode-go": settings.opencode_api_key,
        "anthropic": settings.anthropic_api_key,
        "openrouter": settings.openrouter_api_key,
    }.get(provider)
    return bool(configured or os.environ.get(f"{normalized}_API_KEY") or os.environ.get(f"{normalized}_AUTH_TOKEN"))

def _manifest_local_ready(manifest):
    """Return local executable/environment readiness for a persisted manifest."""
    try:
        checked = validate_manifest(manifest, check_local=True)
    except ManifestValidationError:
        return False, [], None
    return bool(checked.get("local_ready")), checked.get("missing_environment", []), checked.get("executable")

def _model_option_value(config_options, session_config):
    """Resolve the selected model by ACP's option category, not an id name.

    ACP implementations are free to call the model option ``engine``,
    ``model_id``, etc.  Only verified protocol metadata is trusted by the
    caller; this helper deliberately has no fallback based on arbitrary config
    keys.
    """
    config_options=_config_option_list(config_options)
    if not isinstance(session_config, dict):
        return None
    for option in config_options:
        if not isinstance(option, dict) or str(option.get("category", "")).lower() != "model":
            continue
        ident = option.get("id") or option.get("configId") or option.get("key")
        if ident in session_config and isinstance(session_config[ident], str) and session_config[ident]:
            return session_config[ident]
        for key in ("currentValue", "current_value", "value", "default", "default_value"):
            if isinstance(option.get(key), str) and option[key]:
                return option[key]
    return None

def _latest_protocol_probe(db, revision_id):
    return db.query(HarnessProbe).filter_by(revision_id=revision_id, kind="protocol").order_by(desc(HarnessProbe.created_at)).first()

def _latest_full_probe(db, revision_id, transport, mode_id, session_config):
    import json as _json
    probes = db.query(HarnessProbe).filter_by(revision_id=revision_id, kind="full", transport=transport, mode_id=mode_id).order_by(desc(HarnessProbe.created_at)).all()
    wanted = _json.dumps(session_config or {}, sort_keys=True, separators=(",", ":"))
    return next((probe for probe in probes if _json.dumps(probe.session_config or {}, sort_keys=True, separators=(",", ":")) == wanted), None)

def _verification_for(db, revision, *, transport, mode_id, session_config):
    """Build provenance from the newest probes for the exact run dimensions."""
    protocol = _latest_protocol_probe(db, revision.id)
    full = _latest_full_probe(db, revision.id, transport, mode_id, session_config)
    protocol_verified = bool(protocol and protocol.status == "verified")
    identity_mismatch = bool(
        protocol_verified and full and full.status == "verified"
        and protocol.agent_identity and full.agent_identity
        and protocol.agent_identity != full.agent_identity
    )
    config_options = (protocol.evidence or {}).get("config_options", []) if protocol_verified else []
    effective_model = _model_option_value(config_options, session_config) or "agent-default"
    return {
        "trusted_unsandboxed": bool(revision.trusted_unsandboxed),
        "protocol_probe_id": protocol.id if protocol else None,
        "protocol_probe_status": protocol.status if protocol else "unverified",
        "full_probe_id": full.id if full else None,
        "full_probe_status": "identity_mismatch" if identity_mismatch else (full.status if full else "unverified"),
        "full_probe_dimensions": {"transport": transport, "mode_id": mode_id, "session_config": session_config or {}},
        "agent_identity": full.agent_identity if full else None,
        "effective_model": effective_model,
    }

def _listed_ids(value, *keys):
    if isinstance(value, dict):
        for key in keys:
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if not isinstance(value, list):
        return []
    ids=[]
    for item in value:
        if isinstance(item, dict):
            ident=item.get("id") or item.get("value") or item.get("configId")
        else:
            ident=item
        if ident is not None: ids.append(str(ident))
    return ids

def _config_option_list(value):
    if isinstance(value, dict):
        if value.get("id") or value.get("configId"):
            return [value]
        value=value.get("options") or value.get("configOptions") or value.get("config_options") or value.get("availableOptions") or []
    return value if isinstance(value, list) else []

def _select_option_values(options):
    """Flatten both ACP flat choices and official grouped select choices."""
    values=[]
    for item in options if isinstance(options,list) else []:
        if not isinstance(item,dict):
            values.append(item); continue
        if "value" in item:
            values.append(item["value"])
        elif isinstance(item.get("options"),list):
            values.extend(_select_option_values(item["options"]))
    return values

def _protocol_defaults(protocol):
    evidence=(protocol.evidence or {}) if protocol else {}
    raw_modes=evidence.get("modes",{})
    modes=_listed_ids(raw_modes,"availableModes","available_modes")
    mode=(raw_modes.get("currentModeId") or raw_modes.get("current_mode_id")) if isinstance(raw_modes,dict) else None
    if mode not in modes: mode=modes[0] if modes else None
    config={}
    for option in _config_option_list(evidence.get("config_options")):
        if not isinstance(option,dict): continue
        ident=option.get("id") or option.get("configId")
        if ident is None: continue
        value=None
        for key in ("currentValue","current_value","default","default_value"):
            if option.get(key) is not None:
                value=option[key]; break
        if value is None and isinstance(option.get("options"),list) and option["options"]:
            choices=_select_option_values(option["options"])
            value=choices[0] if choices else None
        if value is not None: config[str(ident)]=value
    return mode, config

def _validate_acp_dimensions(db, revision, *, mode_id, session_config):
    """Validate requested ACP dimensions against the newest protocol probe."""
    protocol=_latest_protocol_probe(db, revision.id)
    config=dict(session_config or {})
    if not protocol or protocol.status != "verified":
        if mode_id is not None or config:
            raise HTTPException(422,"Unprobed ACP runs require empty mode and session configuration")
        return
    evidence=protocol.evidence or {}
    modes=_listed_ids(evidence.get("modes"), "availableModes", "available_modes")
    if modes and mode_id is None:
        raise HTTPException(422,"ACP mode is required for a probed run")
    if mode_id is not None and (not modes or str(mode_id) not in modes):
        raise HTTPException(422,"ACP mode is not advertised by the latest protocol probe")
    options=_config_option_list(evidence.get("config_options"))
    option_map={str(x.get("id") or x.get("configId")):x for x in options if isinstance(x,dict) and (x.get("id") or x.get("configId")) is not None}
    unknown=sorted(set(config)-set(option_map))
    if unknown:
        raise HTTPException(422,"ACP session configuration contains unadvertised options: " + ", ".join(unknown))
    missing=sorted(set(option_map)-set(config))
    if missing:
        raise HTTPException(422,"ACP session configuration must include every advertised option: " + ", ".join(missing))
    for ident, value in config.items():
        option=option_map[ident]
        option_type=str(option.get("type") or "").lower()
        if option_type in {"boolean", "bool"}:
            if type(value) is not bool:
                raise HTTPException(422,f"ACP session option {ident} must be boolean")
        elif option_type in {"select", "enum", "string"} or option.get("options") is not None:
            allowed=option.get("options")
            allowed_values=_select_option_values(allowed)
            if not any(type(value) is type(candidate) and value == candidate for candidate in allowed_values):
                raise HTTPException(422,f"ACP session option {ident} has an unadvertised value")
        elif option_type:
            raise HTTPException(422,f"Unsupported ACP session option type: {option_type}")
def profile_json(p: McpProfile, include_json=False):
    revisions = sorted(p.revisions, key=lambda r: r.revision_number)
    out = {"id":p.id,"name":p.name,"description":p.description,"archived":p.archived,"current_revision_id":p.current_revision_id,"created_at":_iso(p.created_at),"updated_at":_iso(p.updated_at),"revisions":[{"id":r.id,"revision_number":r.revision_number,"created_at":_iso(r.created_at), **({"mcp_json":r.mcp_json} if include_json else {})} for r in revisions]}
    if include_json and revisions:
        current = next((r for r in revisions if r.id == p.current_revision_id), revisions[-1])
        try: out["validation"] = validate_mcp_config(current.mcp_json)
        except ProfileValidationError as e: out["validation"] = {"valid": False, "errors": e.errors, "warnings": e.warnings}
    return out

def create_app(settings: Settings | None = None, engine_override=None, session_factory=None, v2_store=None, v2_kit=None) -> FastAPI:
    settings = settings or get_settings(); eng = engine_override or make_engine(settings.database_url)
    # Resolve the application-selected path before handing it to the SDK
    # store. macOS commonly exposes the temporary directory through /var,
    # which is a harmless system symlink; the SDK still rejects symlinks in
    # the resolved database path itself.
    owns_v2_store = v2_store is None
    if owns_v2_store:
        v2_database = settings.database_path.removeprefix("sqlite:///")
        v2_store = SQLiteExecutionStore(str(Path(v2_database).expanduser().resolve()))
    factory = session_factory or __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(eng)
    # Any process that was alive before restart cannot be resumed.
    db=factory()
    try:
        ensure_builtin_profiles(db)
        db.query(Run).filter(Run.status.in_(["queued","running"])).update({Run.status:"failed", Run.error_message:"Interrupted by backend restart", Run.finished_at:now()}, synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    manager=RunManager(factory, settings)
    @asynccontextmanager
    async def lifespan(application):
        yield
        manager.shutdown()
        if application.state.v2_kit_owned:
            application.state.v2_kit.close()
        if application.state.v2_store_owned:
            v2_store.close()
    app=FastAPI(title="MCP Testing Platform", version=_package_version(), description="Agent-facing API for local MCP testing", lifespan=lifespan)
    app.state.settings, app.state.session_factory, app.state.manager = settings, factory, manager
    install_v2(app, v2_store, v2_kit)
    app.state.v2_store_owned = owns_v2_store
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
        claude_flags,claude_missing,claude_executable=_probe(settings.claude_executable,REQUIRED_CLI_FLAGS)
        opencode_flags,opencode_missing,opencode_executable=_probe(settings.opencode_executable,REQUIRED_OPENCODE_FLAGS,("run",))
        providers=settings.opencode_providers()
        opencode_auth=any(_opencode_provider_env(provider,settings) for provider in providers) or (opencode_executable and _saved_opencode_auth(settings.opencode_executable,providers))
        harnesses={
            "claude-code":{"ready":bool(db_ok and settings.anthropic_api_key and claude_executable and claude_flags),"api_key":bool(settings.anthropic_api_key),"executable":claude_executable,"required_cli_flags":{"ok":claude_flags,"missing":claude_missing}},
            "opencode":{"ready":bool(db_ok and opencode_auth and opencode_executable and opencode_flags),"api_key_or_saved_auth":bool(opencode_auth),"providers":providers,"executable":opencode_executable,"required_cli_flags":{"ok":opencode_flags,"missing":opencode_missing}},
        }
        checks={"api_key":bool(settings.anthropic_api_key),"database":db_ok,"claude_executable":claude_executable,"required_cli_flags":{"ok":claude_flags,"missing":claude_missing},"harnesses":harnesses}
        ready=db_ok and any(value["ready"] for value in harnesses.values())
        return {"status":"connected" if db_ok else "degraded","ready":ready,"run_ready":ready,"checks":checks,"harnesses":harnesses}
    @router.get("/capabilities")
    def capabilities(d:Session=Depends(db_dep)):
        claude_flags,_,claude_executable=_probe(settings.claude_executable,REQUIRED_CLI_FLAGS)
        opencode_flags,_,opencode_executable=_probe(settings.opencode_executable,REQUIRED_OPENCODE_FLAGS,("run",))
        providers=settings.opencode_providers()
        opencode_auth=any(_opencode_provider_env(provider,settings) for provider in providers) or (opencode_executable and _saved_opencode_auth(settings.opencode_executable,providers))
        claude_ready=bool(settings.anthropic_api_key and claude_executable and claude_flags)
        opencode_ready=bool(opencode_auth and opencode_executable and opencode_flags)
        out={"harnesses":[{"selection_id":"builtin:claude-code","kind":"builtin","harness":"claude-code","name":"Claude Code","ready":claude_ready,"models":settings.model_ids(),"tool_modes":["mcp_only","mcp_read_only","full"],"limits":{"timeout_seconds":settings.run_timeout_seconds,"max_turns":settings.claude_max_turns,"max_budget_usd":settings.claude_max_budget_usd}},{"selection_id":"builtin:opencode","kind":"builtin","harness":"opencode","name":"OpenCode","ready":opencode_ready,"models":settings.opencode_models(),"tool_modes":["mcp_only","mcp_read_only","full"],"limits":{"timeout_seconds":settings.run_timeout_seconds,"max_turns":None,"max_budget_usd":None}}]}
        for p in d.query(HarnessProfile).filter_by(archived=False).order_by(HarnessProfile.name).all():
            from mcp_pal_app.persistence.models import HarnessProbe
            probes=d.query(HarnessProbe).filter_by(revision_id=p.current_revision_id).order_by(desc(HarnessProbe.created_at)).all()
            protocol=next((x for x in probes if x.kind=="protocol"),None)
            dimensions={}
            import json as _json
            for candidate in probes:
                if candidate.kind != "full": continue
                key=(candidate.transport,candidate.mode_id,_json.dumps(candidate.session_config or {},sort_keys=True,separators=(",",":")))
                dimensions.setdefault(key,candidate)
            protocol_ok=bool(protocol and protocol.status=="verified")
            protocol_identity=protocol.agent_identity if protocol_ok else None
            verified_dimensions=[]
            identity_mismatches=[]
            for candidate in dimensions.values():
                if candidate.status != "verified":
                    continue
                mismatch=bool(protocol_identity and candidate.agent_identity and candidate.agent_identity != protocol_identity)
                if mismatch: identity_mismatches.append(candidate)
                else: verified_dimensions.append(candidate)
            # Bind identity before selecting the scalar representative.  A
            # mismatching dimension must not hide another latest dimension
            # that is verified for the current protocol identity.
            full=verified_dimensions[0] if verified_dimensions else None
            identity_warning=bool(identity_mismatches and not verified_dimensions)
            manifest=(d.get(HarnessProfileRevision,p.current_revision_id).manifest if p.current_revision_id else {})
            local_ready, missing_environment, executable = _manifest_local_ready(manifest)
            protocol_evidence=protocol.evidence if protocol_ok else {}
            options=_config_option_list(protocol_evidence.get("config_options",[])) if protocol_ok else []
            raw_modes=protocol_evidence.get("modes",{}) if protocol_ok else {}
            modes=_listed_ids(raw_modes,"availableModes","available_modes")
            mode_descriptors=(raw_modes.get("availableModes") or raw_modes.get("available_modes") or []) if isinstance(raw_modes,dict) else (raw_modes if isinstance(raw_modes,list) else [])
            current_mode_id=(raw_modes.get("currentModeId") or raw_modes.get("current_mode_id")) if isinstance(raw_modes,dict) else None
            full_verifications=[{"transport":x.transport,"mode_id":x.mode_id,"session_config":x.session_config,"status":x.status,"agent_identity":x.agent_identity,"identity_match":x in verified_dimensions,"evidence":x.evidence} for x in dimensions.values()]
            missing_identity=bool(full and full.status=="verified" and (full.agent_identity is None or protocol_identity is None))
            probe_states={"protocol": protocol.status if protocol else "unverified", "full": full.status if full else "unverified"}
            warnings=(["Identity changed; full verification downgraded"] if identity_warning else [])+(["Agent identity unavailable; verification limited"] if missing_identity else [])+([] if full and full.status=="verified" else ["Harness is not fully verified"])
            if executable is None: warnings.append("Executable is unavailable")
            if missing_environment: warnings.append("Missing environment variables: " + ", ".join(missing_environment))
            out["harnesses"].append({"selection_id":f"profile:{p.id}","kind":"acp","harness":"acp","name":p.name,"profile_id":p.id,"revision_id":p.current_revision_id,"models":["agent-default"],"tool_modes":["agent_default"],"agent_modes":mode_descriptors,"current_agent_mode_id":current_mode_id,"session_config_options":options,"ready":local_ready,"local_ready":local_ready,"local_ready_details":{"executable":bool(executable),"missing_environment":missing_environment,"probe_states":probe_states},"protocol_verified":protocol_ok,"full_verified":bool(full and full.status=="verified"),"full_verifications":full_verifications,"verification":{"protocol":protocol_evidence or None,"full":full.evidence if full else None},"warnings":warnings})
        return _api_projection(out, path="$.harnesses")
    @router.get("/harness-profiles")
    def harness_profiles(include_archived: bool=False, d:Session=Depends(db_dep)):
        q=d.query(HarnessProfile)
        if not include_archived: q=q.filter_by(archived=False)
        return [{"id":p.id,"name":p.name,"description":p.description,"archived":p.archived,"current_revision_id":p.current_revision_id,"revisions":[{"id":r.id,"revision_number":r.revision_number,"manifest":r.manifest,"trusted_unsandboxed":r.trusted_unsandboxed} for r in p.revisions]} for p in q.all()]
    @router.post("/harness-profiles", status_code=201)
    def create_harness_profile(body:HarnessProfileCreate, d:Session=Depends(db_dep)):
        if not body.trusted_unsandboxed:
            raise HTTPException(422,"Trusted unsandboxed acknowledgment is required")
        p=HarnessProfile(name=body.name,description=body.description); d.add(p); d.flush(); r=HarnessProfileRevision(profile_id=p.id,revision_number=1,manifest=body.manifest.model_dump(),trusted_unsandboxed=body.trusted_unsandboxed); d.add(r); d.flush(); p.current_revision_id=r.id; d.commit(); return {"id":p.id,"current_revision_id":r.id,"name":p.name}
    @router.get("/harness-profiles/{profile_id}")
    def get_harness_profile(profile_id:str,d:Session=Depends(db_dep)):
        p=d.get(HarnessProfile,profile_id)
        if not p: raise HTTPException(404,"Harness profile not found")
        return {"id":p.id,"name":p.name,"description":p.description,"archived":p.archived,"current_revision_id":p.current_revision_id,"revisions":[{"id":r.id,"revision_number":r.revision_number,"manifest":r.manifest,"trusted_unsandboxed":r.trusted_unsandboxed} for r in p.revisions]}
    @router.patch("/harness-profiles/{profile_id}")
    def update_harness_profile(profile_id:str, body:dict=Body(...), d:Session=Depends(db_dep)):
        p=d.get(HarnessProfile,profile_id)
        if not p: raise HTTPException(404,"Harness profile not found")
        if "name" in body: p.name=body["name"]
        if "description" in body: p.description=body["description"]
        d.commit(); return {"id":p.id,"name":p.name,"description":p.description}
    @router.post("/harness-profiles/{profile_id}/archive")
    def archive_harness_profile(profile_id:str,d:Session=Depends(db_dep)):
        p=d.get(HarnessProfile,profile_id)
        if not p: raise HTTPException(404,"Harness profile not found")
        p.archived=True; d.commit(); return {"id":p.id,"archived":True}
    @router.post("/harness-profiles/{profile_id}/restore")
    def restore_harness_profile(profile_id:str,d:Session=Depends(db_dep)):
        p=d.get(HarnessProfile,profile_id)
        if not p: raise HTTPException(404,"Harness profile not found")
        p.archived=False; d.commit(); return {"id":p.id,"archived":False}
    @router.post("/harness-profiles/{profile_id}/revisions",status_code=201)
    def add_harness_revision(profile_id:str, body:HarnessRevisionCreate,d:Session=Depends(db_dep)):
        p=d.get(HarnessProfile,profile_id)
        if not p: raise HTTPException(404,"Harness profile not found")
        if p.archived: raise HTTPException(422,"Harness profile is archived")
        if not body.trusted_unsandboxed: raise HTTPException(422,"Trusted unsandboxed acknowledgment is required")
        n=max([r.revision_number for r in p.revisions] or [0])+1; r=HarnessProfileRevision(profile_id=p.id,revision_number=n,manifest=body.manifest.model_dump(),trusted_unsandboxed=body.trusted_unsandboxed); d.add(r); d.flush(); p.current_revision_id=r.id; d.commit(); return {"id":r.id,"revision_number":n,"current_revision_id":r.id}
    @router.get("/harness-profiles/{profile_id}/export")
    def export_harness_profile(profile_id:str,d:Session=Depends(db_dep)):
        p=d.get(HarnessProfile,profile_id)
        if not p: raise HTTPException(404,"Harness profile not found")
        r=next((r for r in p.revisions if r.id==p.current_revision_id),p.revisions[-1]); return {"name":p.name,"description":p.description,"manifest":r.manifest}
    @router.post("/harness-profiles/import",status_code=201)
    def import_harness_profile(body:dict,d:Session=Depends(db_dep)):
        manifest=body.get("manifest") if isinstance(body.get("manifest"),dict) else (body if "command" in body else None)
        if manifest is None: raise HTTPException(422,"Import must contain a raw harness manifest or an exported profile wrapper")
        try: parsed=HarnessProfileCreate(name=body.get("name","Imported harness") if manifest is not body else "Imported harness",description=body.get("description","") if manifest is not body else "",manifest=manifest,trusted_unsandboxed=False)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        # Imported profiles are intentionally unverified.  They must not use
        # the normal trusted creation path, even if an importer includes a
        # stale/forged acknowledgment field.
        p=HarnessProfile(name=parsed.name,description=parsed.description); d.add(p); d.flush(); r=HarnessProfileRevision(profile_id=p.id,revision_number=1,manifest=parsed.manifest.model_dump(),trusted_unsandboxed=False); d.add(r); d.flush(); p.current_revision_id=r.id; d.commit(); return {"id":p.id,"current_revision_id":r.id,"name":p.name}
    @router.get("/harness-profiles/{profile_id}/probes")
    def harness_probes(profile_id:str,d:Session=Depends(db_dep)):
        revision_ids=[r.id for r in d.query(HarnessProfileRevision).filter_by(profile_id=profile_id).all()]
        from ..persistence.models import HarnessProbe
        return [{"id":p.id,"revision_id":p.revision_id,"kind":p.kind,"status":p.status,"transport":p.transport,"mode_id":p.mode_id,"session_config":p.session_config,"agent_identity":p.agent_identity,"evidence":p.evidence,"created_at":_iso(p.created_at)} for p in d.query(HarnessProbe).filter(HarnessProbe.revision_id.in_(revision_ids or ["__none__"])).order_by(desc(HarnessProbe.created_at)).all()]
    @router.post("/harness-profiles/{profile_id}/probe",status_code=202)
    def probe_harness(profile_id:str, kind:str="protocol", transport:str="stdio", mode_id:str|None=None, session_config:str="{}", d:Session=Depends(db_dep)):
        from ..persistence.models import HarnessProbe
        if kind not in {"protocol","full"}: raise HTTPException(422,"Probe kind must be protocol or full")
        p=d.get(HarnessProfile,profile_id)
        if not p: raise HTTPException(404,"Harness profile not found")
        if p.archived: raise HTTPException(422,"Harness profile is archived")
        rev=d.get(HarnessProfileRevision,p.current_revision_id)
        if not rev: raise HTTPException(404,"Harness revision not found")
        import json as _json
        try: config=_json.loads(session_config)
        except Exception: raise HTTPException(422,"session_config must be JSON")
        if not isinstance(config, dict): raise HTTPException(422,"session_config must be a JSON object")
        if kind == "full":
            protocol=d.query(HarnessProbe).filter_by(revision_id=rev.id,kind="protocol").order_by(desc(HarnessProbe.created_at)).first()
            if not protocol or protocol.status != "verified":
                raise HTTPException(422,"A verified protocol probe is required before a full probe")
            default_mode, default_config = _protocol_defaults(protocol)
            mode_id = mode_id if mode_id is not None else default_mode
            if not config: config=default_config
            _validate_acp_dimensions(d, rev, mode_id=mode_id, session_config=config)
        probe=HarnessProbe(revision_id=rev.id,kind=kind,status="queued",transport=transport,mode_id=mode_id,session_config=config,evidence={}); d.add(probe); d.commit(); d.refresh(probe)
        def execute_probe():
            local=d.get if False else None
            session_factory=app.state.session_factory; db2=session_factory(); row=db2.get(HarnessProbe,probe.id); row.status="running"; db2.commit()
            import asyncio
            from mcp_pal.harness.acp import AcpHarnessRunner, protocol_probe, full_probe
            try:
                # Probe launch/readiness is deliberately model-free; the full
                # nonce probe uses the normal ACP runner in run submissions.
                if kind == "protocol":
                    evidence=asyncio.run(protocol_probe(rev.manifest)); row.status=evidence.pop("status","failed"); row.evidence={**evidence,"manifest_revision_id":rev.id}
                else:
                    evidence=asyncio.run(full_probe(rev.manifest,mode_id=row.mode_id,session_config=row.session_config,transport=row.transport)); row.status=evidence.pop("status","failed"); row.evidence={**evidence,"manifest_revision_id":rev.id}
                # Some SDK versions expose session metadata only in the raw
                # session/new response, so recover it from captured frames.
                if kind == "protocol":
                    frames=(row.evidence or {}).get("frames") or []
                    for frame in frames:
                        payload=frame.get("payload") if isinstance(frame,dict) else None
                        value=payload.get("result") if isinstance(payload,dict) and isinstance(payload.get("result"),dict) else {}
                        if not row.evidence.get("config_options") and ("configOptions" in value or "config_options" in value): row.evidence["config_options"]=value.get("configOptions") or value.get("config_options")
                        if not row.evidence.get("modes") and "modes" in value: row.evidence["modes"]=value["modes"]
                        if not row.evidence.get("agent_info") and isinstance(value.get("agentInfo"),dict): row.evidence["agent_info"]=value["agentInfo"]
                    row.evidence=dict(row.evidence or {})
                row.agent_identity=(row.evidence or {}).get("agent_identity") or (row.evidence or {}).get("agent_info")
            except Exception as exc: row.status="failed"; row.evidence={"error":str(exc)}
            db2.commit(); db2.close()
        manager.executor.submit(execute_probe)
        return {"id":probe.id,"status":probe.status,"revision_id":rev.id,"kind":kind,"transport":transport,"mode_id":mode_id,"session_config":config}
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
        out["final_output"] = r.claude_result
        out["effective_model"] = r.model
        out["harness_kind"] = r.harness
        out["harness_profile_id"] = None
        out["failure"] = ({"code":"run_failed","phase":"execution","message":r.error_message} if r.error_message else None)
        if d is not None and r.harness == "acp":
            snap=d.get(RunHarnessSnapshot,r.id)
            out["harness_snapshot"] = {"revision_id":snap.revision_id,"manifest":snap.manifest,"session_config":snap.session_config,"agent_mode_id":snap.agent_mode_id,"tool_mode":snap.tool_mode,"verification":snap.verification,"observed":(snap.verification or {}).get("observed")} if snap else None
            if snap:
                hrev=d.get(HarnessProfileRevision,snap.revision_id)
                out["harness_profile_id"] = hrev.profile_id if hrev else None
                out["effective_model"] = (snap.verification or {}).get("effective_model") or "agent-default"
                if (snap.verification or {}).get("failure"):
                    out["failure"] = snap.verification["failure"]
        if r.harness == "opencode":
            # The persistence columns are retained for compatibility with
            # existing SQLite databases; zero is an internal sentinel, not an
            # advertised OpenCode limit.
            out["max_turns"],out["max_budget_usd"] = None,None
        now_value=datetime.now(timezone.utc)
        start=r.started_at or r.created_at; end=r.finished_at or now_value
        if start and start.tzinfo is None: start=start.replace(tzinfo=timezone.utc)
        if end and end.tzinfo is None: end=end.replace(tzinfo=timezone.utc)
        out["elapsed_seconds"]=max(0.0,(end-start).total_seconds()) if start and end else 0.0
        out["queue_position"]=None
        if d is not None and r.status == "queued":
            out["queue_position"]=d.query(Run).filter(Run.status == "queued", Run.created_at < r.created_at).count()+1
        out["trace_available"] = bool(d is not None and d.get(RunTrace, r.id))
        if d is not None:
            revision = d.get(McpProfileRevision, r.profile_revision_id)
            server = (revision.mcp_json.get("mcpServers", {}).get(r.enabled_server) if revision else None) or {}
            out["transport"] = transport_for_server(server)
        return _api_projection(out, path="$.run")
    @router.post("/runs", status_code=202)
    def create_run(body:RunCreate,d:Session=Depends(db_dep)):
        if body.model not in settings.models_for(body.harness): raise HTTPException(422,"Model is not configured for this harness")
        if body.harness == "acp" and not body.harness_revision_id: raise HTTPException(422,"harness_revision_id is required")
        if body.harness == "acp" and (body.tool_mode != "agent_default" or body.model != "agent-default"): raise HTTPException(422,"ACP requires agent-default model and tool mode")
        if body.harness != "acp" and body.tool_mode == "agent_default": raise HTTPException(422,"agent_default is only valid for ACP")
        rev=d.get(McpProfileRevision,body.profile_revision_id)
        if not rev: raise HTTPException(404,"Profile revision not found")
        p=d.get(McpProfile,rev.profile_id)
        if p.archived: raise HTTPException(422,"Profile is archived")
        try: selected_server_config(rev.mcp_json,body.enabled_server)
        except ProfileValidationError as e: raise HTTPException(422,str(e))
        max_turns=settings.claude_max_turns if body.harness == "claude-code" else 0
        max_budget=settings.claude_max_budget_usd if body.harness == "claude-code" else 0
        hrev = d.get(HarnessProfileRevision, body.harness_revision_id) if body.harness == "acp" else None
        if body.harness == "acp" and (not hrev or not hrev.trusted_unsandboxed): raise HTTPException(422,"Trusted unsandboxed acknowledgment is required")
        if body.harness == "acp" and (not hrev.profile or hrev.profile.archived): raise HTTPException(422,"Harness profile is archived")
        if body.harness == "acp": _validate_acp_dimensions(d, hrev, mode_id=body.agent_mode_id, session_config=body.session_config)
        r=Run(profile_revision_id=rev.id,enabled_server=body.enabled_server,harness=body.harness,model=body.model,tool_mode=body.tool_mode,prompt=body.prompt,expected_output=body.expected_output,timeout_seconds=settings.run_timeout_seconds,max_turns=max_turns,max_budget_usd=max_budget)
        d.add(r); d.flush()
        if body.harness == "acp":
            transport = transport_for_server((rev.mcp_json.get("mcpServers", {}).get(body.enabled_server) or {}))
            verification = _verification_for(d, hrev, transport=transport, mode_id=body.agent_mode_id, session_config=body.session_config)
            d.add(RunHarnessSnapshot(run_id=r.id,revision_id=hrev.id,manifest=hrev.manifest,session_config=body.session_config,agent_mode_id=body.agent_mode_id,tool_mode=body.tool_mode,verification=verification))
        d.commit(); d.refresh(r); manager.submit(r.id); return run_json(r,d)
    @router.get("/runs")
    def runs(status_filter:str|None=Query(None, alias="status"), profile_id:str|None=None, model:str|None=None, harness:str|None=None, harness_kind:str|None=None, harness_profile_id:str|None=None, harness_profile:str|None=None, limit:int=Query(100,ge=1,le=500), offset:int=Query(0,ge=0), d:Session=Depends(db_dep)):
        q=d.query(Run)
        if status_filter:q=q.filter_by(status=status_filter)
        if profile_id:
            revision_ids=[x[0] for x in d.query(McpProfileRevision.id).filter_by(profile_id=profile_id).all()]
            q=q.filter(Run.profile_revision_id.in_(revision_ids or ["__none__"]))
        if model: q=q.filter_by(model=model)
        if harness not in {None, "claude-code", "opencode", "acp"}:
            raise HTTPException(422,"Unknown harness filter")
        if harness_kind not in {None, "builtin", "acp"}:
            raise HTTPException(422,"harness_kind must be builtin or acp")
        if harness and harness_kind and ((harness_kind == "acp") != (harness == "acp")):
            raise HTTPException(422,"harness and harness_kind filters conflict")
        if harness:
            q=q.filter_by(harness=harness)
        elif harness_kind == "acp":
            q=q.filter_by(harness="acp")
        elif harness_kind == "builtin":
            q=q.filter(Run.harness.in_(["claude-code", "opencode"]))
        if harness_profile_id or harness_profile:
            harness_profile_id = harness_profile_id or harness_profile
            if harness_kind == "builtin" or harness in {"claude-code", "opencode"}:
                raise HTTPException(422,"harness profile filters apply only to ACP runs")
            revision_ids=[x[0] for x in d.query(HarnessProfileRevision.id).filter_by(profile_id=harness_profile_id).all()]
            snapshot_run_ids=[x[0] for x in d.query(RunHarnessSnapshot.run_id).filter(RunHarnessSnapshot.revision_id.in_(revision_ids or ["__none__"])).all()]
            q=q.filter(Run.id.in_(snapshot_run_ids or ["__none__"]))
        return [run_json(r,d) for r in q.order_by(desc(Run.created_at)).offset(offset).limit(limit).all()]
    @router.get("/runs/{run_id}")
    def get_run(run_id:str,d:Session=Depends(db_dep)):
        r=d.get(Run,run_id)
        if not r: raise HTTPException(404,"Run not found")
        return run_json(r,d)
    @router.get("/runs/{run_id}/events")
    def events(run_id:str,d:Session=Depends(db_dep)):
        if not d.get(Run,run_id): raise HTTPException(404,"Run not found")
        value = [{"sequence":e.sequence,"timestamp":_iso(e.timestamp),"event_type":e.event_type,"payload":e.payload,"raw_event":e.raw_event} for e in d.query(RunEvent).filter_by(run_id=run_id).order_by(RunEvent.sequence).all()]
        return _api_projection(value, path="$.events")
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
        harness=o.harness or old.harness; model=o.model or old.model
        if model not in settings.models_for(harness): raise HTTPException(422,"Model is not configured for this harness")
        if harness == "acp" and (model != "agent-default" or (o.tool_mode is not None and o.tool_mode != "agent_default") or (o.harness is not None and o.harness != "acp")):
            raise HTTPException(422,"ACP requires agent-default model and tool mode")
        if harness != "acp" and (o.tool_mode == "agent_default"):
            raise HTTPException(422,"agent_default is only valid for ACP")
        if harness != "acp" and o.harness_revision_id:
            raise HTTPException(422,"harness_revision_id is only valid for ACP clones")
        destination_tool_mode = (
            o.tool_mode if o.tool_mode is not None else
            ("agent_default" if harness == "acp" else (old.tool_mode if old.tool_mode != "agent_default" else "mcp_only"))
        )
        if harness == "acp" and destination_tool_mode != "agent_default":
            raise HTTPException(422,"ACP requires agent-default tool mode")
        if harness != "acp" and destination_tool_mode not in {"mcp_only", "mcp_read_only", "full"}:
            raise HTTPException(422,"Invalid tool mode for destination harness")
        prompt=o.prompt if o.prompt is not None else old.prompt; expected=o.expected_output if o.expected_output is not None else old.expected_output
        if not prompt.strip() or not expected.strip(): raise HTTPException(422,"Prompt and expected output must be nonblank")
        max_turns=settings.claude_max_turns if harness == "claude-code" else 0
        max_budget=settings.claude_max_budget_usd if harness == "claude-code" else 0
        r=Run(parent_run_id=old.id,profile_revision_id=rev.id,enabled_server=o.enabled_server or old.enabled_server,harness=harness,model=model,tool_mode=destination_tool_mode,prompt=prompt,expected_output=expected,timeout_seconds=settings.run_timeout_seconds,max_turns=max_turns,max_budget_usd=max_budget)
        try:selected_server_config(rev.mcp_json,r.enabled_server)
        except ProfileValidationError as e: raise HTTPException(422,str(e))
        d.add(r); d.flush()
        if harness == "acp":
            import copy
            old_snapshot=d.get(RunHarnessSnapshot,old.id)
            if not old_snapshot and not o.harness_revision_id and not o.use_latest_harness_revision:
                raise HTTPException(422,"ACP harness snapshot unavailable or untrusted")
            selected_harness_revision=o.harness_revision_id if o.harness_revision_id else (old_snapshot.revision_id if old_snapshot else None)
            if o.use_latest_harness_revision and selected_harness_revision:
                previous=d.get(HarnessProfileRevision,selected_harness_revision); profile=d.get(HarnessProfile,previous.profile_id) if previous else None
                selected_harness_revision=profile.current_revision_id if profile else selected_harness_revision
            selected=d.get(HarnessProfileRevision,selected_harness_revision) if selected_harness_revision else None
            if not selected or not selected.trusted_unsandboxed or (selected.profile and selected.profile.archived): raise HTTPException(422,"ACP harness snapshot unavailable or untrusted")
            # Explicit ACP cross-harness/revision/dimension changes must use
            # currently advertised protocol dimensions.  An unchanged clone
            # retains its historical snapshot exactly for compatibility.
            unchanged_snapshot = bool(old_snapshot and o.harness is None and o.harness_revision_id is None and o.profile_revision_id is None and o.enabled_server is None and not o.use_latest_harness_revision and not o.use_latest_revision and o.agent_mode_id is None and o.session_config is None and o.transport is None and o.tool_mode is None)
            if not unchanged_snapshot:
                next_config=o.session_config if o.session_config is not None else (old_snapshot.session_config if old_snapshot else {})
                next_mode=o.agent_mode_id if o.agent_mode_id is not None else (old_snapshot.agent_mode_id if old_snapshot else None)
                _validate_acp_dimensions(d, selected, mode_id=next_mode, session_config=next_config)
            if unchanged_snapshot:
                # Keep the snapshot byte-for-byte at the API level.  In
                # particular, verification provenance is historical evidence,
                # not something to replace with a fresh trusted flag.
                snapshot_kwargs={"revision_id":old_snapshot.revision_id,"manifest":copy.deepcopy(old_snapshot.manifest),"session_config":copy.deepcopy(old_snapshot.session_config),"agent_mode_id":old_snapshot.agent_mode_id,"tool_mode":old_snapshot.tool_mode,"verification":copy.deepcopy(old_snapshot.verification)}
            else:
                session_config=o.session_config if o.session_config is not None else (old_snapshot.session_config if old_snapshot else {})
                mode_id=o.agent_mode_id if o.agent_mode_id is not None else (old_snapshot.agent_mode_id if old_snapshot else None)
                configured_transport=transport_for_server((rev.mcp_json.get("mcpServers", {}).get(r.enabled_server) or {}))
                if o.transport is not None and o.transport != configured_transport:
                    raise HTTPException(422,"transport override does not match selected MCP server")
                snapshot_kwargs={"revision_id":selected.id,"manifest":copy.deepcopy(selected.manifest),"session_config":copy.deepcopy(session_config),"agent_mode_id":mode_id,"tool_mode":r.tool_mode,"verification":_verification_for(d,selected,transport=o.transport or configured_transport,mode_id=mode_id,session_config=session_config)}
            d.add(RunHarnessSnapshot(run_id=r.id,**snapshot_kwargs))
        d.commit();d.refresh(r);manager.submit(r.id);return run_json(r,d)
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
        d.query(RunEvent).filter_by(run_id=run_id).delete(synchronize_session=False); d.query(RunTrace).filter_by(run_id=run_id).delete(synchronize_session=False); d.query(RunHarnessSnapshot).filter_by(run_id=run_id).delete(synchronize_session=False); d.delete(r); d.commit(); return Response(status_code=204)
    @router.delete("/runs", status_code=204)
    def clear_runs(confirm:bool=False, body:dict|None=Body(None), d:Session=Depends(db_dep)):
        confirm = confirm or bool(body and body.get("confirm"))
        if not confirm: raise HTTPException(400,"Pass confirm=true to clear history")
        active=d.query(Run).filter(Run.status.in_(["queued", "running"])).count()
        if active: raise HTTPException(409,"Active runs must be cancelled and finished before clearing history")
        for r in d.query(Run).all():
            d.query(RunEvent).filter_by(run_id=r.id).delete(synchronize_session=False); d.query(RunTrace).filter_by(run_id=r.id).delete(synchronize_session=False); d.query(RunHarnessSnapshot).filter_by(run_id=r.id).delete(synchronize_session=False); d.delete(r)
        d.commit(); return Response(status_code=204)
    @router.get("/runs/{run_id}/report")
    def report(run_id:str,d:Session=Depends(db_dep)):
        r=d.get(Run,run_id)
        if not r: raise HTTPException(404,"Run not found")
        rev=d.get(McpProfileRevision,r.profile_revision_id); ev=d.query(RunEvent).filter_by(run_id=run_id).order_by(RunEvent.sequence).all()
        from mcp_pal_app.domain.events import derive_mcp_summary
        stored_trace=d.get(RunTrace, run_id)
        trace_value = stored_trace.trace if stored_trace else None
        transport = transport_for_server(((rev.mcp_json.get("mcpServers", {}).get(r.enabled_server) if rev else None) or {}))
        mcp_summary=derive_mcp_summary([e.raw_event for e in ev],r.enabled_server); mcp_summary["transport"]=transport
        safe_profile = _api_projection(rev.mcp_json, path="$.profile_revision.mcp_json")
        # A missing trace means this is an older run captured before trace
        # persistence was introduced; keep its legacy marker.  Newly built
        # traces carry their v2 schema from the builder and stored traces keep
        # the schema they were created with.
        trace_schema = stored_trace.schema_version if stored_trace else ("claude.v1" if r.harness == "claude-code" else None)
        unavailable_reason = "Trace unavailable for legacy run." if r.harness == "claude-code" else f"Trace parsing is not implemented for {r.harness}."
        report_value = {"run":run_json(r,d),"profile_revision":{"id":rev.id,"revision_number":rev.revision_number,"mcp_json":safe_profile},"assertions":{"lifecycle":r.status,"mcp":{"status":r.mcp_assertion},"semantic":{"status":r.semantic_assertion,"reason":r.semantic_reason}},"events":[{"sequence":e.sequence,"timestamp":_iso(e.timestamp),"event_type":e.event_type,"payload":e.payload,"raw_event":e.raw_event} for e in ev],"stderr":r.stderr,"mcp_summary":mcp_summary,"high_risk":r.tool_mode == "full","warning":"Downloaded reports and persisted trace payloads redact detected credentials.","trace":{"available":stored_trace is not None,"schema":trace_schema,"harness":(trace_value or {}).get("harness",r.harness),"capture_status":stored_trace.capture_status if stored_trace else "unavailable","summary":(trace_value or {}).get("summary",{"transport":transport}),"mcp_calls_schema":(trace_value or {}).get("mcp_calls_schema") if trace_value else None,"mcp_calls":(trace_value or {}).get("mcp_calls",[]),"spans":(trace_value or {}).get("spans",[]),"protocol_events":(trace_value or {}).get("protocol_events",[]),"acp_protocol_events":(trace_value or {}).get("acp_protocol_events",(trace_value or {}).get("protocol_events",[])),"mcp_protocol_events":(trace_value or {}).get("mcp_protocol_events",[]),"result_metadata":(trace_value or {}).get("result_metadata",{}),"limitations":(trace_value or {}).get("limitations",[unavailable_reason])}}
        return _api_projection(report_value, path="$.report")
    app.include_router(router)
    return app

app=create_app()
