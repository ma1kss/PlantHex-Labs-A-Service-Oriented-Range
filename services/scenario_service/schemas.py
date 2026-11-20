from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


# Outputs

class ChallengeFull(BaseModel):
    id:             str
    title:          str
    description:    str
    category:       str
    challenge_type: str
    difficulty:     str
    points:         int
    flag_hash:      str
    file_name:      str
    author:         str
    max_attempts:   int
    tags:           list[str]
    created_at:     datetime
    visible:        bool                = True
    is_docker:      bool                = False
    is_lab:         bool                = False
    lab_services:   int                 = 0
    docker:         "DockerSpecOut | None" = None


class ChallengePublic(BaseModel):
    id:             str
    title:          str
    description:    str
    category:       str
    challenge_type: str
    difficulty:     str
    points:         int
    file_name:      str
    tags:           list[str]
    max_attempts:   int
    is_docker:      bool      = False
    is_lab:         bool      = False
    lab_services:   int       = 0
    docker_ports:   list[str] = []


class VisibilityRequest(BaseModel):
    visible: bool


class SubmissionResult(BaseModel):
    correct: bool
    message: str


class FlagOut(BaseModel):
    """Admin-only retrieval of a challenge's plaintext flag.

    `recoverable` is False for challenges created before plaintext flags were
    stored (only the one-way hash exists for those — the value cannot be shown).
    """
    challenge_id: str
    flag:         str
    recoverable:  bool


# Challenge play statistics (dashboards)

class ChallengeStatRow(BaseModel):
    challenge_id:    str
    title:           str
    difficulty:      str
    points:          int
    attempts:        int    # total submissions across all players
    attempters:      int    # distinct players who tried
    solvers:         int    # distinct players who solved (first or repeat)
    completion_rate: float  # solvers / attempters, 0..1


class ChallengeStatsOut(BaseModel):
    total_challenges:  int
    total_submissions: int
    total_solves:      int
    challenges:        list[ChallengeStatRow]   # most-played first


# Docker challenges

class DockerSpecOut(BaseModel):
    """Build metadata + runtime config for a docker challenge.

    Exposed only to admins / instructors via the full challenge listing.
    """
    image_tag:                str
    base_image:               str
    dockerfile_hash:          str
    exposed_ports:            list[str]
    cpu_limit:                float
    memory_limit:             str
    pids_limit:               int
    session_ttl_minutes:      int
    max_instances_per_player: int


class DockerSessionOut(BaseModel):
    id:             str
    challenge_id:   str
    user_id:        str
    username:       str
    status:         str           # creating | running | stopped | error
    container_id:   str
    container_name: str
    hostname:       str
    ports:          dict[str, str]   # "22/tcp" -> "32145"  (for labs: "51820/udp" -> port)
    access_urls:    list[str]
    expires_at:     datetime
    created_at:     datetime
    stopped_at:     datetime | None = None
    error_message:  str = ""
    # Lab fields (kind=="lab"): players reach the topology over WireGuard.
    kind:           str = "single"   # single | lab
    subnet:         str = ""         # 10.10.X.0/24 for labs
    # Per-service endpoints inside the lab subnet — each container's own IP and
    # exposed ports, e.g. [{"name":"web","ip":"10.10.1.10","ports":["80/tcp"]}].
    lab_endpoints:  list[dict] = []


class DockerStatusOut(BaseModel):
    """Admin/instructor view of every SOR-managed session container.

    Sourced live from the Docker daemon (filtered by sor.managed=true labels),
    so it reflects reality even if our DB rows drift.
    """
    daemon_reachable:    bool
    sessions:            list[dict]


# Inputs

class FlagSubmission(BaseModel):
    flag: str = Field(..., min_length=1, max_length=512)


# CTF events (time-boxed challenge sets)

class EventCreate(BaseModel):
    name:          str = Field(..., min_length=1, max_length=255)
    description:   str = ""
    starts_at:     datetime
    ends_at:       datetime
    challenge_ids: list[str] = []


class EventUpdate(BaseModel):
    """Partial update — omitted fields keep their current value.
    `challenge_ids`, when present, replaces the whole membership set."""
    name:          str | None = Field(None, min_length=1, max_length=255)
    description:   str | None = None
    starts_at:     datetime | None = None
    ends_at:       datetime | None = None
    challenge_ids: list[str] | None = None


class EventOut(BaseModel):
    id:              str
    name:            str
    description:     str
    starts_at:       datetime
    ends_at:         datetime
    status:          str            # upcoming | active | ended
    author:          str
    created_at:      datetime
    challenge_count: int
    challenge_ids:   list[str] = [] # staff only; empty for players


class EventDetailOut(EventOut):
    # Whether the caller may play the event's challenges right now (staff:
    # always; players: only while the event is active). When False for a
    # player, `challenges` is empty.
    accessible: bool = False
    # ChallengeFull for staff, ChallengePublic for players.
    challenges: list[ChallengeFull | ChallengePublic] = []
