"""Agent workflow router — spawn and manage Claude Code processes."""

import asyncio
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

try:
    import yaml
except ImportError:
    # Fallback YAML parser using JSON if PyYAML not available
    class yaml:
        @staticmethod
        def safe_load(content):
            # Very basic YAML to JSON conversion for simple cases
            import json
            import re
            # This is a very basic fallback - only handles simple key-value pairs
            lines = content.strip().split('\n')
            data = {}
            current_key = None
            current_value = []
            in_multiline = False

            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue

                if ':' in line and not in_multiline:
                    if current_key and current_value:
                        data[current_key] = '\n'.join(current_value).strip()
                        current_value = []

                    key, value = line.split(':', 1)
                    current_key = key.strip()
                    value = value.strip()

                    if value == '|':
                        in_multiline = True
                    elif value:
                        data[current_key] = value
                        current_key = None
                elif in_multiline and line.startswith('  '):
                    current_value.append(line[2:])  # Remove 2-space indent
                elif not line.startswith(' '):
                    in_multiline = False
                    if current_key and current_value:
                        data[current_key] = '\n'.join(current_value).strip()
                        current_value = []
                        current_key = None

            if current_key and current_value:
                data[current_key] = '\n'.join(current_value).strip()

            return data
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, validator

from web.backend.models import _VALID_EXCHANGES

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
    model: str = "claude-sonnet-4-20250514"
    max_budget_usd: float = 5.0

    @validator("exchange")
    def validate_exchange(cls, v: str) -> str:
        if v not in _VALID_EXCHANGES:
            raise ValueError(f"exchange must be one of {_VALID_EXCHANGES}")
        return v

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

class AgentJobManager:
    def __init__(self):
        self.jobs: Dict[str, Dict] = {}

    def create_job(self, request: AgentRunRequest) -> str:
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {
            "id": job_id,
            "status": "pending",
            "request": request,
            "process": None,
            "events": [],
            "started_at": None,
        }
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict]:
        return self.jobs.get(job_id)

    def update_job(self, job_id: str, **updates):
        if job_id in self.jobs:
            self.jobs[job_id].update(updates)

agent_manager = AgentJobManager()

# ─── Event parsing ───────────────────────────────────────────────────────────

def parse_claude_event(line: str) -> Optional[AgentEvent]:
    """Parse a single line of Claude Code stream-json output into an AgentEvent."""
    try:
        data = json.loads(line.strip())
    except (json.JSONDecodeError, AttributeError):
        return None

    timestamp = str(time.time())

    # Handle assistant messages (thinking + tool_use)
    if data.get("type") == "assistant":
        content_items = data.get("message", {}).get("content", [])
        for item in content_items:
            if item.get("type") == "text":
                # Thinking text
                text = item.get("text", "").strip()
                if text:
                    return AgentEvent(
                        type="thinking",
                        content=text,
                        timestamp=timestamp
                    )
            elif item.get("type") == "tool_use":
                # Tool call
                tool_name = item.get("name", "")
                tool_input = item.get("input", {})

                # Extract file path for file operations
                file_path = None
                if "file_path" in tool_input:
                    file_path = tool_input["file_path"]

                # Format tool input as readable text
                if tool_name == "Bash":
                    content = f"$ {tool_input.get('command', '')}"
                elif tool_name in ["Read", "Write", "Edit"]:
                    content = f"{file_path or ''}"
                    if tool_name == "Edit" and "old_string" in tool_input and "new_string" in tool_input:
                        diff = {
                            "old": tool_input["old_string"],
                            "new": tool_input["new_string"]
                        }
                        return AgentEvent(
                            type="tool_call",
                            tool_name=tool_name,
                            content=content,
                            file_path=file_path,
                            diff=diff,
                            timestamp=timestamp
                        )
                else:
                    content = json.dumps(tool_input, indent=2)

                return AgentEvent(
                    type="tool_call",
                    tool_name=tool_name,
                    content=content,
                    file_path=file_path,
                    timestamp=timestamp
                )

    # Handle tool results
    elif data.get("type") == "result" and data.get("subtype") == "tool_result":
        content = data.get("content", "")
        return AgentEvent(
            type="tool_result",
            content=str(content),
            timestamp=timestamp
        )

    # Handle errors
    elif data.get("type") == "error":
        return AgentEvent(
            type="error",
            content=data.get("message", "Unknown error"),
            timestamp=timestamp
        )

    return None

