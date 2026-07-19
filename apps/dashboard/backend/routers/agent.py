"""Agent workflow router — spawn and manage coding-agent processes."""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    # Fallback YAML parser using JSON if PyYAML not available
    class yaml:
        @staticmethod
        def safe_load(content):
            # Very basic YAML to JSON conversion for simple cases
            # This is a very basic fallback - only handles simple key-value pairs
            lines = content.strip().split("\n")
            data = {}
            current_key = None
            current_value = []
            in_multiline = False

            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if ":" in line and not in_multiline:
                    if current_key and current_value:
                        data[current_key] = "\n".join(current_value).strip()
                        current_value = []

                    key, value = line.split(":", 1)
                    current_key = key.strip()
                    value = value.strip()

                    if value == "|":
                        in_multiline = True
                    elif value:
                        data[current_key] = value
                        current_key = None
                elif in_multiline and line.startswith("  "):
                    current_value.append(line[2:])  # Remove 2-space indent
                elif not line.startswith(" "):
                    in_multiline = False
                    if current_key and current_value:
                        data[current_key] = "\n".join(current_value).strip()
                        current_value = []
                        current_key = None

            if current_key and current_value:
                data[current_key] = "\n".join(current_value).strip()

            return data


from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, validator

from quantforge.agent_providers import build_agent_command, resolve_model
from apps.dashboard.backend.models import _VALID_EXCHANGES

router = APIRouter()

# ─── Agent models ────────────────────────────────────────────────────────────


class AgentRunRequest(BaseModel):
    skill_path: str  # e.g., "quantforge-optimizer"
    strategy: Optional[str] = None
    pine_source: Optional[str] = None
    exchange: str = "bitget"
    symbol: Optional[str] = None
    timeframe: str = "1h"
    max_iterations: int = 5
    agent_provider: str = "claude"
    model: Optional[str] = None
    max_budget_usd: float = 5.0

    @validator("exchange")
    def validate_exchange(cls, v: str) -> str:
        if v not in _VALID_EXCHANGES:
            raise ValueError(f"exchange must be one of {_VALID_EXCHANGES}")
        return v

    @validator("agent_provider")
    def validate_agent_provider(cls, v: str) -> str:
        from quantforge.agent_providers import normalize_provider

        return normalize_provider(v)

    @validator("skill_path")
    def validate_skill_path(cls, v: str) -> str:
        skill_dir = Path.home() / ".openclaw" / "skills" / v
        if not skill_dir.exists():
            raise ValueError(f"Skill not found: {v}")
        return v


class AgentEvent(BaseModel):
    type: str  # 'thinking' | 'tool_call' | 'tool_result' | 'error' | 'done'
    tool_name: Optional[str] = None  # 'Read' | 'Edit' | 'Write' | 'Bash' | etc
    content: str = ""  # text content or tool input/output
    file_path: Optional[str] = None  # for Read/Edit/Write
    diff: Optional[Dict[str, str]] = None  # for Edit: {old: str, new: str}
    duration_ms: Optional[int] = None  # for tool calls
    timestamp: str = ""


class AgentJobStatus(BaseModel):
    job_id: str
    status: str  # pending | running | completed | failed | cancelled
    started_at: Optional[str] = None
    events_count: int = 0
    error: Optional[str] = None


class AgentMetric(BaseModel):
    name: str
    pattern: str
    higher_is_better: Optional[bool]
    primary: bool = False


class AgentSkillInfo(BaseModel):
    name: str
    description: str
    defaults: Dict[str, Any]
    metrics: List[AgentMetric]


# ─── Agent job manager ───────────────────────────────────────────────────────

_PERSIST_DIR = Path.home() / ".quantforge" / "dashboard" / "agent_jobs"


