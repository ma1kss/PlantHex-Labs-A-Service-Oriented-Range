from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class SolveOut(BaseModel):
    challenge_id: str
    difficulty:   str
    points:       int
    solved_at:    datetime


class MeOut(BaseModel):
    user_id:     str
    username:    str
    team:        str
    total_score: int
    solve_count: int
    rank:        int
    solves:      list[SolveOut]


class LeaderboardRow(BaseModel):
    rank:        int
    user_id:     str
    username:    str
    team:        str
    total_score: int
    solve_count: int


class ParticipantOut(BaseModel):
    user_id:     str
    username:    str
    team:        str
    total_score: int
    solve_count: int


class AssignTeamRequest(BaseModel):
    team: str = Field("", max_length=64)


class EnrolRequest(BaseModel):
    """Explicit enrolment — staff pre-register a participant (and optionally
    place them on a team) before they have solved anything."""
    user_id:  str = Field(..., min_length=1, max_length=36)
    username: str = Field("", max_length=64)
    team:     str = Field("", max_length=64)


class AttemptRequest(BaseModel):
    """Register/end a challenge attempt. Used by the Orchestration launch
    workflow. user_id defaults to the caller; staff may act on behalf."""
    challenge_id: str = Field(..., min_length=1, max_length=36)
    user_id:      str | None = Field(None, max_length=36)


# Teams (F07)

class TeamMember(BaseModel):
    user_id:     str
    username:    str
    total_score: int
    solve_count: int


class TeamOut(BaseModel):
    id:      str
    name:    str
    motto:   str
    members: list[TeamMember] = []


class TeamCreate(BaseModel):
    name:  str = Field(..., min_length=2, max_length=64)
    motto: str = Field("", max_length=255)


class TeamUpdate(BaseModel):
    motto: str = Field("", max_length=255)


class TeamLeaderboardRow(BaseModel):
    rank:         int
    team_id:      str
    team:         str
    motto:        str
    member_count: int
    total_score:  int
    solve_count:  int


class TeamSolveOut(BaseModel):
    challenge_id: str
    difficulty:   str
    points:       int
    solved_at:    datetime
    user_id:      str
    username:     str


class RevokeSolveRequest(BaseModel):
    """Revoke a solve for one player (user_id) or a whole team (team_id)."""
    challenge_id: str = Field(..., min_length=1, max_length=36)
    user_id:      str | None = Field(None, max_length=36)
    team_id:      str | None = Field(None, max_length=36)


class PointsPolicy(BaseModel):
    easy:    int
    medium:  int
    hard:    int
    default: int


class StatsOut(BaseModel):
    participants:  int
    total_solves:  int
    total_points:  int
    by_difficulty: dict[str, int]   # difficulty -> solve count
    points_policy: PointsPolicy


EXERCISE_ROLES = ("red", "blue", "white", "observer")


class EnrolmentIn(BaseModel):
    user_id:  str = Field(..., min_length=1, max_length=36)
    username: str = Field("", max_length=64)
    team:     str = Field("", max_length=64)
    role:     str = Field("blue", pattern="^(red|blue|white|observer)$")


class EnrolmentOut(BaseModel):
    event_id:    str
    user_id:     str
    username:    str
    team:        str
    role:        str
    enrolled_at: datetime


class ReconcileIn(BaseModel):
    valid_challenge_ids: list[str]


class ReconcileOut(BaseModel):
    removed_solves: int
    orphan_challenges: list[str]

