"""Offline controlled bootstrap; protected state is a prerequisite, not authentication."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Callable, Protocol

from .context_provider import LocalContextProvider, ProviderResult, RegisteredReview
from .review_types import ReviewIssue


@dataclass(frozen=True)
class DeploymentConfig:
    deployment_id: str
    revision: int
    registry_sha256: str
    authority_ref: str
    code_sha256: str
    resolver_id: str
    status: str = 'active'


@dataclass(frozen=True)
class HighWatermark:
    deployment: DeploymentConfig
    checked_at: datetime


class HighWaterStore(Protocol):
    """Read protected latest state. Writes are operator deployment actions, not requests."""
    def read(self) -> HighWatermark: ...


@dataclass(frozen=True)
class BootstrapResult:
    bootstrap_gate: str
    issues: tuple[ReviewIssue, ...]
    provider_result: ProviderResult | None = None
    audit: dict | None = None
    usage: str = 'local_controlled/test_only'
    nutrition_eligible: bool = False
    submitter_authenticated: bool = False


class _Stop(Exception):
    def __init__(self, code, path, message):
        self.issue = ReviewIssue(code, path, message)


def _require(condition, code, path, message):
    if not condition:
        raise _Stop(code, path, message)


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate key')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Non-finite constant')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)


def controlled_file_reader(root, relative_name):
    """Trusted bootstrap helper. Does not grant ACLs or prevent filesystem races."""
    root = Path(root).resolve(strict=True)
    relative = Path(relative_name)
    if relative.is_absolute() or relative.drive or '..' in relative.parts:
        raise ValueError('Relative controlled path required')
    def read():
        target = (root / relative).resolve(strict=True)
        _require(target.is_relative_to(root), 'controlled_path_violation', '/registry', 'Path escapes controlled root')
        return target.read_bytes()
    return read


class BootstrapLoader:
    """Only operator bootstrap supplies configuration, store, clock and resolver.

    No persistent writes, automatic pin updates or fallback. Public calls cannot
    choose a minimum revision, trusted pin, resolver, root or clock.
    """
    def __init__(self, *, config_reader: Callable[[], DeploymentConfig], high_water: HighWaterStore,
                 registry_reader: Callable[[], bytes], clock: Callable[[], datetime],
                 code_sha256: str, resolver_id: str,
                 review_resolver: Callable[[DeploymentConfig], dict[str, RegisteredReview]]):
        if not _digest(code_sha256) or not isinstance(resolver_id, str) or not resolver_id.strip():
            raise ValueError('Independently controlled code pin and resolver ID required')
        self._config_reader, self._store, self._reader = config_reader, high_water, registry_reader
        self._clock, self._code, self._resolver_id = clock, code_sha256, resolver_id
        self._resolver = review_resolver
        self._last_config, self._last_time = None, None

    def _state(self):
        config, water = self._config_reader(), self._store.read()
        _require(isinstance(config, DeploymentConfig) and isinstance(water, HighWatermark)
                 and isinstance(water.deployment, DeploymentConfig),
                 'deployment_state_missing', '/bootstrap', 'Controlled configuration/high water required')
        for record in (config, water.deployment):
            _require(type(record.revision) is int and record.revision > 0
                     and _digest(record.registry_sha256) and _digest(record.code_sha256)
                     and all(isinstance(v, str) and v.strip() for v in
                             (record.deployment_id, record.authority_ref, record.resolver_id)),
                     'deployment_state_invalid', '/bootstrap', 'Malformed deployment metadata')
        _require(config.status == water.deployment.status == 'active',
                 'deployment_inactive', '/bootstrap', 'Deployment is not active')
        _require(config.revision >= water.deployment.revision,
                 'bootstrap_rollback', '/bootstrap/revision', 'Below protected high water')
        _require(config.revision == water.deployment.revision,
                 'deployment_state_inconsistent', '/bootstrap/revision', 'Activation is incomplete')
        _require(config.registry_sha256 == water.deployment.registry_sha256,
                 'bootstrap_revision_collision', '/bootstrap/registry_sha256', 'Same revision has different pins')
        _require(config == water.deployment, 'deployment_state_inconsistent', '/bootstrap', 'Deployment metadata differs')
        _require(config.code_sha256 == self._code and config.resolver_id == self._resolver_id,
                 'deployment_code_untrusted', '/bootstrap/code', 'Unapproved code or resolver identity')
        if self._last_config is not None:
            _require(config.revision >= self._last_config.revision, 'bootstrap_rollback', '/bootstrap', 'Observed state moved backwards')
            _require(config.revision != self._last_config.revision or config == self._last_config,
                     'bootstrap_revision_collision', '/bootstrap', 'Previously observed revision changed')
        _require(_aware(water.checked_at), 'bootstrap_time_untrusted', '/bootstrap/time', 'Protected time floor required')
        return config, water

    def _now(self, floor):
        now = self._clock()
        _require(_aware(now) and now >= floor and (self._last_time is None or now >= self._last_time),
                 'bootstrap_time_untrusted', '/clock', 'Clock unavailable or below trusted/observed floor')
        self._last_time = now
        return now

    def review_registered(self, context_id, *, expected_recipe_scope, expected_candidate_scope, basis_target):
        try:
            config, water = self._state()
            self._last_config = config
            now = self._now(water.checked_at)
            raw = self._reader()
            _require(isinstance(raw, bytes) and sha256(raw).hexdigest() == config.registry_sha256,
                     'registry_hash_mismatch', '/registry', 'Registry differs from protected deployment pin')
            registry = _json(raw)
            _require(type(registry['revision']) is int and registry['revision'] == config.revision
                     and registry['authority_ref'] == config.authority_ref
                     and registry['profile'] == 'foodcal.local_review_registry.v0',
                     'deployment_state_inconsistent', '/registry', 'Registry differs from active deployment')
            reviews = deepcopy(self._resolver(config))
            _require(isinstance(reviews, dict) and all(isinstance(v, RegisteredReview) for v in reviews.values()),
                     'bundle_untrusted', '/bundles', 'Controlled resolver must return registered bundles')
            provider = LocalContextProvider(registry_reader=lambda: raw, registry_sha256=config.registry_sha256,
                                            authority_ref=config.authority_ref, policy_revision=config.revision,
                                            reviews=reviews, clock=lambda: now)
            result = provider.review_registered(context_id, expected_recipe_scope=deepcopy(expected_recipe_scope),
                                                expected_candidate_scope=deepcopy(expected_candidate_scope),
                                                basis_target=deepcopy(basis_target))
            _require(self._state() == (config, water) and self._reader() == raw,
                     'deployment_changed', '/bootstrap', 'Controlled state changed during review')
            end = self._now(water.checked_at)
            if result.decision is not None:
                context = registry['contexts'][context_id]
                rows = _json(reviews[context_id].context.record_bytes)['reviews']
                reviewers = {r['result']['reviewer_ref'] for r in rows}
                leases = [context] + [registry['grants'][g] for g in context['grant_ids']
                                      if registry['grants'][g]['reviewer_id'] in reviewers]
                for lease in leases:
                    start, expiry = datetime.fromisoformat(lease['valid_from']), datetime.fromisoformat(lease['expires_at'])
                    _require(_aware(start) and _aware(expiry) and start <= end < expiry,
                             'review_expired', '/delivery', 'Lease expired before delivery')
            audit = {'deployment_id': config.deployment_id, 'policy_revision': config.revision,
                     'registry_sha256': config.registry_sha256, 'code_sha256': config.code_sha256,
                     'context_id': context_id, 'checked_at': now.isoformat(), 'delivered_at': end.isoformat(),
                     'provider_gate': result.provider_gate, 'issue_codes': [i.code for i in result.issues],
                     'validation_state': result.validation.state if result.validation else None,
                     'usage': 'local_controlled/test_only',
                     'nutrition_eligible': False}
            if context_id in registry['contexts']:
                context = registry['contexts'][context_id]
                audit.update({k: deepcopy(context[k]) for k in ('input_sha256', 'context_sha256', 'recipe_scope',
                                                               'candidate_scope', 'basis_target', 'grant_ids',
                                                               'source_registration_id')})
            if result.decision is not None:
                audit['review_records'] = [{k: deepcopy(r['result'][k]) for k in
                                           ('reviewer_ref', 'rule_id', 'rule_version', 'reviewed_at')}
                                          | {'process_ref': r['process_ref']} for r in rows]
                audit['official_dataset_version'] = registry['sources'][context['source_registration_id']]['official_dataset_version']
                audit['identity_decision'] = result.decision['identity_decision']
                audit['basis_decision'] = result.decision['basis_decision']
            return BootstrapResult('passed', result.issues, result, audit)
        except _Stop as error:
            return BootstrapResult('blocked', (error.issue,))
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, UnicodeError, OSError):
            return BootstrapResult('blocked', (ReviewIssue('bootstrap_data_invalid', '/bootstrap',
                                                          'Missing, malformed or unavailable controlled data; no fallback'),))