class AgentJobManager:
    """Manages agent job state with file-based persistence.

    Jobs are snapshotted to ``~/.quantforge/dashboard/agent_jobs/{id}.json`` on
    every mutation so that they survive backend reloads (uvicorn --reload) and
    restarts. The subprocess handle itself cannot be persisted; on reload, any
    jobs whose status was running/pending are marked as ``failed`` with an
    interrupted note so the user knows to re-run.
    """

    def __init__(self, persist_dir: Path = _PERSIST_DIR):
        self.jobs: Dict[str, Dict] = {}
        self._persist_dir = persist_dir
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._load_persisted()

    def create_job(self, request: AgentRunRequest) -> str:
        # Only one job is kept at a time — wipe any prior state first so the
        # persisted history never grows beyond a single file.
        self._prune_all()
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {
            "id": job_id,
            "status": "pending",
            "request": request,
            "process": None,
            "events": [],
            "started_at": None,
        }
        self._persist(job_id)
        return job_id

    def _prune_all(self):
        """Drop every in-memory and on-disk job."""
        self.jobs.clear()
        if not self._persist_dir.exists():
            return
        for path in self._persist_dir.glob("*.json"):
            try:
                path.unlink()
            except OSError as e:
                print(f"[agent] failed to delete {path.name}: {e}")

    def get_job(self, job_id: str) -> Optional[Dict]:
        return self.jobs.get(job_id)

    def update_job(self, job_id: str, **updates):
        if job_id in self.jobs:
            self.jobs[job_id].update(updates)
            self._persist(job_id)

    def append_event(self, job_id: str, event_dict: Dict):
        """Append a single event and persist. Use this from streaming loops."""
        job = self.jobs.get(job_id)
        if job is None:
            return
        job["events"].append(event_dict)
        self._persist(job_id)

    def _serialize(self, job: Dict) -> Dict:
        request = job.get("request")
        if hasattr(request, "model_dump"):
            req_dict = request.model_dump()
        elif hasattr(request, "dict"):
            req_dict = request.dict()
        else:
            req_dict = dict(request) if request else None
        # Capture the subprocess PID (not the Popen handle, which doesn't
        # survive reload). On startup we'll SIGTERM it so the orphan doesn't
        # keep burning CPU after the backend reloaded.
        process = job.get("process")
        pid = getattr(process, "pid", None) if process is not None else job.get("pid")
        return {
            "id": job["id"],
            "status": job["status"],
            "request": req_dict,
            "events": job.get("events", []),
            "started_at": job.get("started_at"),
            "error": job.get("error"),
            "pid": pid,
        }

    def _persist(self, job_id: str):
        job = self.jobs.get(job_id)
        if not job:
            return
        path = self._persist_dir / f"{job_id}.json"
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(self._serialize(job)), encoding="utf-8")
            tmp.replace(path)  # atomic rename on POSIX
        except OSError as e:
            print(f"[agent] persist failed for {job_id}: {e}")

    def _load_persisted(self):
        """Load only the most recent job; delete any older leftover files."""
        if not self._persist_dir.exists():
            return
        files = sorted(
            self._persist_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return
        # Keep newest, delete the rest (leftovers from before single-job retention).
        latest, *stale = files
        for path in stale:
            try:
                path.unlink()
            except OSError as e:
                print(f"[agent] failed to delete stale {path.name}: {e}")
        try:
            data = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"[agent] failed to load {latest.name}: {e}")
            return
        job_id = data.get("id")
        if not job_id:
            return
        # Subprocess handle is lost across restarts — mark any still-active
        # job as failed-interrupted so the frontend stops polling, and
        # SIGTERM any orphan child PID we captured so it doesn't keep
        # burning CPU forever after backend reload.
        if data.get("status") in {"pending", "running"}:
            pid = data.get("pid")
            killed_note = ""
            if pid:
                try:
                    import os as _os
                    import signal as _signal

                    _os.kill(pid, _signal.SIGTERM)
                    killed_note = f" Sent SIGTERM to orphan PID {pid}."
                except ProcessLookupError:
                    killed_note = f" Orphan PID {pid} already exited."
                except OSError as e:
                    killed_note = f" Failed to SIGTERM orphan PID {pid}: {e}."
            prev = data.get("error") or ""
            data["status"] = "failed"
            data["error"] = (prev + "\n" if prev else "") + (
                "Backend restarted while job was running; subprocess output truncated."
                + killed_note
            )
        data["process"] = None
        self.jobs[job_id] = data
        if data["status"] == "failed" and "Backend restarted" in (
            data.get("error") or ""
        ):
            self._persist(job_id)


agent_manager = AgentJobManager()

# ─── Event parsing ───────────────────────────────────────────────────────────


