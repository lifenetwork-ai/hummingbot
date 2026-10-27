"""Current MM session state. Execution stays in the V2 runner and PG owner loop."""
import hashlib
import json
import math
import uuid
from decimal import Decimal
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from controllers.market_making.life_mm import ReferencePair
from hummingbot.client.config.mm_profiles import DurationMode, MarketMode, MMProfilesConfig
from hummingbot.core.control_errors import RequestError

MMSessionStatus = Literal["preparing", "running", "stopping", "stopped", "expired"]


def session_permits(state, *, session_id, generation, now):
    """Small final-gate projection; no catalogue parsing or exchange calls."""
    try:
        if not (state['status'] == 'running' and state['id'] == session_id and state['generation'] == generation
                and math.isfinite(now) and math.isfinite(state['started_at']) and state['started_at'] <= now):
            return False
        request = state.get('request') or {}
        mode = request.get('duration_mode', 'timed')
        end, step = state['expires_at'], state['step_expires_at']
        if mode == 'timed':
            return (end is not None and step is not None and math.isfinite(end) and math.isfinite(step)
                    and now < min(end, step))
        if (mode != 'until_stopped' or (state.get('catalogue') or {}).get('allow_until_stopped') is not True
                or end is not None or request.get('duration_seconds') is not None):
            return False
        if request.get('selection') == 'fixed':
            return step is None and request.get('step_duration_seconds') is None and bool(request.get('profile_id'))
        return (request.get('selection') == 'random' and step is not None and math.isfinite(step) and now < step
                and type(request.get('step_duration_seconds')) is int and request['step_duration_seconds'] > 0)
    except (KeyError, TypeError, ValueError):
        return False


class MMSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market_mode: MarketMode
    selection: Literal["fixed", "random"] = "fixed"
    duration_mode: DurationMode = 'timed'
    duration_seconds: Optional[int] = Field(default=None, gt=0, strict=True)
    profile_id: Optional[str] = None
    references: Optional[List[ReferencePair]] = None
    profile_ids: Optional[List[str]] = None
    step_duration_seconds: Optional[int] = Field(default=None, gt=0, strict=True)

    @model_validator(mode='after')
    def duration_matches_mode(self):
        if ((self.duration_mode == 'timed' and self.duration_seconds is None)
                or (self.duration_mode == 'until_stopped' and self.duration_seconds is not None)):
            raise ValueError('Timed sessions require a duration; until stopped must omit it')
        return self


class MMSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    request: MMSessionRequest
    catalogue: MMProfilesConfig
    config_hash: str
    profile_id: str
    generation: int = Field(default=0, ge=0, strict=True)
    step_index: int = Field(default=0, ge=0, strict=True)
    status: MMSessionStatus = "preparing"
    started_at: Optional[float] = None
    expires_at: Optional[float] = None
    step_expires_at: Optional[float] = None
    anchors: Dict[str, dict] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def restore_legacy_random_pool(cls, values):
        """Decode saved pre-profile-pool sessions without redrawing or resetting time.

        New YAML uses only profile_ids; this compatibility applies to archived
        session catalogues already persisted by the previous service version.
        """
        if not isinstance(values, dict) or not isinstance(values.get("catalogue"), dict):
            return values
        catalogue = values["catalogue"]
        policy = catalogue.get("random")
        if not isinstance(policy, dict) or "scenarios" not in policy or "profile_ids" in policy:
            return values
        scenarios, profiles = policy["scenarios"], catalogue.get("profiles", {})
        if (not isinstance(scenarios, list) or not scenarios or not isinstance(profiles, dict)
                or any(not isinstance(scenario, str) for scenario in scenarios)
                or len(set(scenarios)) != len(scenarios)):
            return values
        pool = [key for key, profile in profiles.items() if isinstance(profile, dict) and profile.get("scenario") in scenarios]
        if {profiles[key]["scenario"] for key in pool} != set(scenarios):
            return values
        policy = {key: value for key, value in policy.items() if key != "scenarios"}
        return {**values, "catalogue": {**catalogue, "random": {**policy, "profile_ids": pool}}}

    @classmethod
    def create(cls, catalogue, request, *, rng, session_id=None):
        catalogue.validate_duration(request.duration_seconds, duration_mode=request.duration_mode)
        if request.selection == "fixed":
            if request.profile_id not in catalogue.profiles or request.profile_ids is not None or request.step_duration_seconds is not None:
                raise RequestError("Fixed selection requires one known profile and no random fields", code="selection_invalid", field="profile_id")
            profile_id = request.profile_id
        else:
            if request.references is not None or request.profile_id is not None:
                raise RequestError("Random selection uses configured profile pools and baskets", code="selection_invalid", field="profile_id")
            pool = list(catalogue.random.profile_ids) if request.profile_ids is None else request.profile_ids
            if not pool or len(pool) != len(set(pool)) or not set(pool) <= set(catalogue.random.profile_ids):
                raise ValueError("Random selection requires unique profile IDs from the configured random pool")
            # Store the effective pool and step once; a revision cannot replace them.
            request = request.model_copy(update={
                "profile_id": None, "profile_ids": list(pool),
                "step_duration_seconds": request.step_duration_seconds or catalogue.random.step_duration_seconds,
            })
            if request.step_duration_seconds > catalogue.max_duration_seconds:
                raise ValueError("Random step exceeds maximum duration")
            profile_id = cls._draw(catalogue, request, rng)
        # Static validation only. Runtime admission must also supply fresh instrument
        # membership, available collateral and jointly ready connectors.
        catalogue.controller_configs(profile_id, request.market_mode, None, references=request.references)
        digest = hashlib.sha256(json.dumps({"catalogue": catalogue.model_dump(mode="json"),
                                           "request": request.model_dump(mode="json")}, sort_keys=True,
                                           separators=(",", ":")).encode()).hexdigest()
        return cls(id=session_id or str(uuid.uuid4()), request=request.model_copy(deep=True), catalogue=catalogue.model_copy(deep=True),
                   config_hash=digest, profile_id=profile_id)

    @staticmethod
    def _draw(catalogue, request, rng):
        return rng.choice(sorted(request.profile_ids))

    def ready(self, *, now, anchors):
        if not math.isfinite(now) or (self.started_at is not None and now < self.started_at):
            raise ValueError("Session clock is invalid")
        if self.status == "running":
            # A retry/restart cannot recapture anchors or extend either deadline.
            return
        if self.status != "preparing" or (self.step_expires_at is not None and now >= self.step_expires_at):
            raise ValueError("Session step is no longer eligible for activation")
        configs = self.catalogue.controller_configs(self.profile_id, self.request.market_mode, None,
                                                    references=self.request.references)
        if set(anchors) != {config.id for config in configs}:
            raise ValueError("Every selected market requires an anchor")
        source_baskets = []
        for config in configs:
            values = anchors[config.id]
            life = Decimal(values["life"])
            sources = {pair: Decimal(value) for pair, value in values["sources"].items()}
            if not life.is_finite() or not config.min_price <= life <= config.max_price:
                raise ValueError("Invalid LIFE anchor")
            if (set(sources) != {ref.pair for ref in config.references}
                    or any(not value.is_finite() or value <= 0 for value in sources.values())):
                raise ValueError("Every reference requires a positive finite anchor")
            source_baskets.append(sources)
        if any(sources != source_baskets[0] for sources in source_baskets):
            raise ValueError("Both markets must share the same benchmark anchors")
        if self.started_at is None:
            self.started_at = now
            self.expires_at = now + self.request.duration_seconds if self.request.duration_mode == 'timed' else None
            if self.request.selection == 'random':
                step_end = now + self.request.step_duration_seconds
                self.step_expires_at = min(self.expires_at, step_end) if self.expires_at is not None else step_end
            else:
                self.step_expires_at = self.expires_at
        self.anchors = json.loads(json.dumps(anchors))
        self.status = "running"

    def advance(self, *, now, rng):
        if self.status in ("stopping", "stopped", "expired") or self.started_at is None:
            return False
        if not math.isfinite(now) or now < self.started_at:
            raise ValueError("Session clock is invalid")
        if self.expires_at is not None and now >= self.expires_at:
            self.status = "expired"
            self.generation += 1
            return True
        if self.request.selection == "fixed":
            return False
        step_index = int((now - self.started_at) // self.request.step_duration_seconds)
        if step_index == self.step_index:
            return False
        self.step_index = step_index
        self.generation += 1
        self.profile_id = self._draw(self.catalogue, self.request, rng)
        self.status = "preparing"
        self.anchors = {}
        step_end = self.started_at + (step_index + 1) * self.request.step_duration_seconds
        self.step_expires_at = min(self.expires_at, step_end) if self.expires_at is not None else step_end
        return True

    def permits(self, *, session_id, generation, now):
        state = {name: getattr(self, name) for name in (
            "status", "id", "generation", "started_at", "expires_at", "step_expires_at")}
        state['request'] = self.request.model_dump(exclude={'references', 'profile_ids'})
        state['catalogue'] = {'allow_until_stopped': self.catalogue.allow_until_stopped}
        return session_permits(state, session_id=session_id, generation=generation, now=now)