# ─── Background process management ────────────────────────────────────────────

async def run_claude_agent(job_id: str, request: AgentRunRequest):
    """Run Claude Code agent as a subprocess and stream events."""
    job = agent_manager.get_job(job_id)
    if not job:
        return

    try:
        # Load skill configuration
        skill_dir = Path.home() / ".openclaw" / "skills" / request.skill_path
        workflow_file = skill_dir / "workflow.yaml"

        if not workflow_file.exists():
            agent_manager.update_job(job_id, status="failed", error=f"workflow.yaml not found in {skill_dir}")
            return

        with open(workflow_file) as f:
            workflow = yaml.safe_load(f)

        # Build prompt from template
        prompt_template = workflow.get("prompt_template", "")
        strategy_path = ""
        if request.strategy:
            strategy_path = f"quantforge/pine/strategies/{request.strategy}"

        # Format prompt with variables
        prompt = prompt_template.format(
            skill_path=skill_dir,
            strategy_path=strategy_path,
            exchange=request.exchange,
            symbol=request.symbol or "BTC/USDT:USDT",
            timeframe=request.timeframe,
            max_iterations=request.max_iterations,
        )

        # Build Claude Code command
        cmd = [
            "claude",
            "--permission-mode", "bypassPermissions",
            "--output-format", "stream-json",
            "--model", request.model,
            "--prompt", prompt
        ]

        agent_manager.update_job(job_id, status="running", started_at=str(time.time()))

        # Start Claude Code process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd="/home/pzheng46/QuantForge"
        )

        agent_manager.update_job(job_id, process=process)

        # Stream output
        while True:
            line = await process.stdout.readline()
            if not line:
                break

            line_str = line.decode('utf-8').strip()
            if not line_str:
                continue

            # Parse event
            event = parse_claude_event(line_str)
            if event:
                job = agent_manager.get_job(job_id)
                if job:
                    job["events"].append(event.dict())

        # Wait for completion
        await process.wait()

        if process.returncode == 0:
            agent_manager.update_job(job_id, status="completed")
            # Add completion event
            job = agent_manager.get_job(job_id)
            if job:
                done_event = AgentEvent(
                    type="done",
                    content="Agent workflow completed successfully",
                    timestamp=str(time.time())
                )
                job["events"].append(done_event.dict())
        else:
            stderr_output = await process.stderr.read()
            error_msg = stderr_output.decode('utf-8') if stderr_output else f"Process failed with code {process.returncode}"
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
                            metrics.append(AgentMetric(
                                name=metric_data["name"],
                                pattern=metric_data["pattern"],
                                higher_is_better=metric_data.get("higher_is_better"),
                                primary=metric_data.get("primary", False)
                            ))

                        skills.append(AgentSkillInfo(
                            name=skill_dir.name,
                            description=workflow.get("description", ""),
                            defaults=workflow.get("defaults", {}),
                            metrics=metrics
                        ))
                    except Exception as e:
                        # Skip invalid workflow files
                        continue

    return skills

@router.post("/agent/run", response_model=AgentJobStatus)
async def run_agent(request: AgentRunRequest):
    """Start a Claude Code agent job."""
    job_id = agent_manager.create_job(request)

    # Start background task
    asyncio.create_task(run_claude_agent(job_id, request))

    return AgentJobStatus(
        job_id=job_id,
        status="pending",
        events_count=0
    )

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
        error=job.get("error")
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