def parse_claude_events(line: str) -> List[AgentEvent]:
    """Parse a single line of Claude Code stream-json output into AgentEvent(s).

    One CC message can contain multiple content items (thinking + tool_use),
    so we return a list.
    """
    try:
        data = json.loads(line.strip())
    except (json.JSONDecodeError, AttributeError):
        return []

    timestamp = str(time.time())
    events: List[AgentEvent] = []

    # Handle assistant messages (thinking + tool_use + text)
    if data.get("type") == "assistant":
        content_items = data.get("message", {}).get("content", [])
        for item in content_items:
            if item.get("type") == "thinking":
                text = item.get("thinking", "").strip()
                if text:
                    events.append(
                        AgentEvent(type="thinking", content=text, timestamp=timestamp)
                    )
            elif item.get("type") == "text":
                text = item.get("text", "").strip()
                if text:
                    events.append(
                        AgentEvent(type="thinking", content=text, timestamp=timestamp)
                    )
            elif item.get("type") == "tool_use":
                tool_name = item.get("name", "")
                tool_input = item.get("input", {})

                file_path = (
                    tool_input.get("file_path")
                    or tool_input.get("path")
                    or tool_input.get("file")
                )

                if tool_name == "Bash":
                    content = f"$ {tool_input.get('command', '')}"
                elif tool_name in ["Read", "Write", "Edit"]:
                    content = file_path or ""
                else:
                    content = json.dumps(tool_input, indent=2)[:500]

                diff = None
                if (
                    tool_name == "Edit"
                    and "old_string" in tool_input
                    and "new_string" in tool_input
                ):
                    diff = {
                        "old": tool_input["old_string"],
                        "new": tool_input["new_string"],
                    }

                events.append(
                    AgentEvent(
                        type="tool_call",
                        tool_name=tool_name,
                        content=content,
                        file_path=file_path,
                        diff=diff,
                        timestamp=timestamp,
                    )
                )

    # Handle tool results (multiple possible subtypes)
    elif data.get("type") == "result":
        subtype = data.get("subtype", "")
        if subtype == "tool_result":
            content = data.get("content", "")
            if isinstance(content, list):
                # Some tool results are arrays of content blocks
                content = "\n".join(
                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                    for c in content
                )
            events.append(
                AgentEvent(
                    type="tool_result", content=str(content)[:2000], timestamp=timestamp
                )
            )
        elif subtype == "success":
            # Final result
            result_text = data.get("result", "")
            cost = data.get("total_cost_usd", 0)
            events.append(
                AgentEvent(
                    type="done",
                    content=f"{result_text}\n\n💰 Cost: ${cost:.4f}",
                    timestamp=timestamp,
                )
            )

    # Handle errors
    elif data.get("type") == "error":
        events.append(
            AgentEvent(
                type="error",
                content=data.get("message", str(data.get("error", "Unknown error"))),
                timestamp=timestamp,
            )
        )

    return events


# ─── Background process management ────────────────────────────────────────────


