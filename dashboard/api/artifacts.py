"""Raw artifact content endpoint.

Per design doc § Presentation plane § URL topology — ``GET
/api/artifacts/{job_slug}/{path:path}`` returns the bytes of a file that
lives under the job's directory in the hammock root. Used by the artifact
viewer (markdown / yaml / json render) and inline embeds.

Security: the requested path is resolved relative to the job dir and must
stay inside it. ``..``-style traversal returns 400. Files outside the job
dir return 404 (we don't reveal whether such a path exists elsewhere).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response

from shared import paths

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


# Mapping from suffix to a content-type; everything else is served as plain
# text (UTF-8). Binary files are out of scope for the v0 artifact viewer.
_CONTENT_TYPES: dict[str, str] = {
    ".md": "text/markdown; charset=utf-8",
    ".yaml": "application/yaml; charset=utf-8",
    ".yml": "application/yaml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".jsonl": "application/x-ndjson; charset=utf-8",
}


def _resolve_artifact(root: Path, job_slug: str, rel: str) -> Path:
    job_root = paths.job_dir(job_slug, root=root).resolve()
    candidate = (job_root / rel).resolve()
    try:
        candidate.relative_to(job_root)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="path escapes job directory") from e
    return candidate


@router.get("/{job_slug}/{file_path:path}", response_class=PlainTextResponse)
async def get_artifact(request: Request, job_slug: str, file_path: str) -> Response:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    if cache.get_job(job_slug) is None:
        raise HTTPException(status_code=404, detail=f"job {job_slug!r} not found")
    if not file_path:
        raise HTTPException(status_code=400, detail="file path required")

    full = _resolve_artifact(cache.root, job_slug, file_path)
    if not full.is_file():
        raise HTTPException(status_code=404, detail=f"artifact {file_path!r} not found")

    suffix = full.suffix.lower()
    content_type = _CONTENT_TYPES.get(suffix, "text/plain; charset=utf-8")
    body = full.read_bytes()
    return Response(content=body, media_type=content_type)
