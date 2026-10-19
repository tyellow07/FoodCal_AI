"""Content-free, advisory token accounting; never enforces a server budget."""


def _count(value):
    return value if type(value) is int and value >= 0 else None


def diagnose_generation_budget(options, final_response=None, *, backend_phase_counts=None):
    """Summarize local options and API counters without copying arbitrary fields.

    Optional phase counts are analyst-supplied observations from a correlated
    local log, not proof of server options. None means unobserved. A phase count
    may be None when no exact terminal counter was logged. No text/log is saved.
    """
    options = options if isinstance(options, dict) else {}
    final = final_response if isinstance(final_response, dict) else {}
    requested = {key: _count(options.get(key)) for key in ('num_predict', 'num_ctx')}
    counters = {key: _count(final.get(key)) for key in ('prompt_eval_count', 'eval_count')}
    codes = ['server_effective_budget_unobserved']
    for key in counters:
        if key not in final:
            codes.append('missing_' + key)
        elif counters[key] is None:
            codes.append('invalid_' + key)
    budget, generated = requested['num_predict'], counters['eval_count']
    excess = generated - budget if generated is not None and budget is not None else None
    if excess is not None and excess > 0:
        codes.append('reported_generation_exceeds_requested')
    reason = final.get('done_reason')
    # Unknown server strings are not copied into this content-free diagnostic.
    reason = reason if reason in ('stop', 'length') else None
    if reason == 'length':
        codes.append('length_cause_unresolved')
    phases = None
    if backend_phase_counts is not None:
        if not isinstance(backend_phase_counts, (list, tuple)):
            codes.append('invalid_backend_phase_counts')
        else:
            phases = [_count(value) for value in backend_phase_counts]
            if any(value is None for value in phases):
                codes.append('incomplete_backend_phase_counts')
            if len(phases) > 1:
                codes.append('multiple_backend_phases_observed')
            if phases and all(value is not None for value in phases) and generated is not None:
                if sum(phases) != generated:
                    codes.append('backend_api_count_mismatch')
    return {
        'version': 1,
        'client_request_options': requested,
        'server_received_options': None,
        'backend_effective_budget': None,
        'api_final_counters': counters,
        'done_reason': reason,
        'reported_excess_tokens': max(0, excess) if excess is not None else None,
        'aggregate_within_requested': excess <= 0 if excess is not None else None,
        'backend_phase_counts': phases,
        'backend_observation_origin': 'caller_correlated_local_log' if phases is not None else None,
        'thinking_token_count': None,
        'answer_token_count': None,
        'context_occupancy': None,
        'hard_generation_cap_verified': False,
        'codes': codes,
    }