async def run_coding_agent(job_id: str, request: AgentRunRequest):
    """Run a coding agent subprocess and stream events."""
    job = agent_manager.get_job(job_id)
    if not job:
        return

    try:
        # Load skill configuration
        skill_dir = Path.home() / ".openclaw" / "skills" / request.skill_path
        workflow_file = skill_dir / "workflow.yaml"

        if not workflow_file.exists():
            agent_manager.update_job(
                job_id, status="failed", error=f"workflow.yaml not found in {skill_dir}"
            )
            return

        with open(workflow_file) as f:
            workflow = yaml.safe_load(f)

        # Build prompt from template
        prompt_template = workflow.get("prompt_template", "")
        strategy_path = ""
        work_path = ""
        if request.strategy:
            strategy_name = request.strategy.removesuffix(".pine")
            strategy_path = f"quantforge/pine/strategies/{strategy_name}.pine"
            # Working copy in /tmp to avoid modifying original
            work_path = f"/tmp/optimize_{strategy_name}_{job_id[:8]}.pine"
            # Final output saved in optimized/ subdirectory with timestamp
            from datetime import datetime

            ts = datetime.now().strftime("%Y%m%d_%H%M")
            output_path = f"quantforge/pine/strategies/optimized/{strategy_name}_optimized_{ts}.pine"

        # Format prompt with variables
        prompt = prompt_template.format(
            skill_path=skill_dir,
            strategy_path=strategy_path,
            work_path=work_path,
            output_path=output_path,
            exchange=request.exchange,
            symbol=request.symbol or "BTC/USDT:USDT",
            timeframe=request.timeframe,
            max_iterations=request.max_iterations,
        )

        model = resolve_model(request.agent_provider, request.model)
        cmd = build_agent_command(
            request.agent_provider,
            model,
            project_dir="/home/pzheng46/QuantForge",
            max_turns=80,
        )

        agent_manager.update_job(job_id, status="running", started_at=str(time.time()))

        # Start Claude Code process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/home/pzheng46/QuantForge",
        )
        assert process.stdin is not None
        process.stdin.write(prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

        agent_manager.update_job(job_id, process=process)

        # Stream output
        while True:
            line = await process.stdout.readline()
            if not line:
                break

            line_str = line.decode("utf-8").strip()
            if not line_str:
                continue

            # Parse events (one CC line can produce multiple events)
            parsed = parse_claude_events(line_str)
            if parsed:
                for event in parsed:
                    agent_manager.append_event(job_id, event.dict())

        # Wait for completion
        await process.wait()

        if process.returncode == 0:
            agent_manager.update_job(job_id, status="completed")
            done_event = AgentEvent(
                type="done",
                content="Agent workflow completed successfully",
                timestamp=str(time.time()),
            )
            agent_manager.append_event(job_id, done_event.dict())
        else:
            stderr_output = await process.stderr.read()
            error_msg = (
                stderr_output.decode("utf-8")
                if stderr_output
                else f"Process failed with code {process.returncode}"
            )
            agent_manager.update_job(job_id, status="failed", error=error_msg)

    except Exception as e:
        agent_manager.update_job(job_id, status="failed", error=str(e))


# ─── API endpoints ────────────────────────────────────────────────────────────


@router.get("/agent/skills", response_model=List[AgentSkillInfo])
async def list_agent_skills():
    """List available skills by scanning for workflow.yaml files."""
    skills_dir = Path.home() / ".openclaw" / "skills"
    skills = []

    if skills_dir.exists():
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir():
                workflow_file = skill_dir / "workflow.yaml"
                if workflow_file.exists():
                    try:
                        with open(workflow_file) as f:
                            workflow = yaml.safe_load(f)

                        metrics = []
                        for metric_data in workflow.get("metrics", []):
                            metrics.append(
                                AgentMetric(
                                    name=metric_data["name"],
                                    pattern=metric_data["pattern"],
                                    higher_is_better=metric_data.get(
                                        "higher_is_better"
                                    ),
                                    primary=metric_data.get("primary", False),
                                )
                            )

                        skills.append(
                            AgentSkillInfo(
                                name=skill_dir.name,
                                description=workflow.get("description", ""),
                                defaults=workflow.get("defaults", {}),
                                metrics=metrics,
                            )
                        )
                    except Exception:
                        # Skip invalid workflow files
                        continue

    return skills


@router.post("/agent/run", response_model=AgentJobStatus)
async def run_agent(request: AgentRunRequest):
    """Start a coding-agent job."""
    job_id = agent_manager.create_job(request)

    # Start background task
    asyncio.create_task(run_coding_agent(job_id, request))

    return AgentJobStatus(job_id=job_id, status="pending", events_count=0)


@router.get("/agent/{job_id}", response_model=AgentJobStatus)
async def get_agent_status(job_id: str):
    """Get agent job status."""
    job = agent_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return AgentJobStatus(
        job_id=job_id,
        status=job["status"],
        started_at=job.get("started_at"),
        events_count=len(job.get("events", [])),
        error=job.get("error"),
    )


@router.post("/agent/{job_id}/stop")
async def stop_agent(job_id: str):
    """Kill Claude Code process."""
    job = agent_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    process = job.get("process")
    if process:
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    agent_manager.update_job(job_id, status="cancelled")
    return {"status": "cancelled"}


@router.websocket("/ws/agent/{job_id}")
async def agent_websocket(websocket: WebSocket, job_id: str):
    """Stream agent events to frontend."""
    await websocket.accept()

    job = agent_manager.get_job(job_id)
    if not job:
        await websocket.send_json({"error": "Job not found"})
        await websocket.close()
        return

    # Send existing events
    for event in job.get("events", []):
        try:
            await websocket.send_json(event)
        except WebSocketDisconnect:
            break

    # Stream new events
    last_event_count = len(job.get("events", []))

    try:
        while True:
            await asyncio.sleep(0.5)  # Poll every 500ms

            job = agent_manager.get_job(job_id)
            if not job:
                break

            current_events = job.get("events", [])

            # Send new events
            if len(current_events) > last_event_count:
                new_events = current_events[last_event_count:]
                for event in new_events:
                    await websocket.send_json(event)
                last_event_count = len(current_events)

            # Check if job is done
            if job["status"] in ["completed", "failed", "cancelled"]:
                break

    except WebSocketDisconnect:
        pass
