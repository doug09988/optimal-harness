"""Attempt-local, server-usage ledger; conservative reserve before dispatch."""
from __future__ import annotations
import time

DEFAULT_BUDGET = {'max_calls': 60, 'max_fresh_tokens': 200000,
                  'max_seconds': 1800, 'max_prompt_bytes': 131072,
                  'max_command_seconds': 60, 'max_observation_bytes': 32768,
                  'max_call_seconds': 180, 'max_completion_tokens': 8192}


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, config=None, clock=time.monotonic):
        self.budget = DEFAULT_BUDGET | (config or {})
        if any(type(v) not in (int, float) or v <= 0 for v in self.budget.values()):
            raise ValueError('budgets must be positive numbers')
        self.clock = clock
        self.started = clock()
        self.calls = self.fresh = self.cached = 0

    @property
    def seconds(self):
        return self.clock() - self.started

    def reserve(self, prompt, static_prefix=''):
        if self.calls >= self.budget['max_calls']:
            raise BudgetExceeded('circuit_breaker:model_calls')
        if self.seconds >= self.budget['max_seconds']:
            raise BudgetExceeded('circuit_breaker:wall_clock')
        if len(prompt.encode()) > self.budget['max_prompt_bytes']:
            raise BudgetExceeded('circuit_breaker:context_bytes')
        # UTF-8 bytes conservatively bound text tokens; reserve template overhead.
        # Cache cannot be assumed before the server reports it.
        prompt_reserve = len((static_prefix + prompt).encode()) + 512
        cap = int(self.budget['max_fresh_tokens'] - self.fresh - prompt_reserve)
        if cap < 1:
            raise BudgetExceeded('circuit_breaker:fresh_tokens_reserve')
        return min(cap, int(self.budget['max_completion_tokens'])), min(self.budget['max_call_seconds'], self.budget['max_seconds']-self.seconds)

    def charge(self, result):
        values = (result.prompt_tokens, result.cached_tokens, result.completion_tokens)
        if any(type(v) is not int or v < 0 for v in values) or values[1] > values[0]:
            raise ValueError('invalid server usage')
        self.calls += 1
        self.fresh += result.fresh_tokens
        self.cached += result.cached_tokens
        if self.fresh > self.budget['max_fresh_tokens']:
            raise BudgetExceeded('circuit_breaker:fresh_tokens_after_meter')

    def totals(self):
        return {'calls': self.calls, 'fresh_tokens': self.fresh, 'cached_tokens_not_billed': self.cached,
                'seconds': self.seconds}
