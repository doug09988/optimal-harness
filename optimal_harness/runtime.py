"""Bounded fresh-context runtime, with trusted receipts and honest handoffs."""
from __future__ import annotations
import asyncio
import hashlib
import json
import time
from pathlib import Path
from .ledger import Ledger
from .model import SYSTEM_PREFIX, RawWritePrompt, parse_action, ModelError
from .policy import PolicyError, atomic_write, confined, sandbox_command
from .store import canonical, LeaseLost
from .handoff import ready_document, compose_handoff
from .review import DrivenReview
from .qa import DrivenQA
from .coder import DrivenCoder
from .integrator import DrivenIntegrator
from .fixer import DrivenFixer



INTAKE_COMPLETE = ('INTAKE artifact is complete and valid. The remaining step is to finish '
                   'with summary and open_discrepancies; do not rewrite the artifact. Template: '
                   '{"action":"finish","summary":"Describe completed work","open_discrepancies":[]}')


def intake_span_error(key, reason, lines, start, end):
    """Give bounded, source-derived correction context without relaxing span checks."""
    anchors = [n for n in (start, end) if type(n) is int]
    low = max(1, min(len(lines), min(anchors))) if anchors and lines else 1
    high = max(1, min(len(lines), max(anchors))) if anchors and lines else low
    before = next((n for n in range(min(low, len(lines)), 0, -1) if lines[n-1].strip()), None)
    after = next((n for n in range(high, len(lines)+1) if lines[n-1].strip()), None)
    nearby = sorted({n for n in (before, after) if n is not None})
    context = '; '.join(f'line {n}: {lines[n-1].rstrip()[:400]}' for n in nearby)
    return PolicyError(f'INTAKE pointer: {reason}: {key}; requested span={start!r}..{end!r}; '
                       f'spec total lines={len(lines)}; nearest non-empty lines: '
                       + (context or '(none)'))


def intake_document(data, source_read=None):
    """Normalize only an unambiguous wrapper omission; never invent slot content."""
    from .verifier import LIFECYCLE
    try:
        document = json.loads(data) if isinstance(data, str) else data
    except ValueError as exc:
        raise PolicyError('INTAKE schema: data must be a complete JSON object') from exc
    if isinstance(document, dict) and set(document) == set(LIFECYCLE):
        document = {'slots': document}
    slots = document.get('slots', {}) if isinstance(document, dict) else {}
    if isinstance(slots, list):
        if any(not isinstance(item, dict) or item.get('slot') not in LIFECYCLE for item in slots):
            raise PolicyError('INTAKE pointer: invalid slot')
        if len({item['slot'] for item in slots}) != len(slots):
            raise PolicyError('INTAKE pointer: duplicate slot')
        slots = {item['slot']: item for item in slots}
        document = {'slots': slots}
    if not isinstance(slots, dict) or set(slots) != set(LIFECYCLE):
        keys = set(slots) if isinstance(slots, dict) else set()
        raise PolicyError('INTAKE schema: rewrite the COMPLETE document with slots object; missing=' +
                          ','.join(sorted(set(LIFECYCLE)-keys)) + '; unexpected=' +
                          ','.join(sorted(keys-set(LIFECYCLE))))
    for key in LIFECYCLE:
        item = slots[key]
        if isinstance(item, dict) and any(field in item for field in ('source', 'start_line', 'end_line')):
            start, end = item.get('start_line'), item.get('end_line')
            if item.get('slot', key) != key:
                raise PolicyError('INTAKE pointer: slot mismatch: ' + key)
            if source_read is None or not isinstance(item.get('source'), str):
                raise PolicyError('INTAKE pointer: source reader and source required')
            lines = source_read(item['source']).splitlines(keepends=True)
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                reason = ('non-integer bounds' if type(start) is not int or type(end) is not int else
                          'reversed bounds' if start > end else 'out of range')
                raise intake_span_error(key, 'invalid inclusive line span (' + reason + ')', lines, start, end)
            quote = ''.join(lines[start-1:end])
            if not quote.strip():
                raise intake_span_error(key, 'empty line span', lines, start, end)
            item = dict(item, status='citation', quote=quote)
            slots[key] = item
        if not isinstance(item, dict) or item.get('status') not in ('citation', 'gap'):
            raise PolicyError('INTAKE schema: ' + key + ' must be an object with status=citation or gap')
        fields = ('quote', 'because') if item['status'] == 'citation' else ('detail',)
        for field in fields:
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise PolicyError('INTAKE schema: ' + key + '.' + field + ' must be a nonempty string')
    return document


async def run_task(engine, task):
    store = engine.store
    project = store.project(task['project_id'])
    plan, root = project['plan'], Path(project['root'])
    conf = plan['stages'][task['stage']]
    ledger = Ledger(conf.get('budget'))
    # The stage envelope is shared by every generation, including lease reclaims.
    prior_usage = store.stage_usage(task)
    ledger.calls, ledger.fresh, ledger.cached = (prior_usage[k] for k in ('calls', 'fresh', 'cached'))
    ledger.started -= max(0, time.time() - prior_usage['started'])
    checkpoints = {}
    monitor = asyncio.create_task(engine._heartbeat(task))
    broker = handoff = evidence = None
    manifest_path = f"vault/runs/{task['id']}/a{task['generation']}/manifest.json"
    last = 'New run. Write deliverables, then finish with summary and open_discrepancies.'
    last_observation = last
    repeat = 0
    previous = None
    seen_actions = {}
    intake_format_errors = 0
    partial = set()
    structured_chunks = {}
    review = DrivenReview(task['brief']['prior_stage']) if task['stage'] == 'REVIEW' else None
    qa = DrivenQA() if task['stage'] == 'QA' else None
    coder = DrivenCoder() if task['stage'] == 'CODER' else None
    finish_template = canonical({'action': 'finish', 'summary': 'Describe completed work',
                                 'open_discrepancies': []})

    def intake_template(path):
        from .verifier import LIFECYCLE
        return canonical({'action': 'write', 'path': path, 'data': {'slots': {
            key: {'status': 'gap', 'detail': 'Describe the actual gap'} for key in LIFECYCLE}}})

    raw_writer = task['stage'] == 'FIXER'

    def raw_template(path, content='Small complete file or first chunk', more=True):
        return canonical({'action': 'write', 'path': path, 'format': 'raw', 'more': more}) + '\n---CONTENT---\n' + content

    action_format_errors = 0
    coder_handoff_errors = 0
    finish_feedback = ''
    chunk_limit = min(4096, max(1, int(ledger.budget['max_completion_tokens']) - 512))
    chunk_feedback = (f"{task['stage']} action size: send ONE complete action. "
                      f'Write payloads are stored internally in chunks of at most {chunk_limit} UTF-8 bytes. '
                      'The total file size must stay within the file limit. '
                      'Use more=true while content remains; final append more=false. '
                      'Rejected actions wrote no bytes; correct the reported problem before retrying.')
    manifest = {'task': task['id'], 'generation': task['generation'], 'role': task['stage'],
                'budget': ledger.budget, 'budget_scope': 'shared_stage_task_all_generations', 'static_prefix_sha256': hashlib.sha256(SYSTEM_PREFIX.encode()).hexdigest(),
                'charter_sha256': plan['charter_hashes'][f"vault/charters/{task['stage'].lower()}.md"],
                'cache_configuration': getattr(engine.queue, 'cache_config', {'max_bytes': 268435456, 'verified': False}),
                'status': 'running'}
    manifest['cache_configuration_sha256'] = hashlib.sha256(canonical(manifest['cache_configuration']).encode()).hexdigest()

    def trusted_write(path, data):
        def effect(c):
            atomic_write(confined(root, path), data)
            # An honest runtime error may replace a model's ready handoff.
            # Keep its new trusted value checkpointed rather than falsely
            # diagnosing the runtime's own report update as outside tampering.
            if path in checkpoints:
                engine._checkpoint(root, task, path, checkpoints, 'trusted_report', c)
        store.commit(task, effect, allow_report=True)

    def write_checkpoint(path, data=None, append=False, chunks=None, on_checkpoint=None):
        def effect(c):
            count = None
            if data is not None:
                if chunks is not None:
                    payload = data.encode('utf-8')
                    target = confined(root, path)
                    old_size = target.stat().st_size if append and target.is_file() else 0
                    if old_size + len(payload) > broker.max_bytes:
                        raise PolicyError(f'write total file size exceeds file limit of {broker.max_bytes} UTF-8 bytes')
                    for index, chunk in enumerate(chunks or ['']):
                        count = broker.write(path, chunk, append=append or index > 0)
                else:
                    count = broker.write(path, data, append=append)
            engine._checkpoint(root, task, path, checkpoints, 'runtime', c)
            if on_checkpoint is not None:
                on_checkpoint(c)
            return count
        return store.commit(task, effect)

    def source_read(source):
        # Read the full source through the existing scoped, bounded page broker.
        chunks, offset = [], 0
        while True:
            text = broker.read(source, offset, broker.max_bytes)
            if not text:
                break
            chunks.append(text)
            offset += len(text.encode('utf-8'))
        return ''.join(chunks)

    async def execute_command(name):
        command = conf['commands'][name]
        # Non-isolated commands are synchronous under lease fence: reclaimed
        # workers cannot commit staged outputs after another worker claims.
        def command_effect(c):
            output = sandbox_command(root, command, min(ledger.budget['max_command_seconds'],
                                     ledger.budget['max_seconds']-ledger.seconds))
            capture = command['capture']
            payload = (canonical(output)+'\n').encode()
            owned_capture = f"vault/runs/{task['id']}/a{task['generation']}/commands/{name}.json"
            atomic_write(confined(root, owned_capture), payload)
            engine._checkpoint(root, task, owned_capture, checkpoints, 'command', c)
            atomic_write(confined(root, capture), payload)
            engine._checkpoint(root, task, capture, checkpoints, 'command_alias', c)
            for path in command.get('writes', []):
                if confined(root, path).is_file():
                    engine._checkpoint(root, task, path, checkpoints, 'command_write', c)
            return {'capture': owned_capture, 'declared_capture': capture, 'exit_code': output['exit_code'], 'reason': output['reason'],
                    'containment': output.get('containment', 'test-double')}
        return await asyncio.to_thread(store.commit, task, command_effect)

    def storage_chunks(data):
        payload, chunks = data.encode('utf-8'), []
        while payload:
            end = min(chunk_limit, len(payload))
            while end < len(payload) and payload[end] & 0xc0 == 0x80:
                end -= 1
            if not end:
                raise PolicyError('storage chunk limit cannot hold a UTF-8 character')
            chunks.append(payload[:end].decode('utf-8'))
            payload = payload[end:]
        return chunks

    try:
        if not engine._pins_intact(plan, root):
            raise PolicyError('pinned input or charter changed')
        broker, evidence, handoff, brief = engine._prepare(root, task)
        manifest['evidence'], manifest['handoff'] = evidence, handoff
        trusted_write(manifest_path, (canonical(manifest)+'\n').encode())
        if review is not None:
            # Hash complete source bytes, before prompt excerpts are shortened.
            inputs = set(task['brief']['permitted_reads'])
            input_hash = hashlib.sha256(canonical({
                path: hashlib.sha256(confined(root, path).read_bytes()).hexdigest()
                if confined(root, path).is_file() else None for path in sorted(inputs)
            }).encode()).hexdigest()
            review.restore(store.load_review_progress(task, input_hash))
        if qa is not None:
            # Hash input bytes without exposing command-only artifacts to the model.
            inputs = (set(task['brief']['permitted_reads']) | set(conf.get('command_only', [])) |
                      set(plan.get('implementation_paths', [])) | set(plan.get('immutable_inputs', [])) |
                      {plan['spec']})
            for command in conf.get('commands', {}).values():
                inputs.update(command.get('reads', []))
            inputs.difference_update(conf.get('deliverables', []))
            input_hash = hashlib.sha256(canonical({
                path: hashlib.sha256(confined(root, path).read_bytes()).hexdigest()
                if confined(root, path).is_file() else None for path in sorted(inputs)
            }).encode()).hexdigest()
            qa.restore(store.load_qa_progress(task, input_hash))
        if coder is not None:
            intake_paths = set(plan['stages'].get('INTAKE', {}).get('deliverables', []))
            broker.reads.update(intake_paths)
            inputs = set(task['brief']['permitted_reads']) | {plan['spec']} | intake_paths
            inputs.difference_update(conf.get('writes', []))
            input_hash = hashlib.sha256(canonical({
                path: hashlib.sha256(confined(root, path).read_bytes()).hexdigest()
                if confined(root, path).is_file() else None for path in sorted(inputs)
            }).encode()).hexdigest()
            coder.restore(store.load_coder_progress(task, input_hash))
            # Restore accepted bytes through the same broker/checkpoint path, never
            # trust an uncheckpointed on-disk file merely because it exists.
            for path, body in coder.bodies.items():
                write_checkpoint(path, body, chunks=storage_chunks(body))
        fixer = DrivenFixer() if task['stage'] == 'FIXER' else None
        integrator = DrivenIntegrator() if task['stage'] == 'INTEGRATOR' else None
        while True:
            store.assert_lease(task)
            if coder is not None and coder.current == 'check' and coder.unwritable:
                raise RuntimeError('coder_action_format_exhausted:unwritable; ' + canonical(coder.unwritable))
            if qa is not None and qa.current == 'report' and qa.execution is None:
                target = next(p for p in conf['deliverables'] if p.endswith('test_acceptance.py'))
                write_checkpoint(target, qa.assembly())
                last = canonical({'written': target, 'sha256': checkpoints[target],
                                  'planned_tests': qa.plan, 'unwritable': qa.unwritable})
                store.event('tool_action', {'task': task['id'], 'generation': task['generation'],
                            'op': 'write', 'path_or_name': target, 'receipt': last})
                name = next(name for name, cmd in conf['commands'].items() if target in cmd.get('reads', []))
                receipt = await execute_command(name)
                qa.execution = confined(root, receipt['capture']).read_text()
                last = canonical(receipt) + '\n' + qa.execution
                last_observation = last
                store.event('tool_action', {'task': task['id'], 'generation': task['generation'],
                            'op': 'command', 'path_or_name': name, 'receipt': last})
            if fixer is not None:
                action = await fixer.run(engine, task, plan, root, conf, ledger, manifest,
                                         broker, write_checkpoint, storage_chunks, checkpoints, trusted_write, evidence)
            elif integrator is not None:
                action = await integrator.run(engine, task, plan, root, conf, ledger, manifest,
                                              broker, write_checkpoint, storage_chunks, execute_command,
                                              trusted_write, evidence, checkpoints)
            else:
                progress = canonical({'checkpointed': sorted(set(checkpoints) - partial),
                                      'missing_deliverables': [p for p in conf.get('deliverables', []) if p not in checkpoints or p in partial],
                                      'partial_files_continue_appending': sorted(partial), 'handoff_path': handoff, 'evidence_path': evidence})
                missing = [p for p in conf.get('deliverables', []) if p not in checkpoints or p in partial]
                context_task = dict(task)
                context_task['brief'] = dict(task['brief']) | {
                    'deliverables': missing,
                    'already_checkpointed_do_not_repeat': sorted(set(checkpoints) - partial),
                    'role_work_complete': not missing,
                    'current_goal': ('Produce remaining deliverables only' if missing else
                                     'The role work is ALREADY COMPLETE. Finish with summary and open_discrepancies.'),
                }
                prompt = engine._context(root, context_task, broker, last) + '\nSAVED RUN STATE:\n' + progress
                prompt += ('\nEXECUTION RULE: The LAST TOOL OBSERVATION has ALREADY EXECUTED. '
                           'Do not request that same read again. Use the returned content to do the next step. '
                           'Files in checkpointed are already written; do not repeat those writes. '
                           'Once all deliverables are checkpointed, finish with summary=<factual summary> and '
                           'open_discrepancies=<list of actual gaps; [] only if none>. Runtime generates the handoff. '
                           + ('Respond with ONE next action only.' if raw_writer else 'Respond with ONE next JSON action only.'))
                if raw_writer:
                    prompt += ('\nWRITE PROTOCOL: For writes and appends, use format=raw by default: first line JSON header without data, '
                               'then an exact ---CONTENT--- line, then literal content through end-of-message. '
                               'Do not stop at the header closing brace. JSON data is a small-content alternative. '
                               'Read, command, finish and fail still use one JSON object.\n' + chunk_feedback)
                missing = [p for p in conf.get('deliverables', []) if p not in checkpoints or p in partial]
                commands = {cmd['capture']: name for name, cmd in conf.get('commands', {}).items()}
                if missing:
                    target = missing[0]
                    next_step = ('Run named command ' + commands[target] if target in commands else
                                 ('Continue ' + target + ' using action=append; final chunk more=false' if target in partial else
                                  'Create ' + target + ' using action=write, path=' + target +
                                  (', format=raw followed by separator and literal file text, more=<true if chunks remain>' if raw_writer else
                                   ', data=<file text or first chunk>, more=<true if chunks remain>')))
                elif handoff not in checkpoints:
                    next_step = 'Emit action=finish with summary and open_discrepancies; runtime generates ' + handoff
                else:
                    next_step = 'All required outputs exist. Emit action=finish now; do not read or rewrite them.'
                if manifest.get('calls_dispatched', 0) == 0 and plan['spec'] in task['brief']['permitted_reads']:
                    next_step = 'Read ' + plan['spec'] + ' once to obtain the authoritative source before writing.'
                if finish_feedback:
                    next_step = finish_feedback
                prompt += '\nSCHEDULER CURRENT STEP (not a new run): ' + next_step
                prompt += '\nCompleted model calls across this stage task: ' + str(ledger.calls)
                if last.startswith('{') and '"content":' in last:
                    prompt += '\nThe read response above is already available. NOW perform the current write step, not another read.'
                prompt += '\nCHUNK RULE: ' + chunk_feedback
                if review is not None:
                    # REVIEW owns progression; no action-loop instructions enter its prompt.
                    sources = {path: source_read(path) for path in sorted(task['brief']['permitted_reads'])
                               if confined(root, path).is_file()}
                    prompt = review.prompt(review.section_sources(
                        sources, plan['stages'].get(review.prior_stage, {}).get('deliverables', [])))
                    manifest['static_prefix_sha256'] = hashlib.sha256(prompt.system_prefix.encode()).hexdigest()
                if qa is not None:
                    sources = {path: source_read(path) for path in sorted(task['brief']['permitted_reads'])
                               if confined(root, path).is_file()}
                    prompt = qa.prompt(sources, record=lambda shed: store.event('prompt_context_shed',
                        dict(shed, task=task['id'], generation=task['generation'])))
                    manifest['static_prefix_sha256'] = hashlib.sha256(prompt.system_prefix.encode()).hexdigest()
                if coder is not None:
                    sources = {path: source_read(path) for path in sorted(inputs)
                               if confined(root, path).is_file()}
                    prompt = coder.prompt(sources, conf['writes'], conf.get('deliverables', []),
                        record=lambda shed: store.event('prompt_context_shed',
                            dict(shed, task=task['id'], generation=task['generation'])))
                    manifest['static_prefix_sha256'] = hashlib.sha256(prompt.system_prefix.encode()).hexdigest()
                if raw_writer:
                    prompt = RawWritePrompt(prompt)
                    manifest['static_prefix_sha256'] = hashlib.sha256(prompt.system_prefix.encode()).hexdigest()
                cap, timeout = ledger.reserve(prompt, getattr(prompt, "system_prefix", SYSTEM_PREFIX))
                if review is not None:
                    cap = min(cap, review.completion_cap)
                if qa is not None:
                    cap = min(cap, qa.completion_cap)
                if coder is not None:
                    cap = min(cap, coder.completion_cap)
                if task['stage'] in ('INTAKE', 'FIXER'):
                    cap = min(cap, 4096)
                store.event('model_call_started', {'task': task['id'], 'generation': task['generation'],
                            'call': ledger.calls+1, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                            'completion_cap': cap})
                manifest["calls_dispatched"] = manifest.get("calls_dispatched", 0) + 1
                try:
                    result = await engine.queue.request(prompt, cap, timeout)
                except Exception:
                    manifest["usage_unknown_for_failed_request"] = True
                    raise
                # Persist actual usage before the breaker, even if the server over-reports.
                store.event('model_call_finished', {'task': task['id'], 'generation': task['generation'],
                            'prompt_tokens': result.prompt_tokens, 'cached_tokens': result.cached_tokens,
                            'completion_tokens': result.completion_tokens, 'fresh_tokens': result.fresh_tokens,
                            'raw_action': result.text})
                metered = store.meter(task, result.prompt_tokens, result.cached_tokens, result.completion_tokens)
                ledger.charge(result)
                if metered["budget_exceeded"]:
                    raise RuntimeError("circuit_breaker:board_attempt_budget")
                if ledger.seconds >= ledger.budget['max_seconds']:
                    raise RuntimeError('circuit_breaker:wall_clock_after_call')
                if coder is not None:
                    current = coder.current
                    try:
                        if current == 'plan':
                            coder.accept_plan(result.text, conf['writes'],
                                [p for p in conf.get('deliverables', [])
                                 if p not in {cmd['capture'] for cmd in conf.get('commands', {}).values()}],
                                dropped=lambda entry: store.event('plan_entry_dropped', entry))
                        elif current == 'check':
                            try:
                                argv = coder.argv(result.text)
                                name = next((n for n, cmd in conf.get('commands', {}).items()
                                             if cmd['argv'] == argv), None)
                                if name is None:
                                    raise PolicyError('command not on existing executor allowlist')
                            except PolicyError as exc:
                                coder.execution.append({'status': 'skipped', 'reason': str(exc)})
                                coder.phase = 'done'
                            else:
                                coder.command = name
                                receipt = await execute_command(name)
                                output = json.loads(confined(root, receipt['capture']).read_text())
                                coder.execution.append(output)
                                coder.phase = 'done' if output['exit_code'] == 0 else 'repair'
                        elif current == 'repair':
                            path, sep, content = result.text.partition('\n')
                            if path not in coder.bodies or not sep or not content.strip():
                                raise PolicyError('repair requires accepted path line followed by content')
                            coder.repair_path = path
                            coder.phase = 'repair_body'
                        else:
                            path = coder.repair_path if current == 'repair_body' else current.removeprefix('file:')
                            body = result.text
                            if not body.strip() or '\x00' in body:
                                raise PolicyError('file content must be nonempty text without NUL')
                            chunks = storage_chunks(body)
                            count = write_checkpoint(path, body, chunks=chunks)
                            if len(chunks) > 1:
                                store.event('oversized_accepted_split', {'task': task['id'],
                                    'generation': task['generation'], 'op': 'write', 'path': path,
                                    'bytes': len(body.encode()), 'chunk_count': len(chunks), 'chunk_limit': chunk_limit})
                            coder.bodies[path] = body
                            store.event('tool_action', {'task': task['id'], 'generation': task['generation'],
                                        'op': 'write', 'path_or_name': path,
                                        'receipt': canonical({'written': path, 'bytes': count, 'sha256': checkpoints[path]})})
                            if current == 'repair_body':
                                receipt = await execute_command(coder.command)
                                coder.execution.append(json.loads(confined(root, receipt['capture']).read_text()))
                                coder.phase = 'done'
                        coder.feedback = ''
                    except (PolicyError, OSError, ValueError) as exc:
                        store.event('tool_denied', {'task': task['id'], 'op': 'write', 'reason': str(exc)})
                        if current == 'check':
                            coder.execution.append({'status': 'skipped', 'reason': str(exc)})
                            coder.phase = 'done'
                        else:
                            coder.reject(str(exc))
                    if coder.current == 'files':
                        coder.phase = 'check'
                    store.save_coder_progress(task, input_hash, coder.progress())
                    if coder.current == 'check' and coder.unwritable:
                        raise RuntimeError('coder_action_format_exhausted:unwritable; ' + canonical(coder.unwritable))
                    if coder.current != 'done':
                        continue
                    trusted_write(evidence, confined(root, evidence).read_bytes() +
                                  (canonical({'event': 'coder_self_check', 'results': coder.execution}) + '\n').encode())
                    action = coder.finish()
                elif qa is not None:
                    try:
                        action = qa.accept(result.text, spec=source_read(plan['spec']) if qa.current == 'plan' else '',
                            dropped=lambda entry: store.event('qa_test_dropped', entry),
                            fallback=lambda entry: store.event('qa_plan_citation_fallback', entry))
                    except PolicyError as exc:
                        last = str(exc)
                        store.event('tool_denied', {'task': task['id'], 'op': 'write', 'reason': last})
                        qa.reject(last)
                        action = None
                    store.save_qa_progress(task, input_hash, qa.progress())
                    if action is None:
                        continue
                elif review is not None:
                    section = review.current
                    try:
                        action = review.accept(result.text)
                    except PolicyError as exc:
                        last = str(exc)
                        store.event('tool_denied', {'task': task['id'], 'op': 'write', 'reason': last})
                        review.reject(last)
                        store.save_review_progress(task, input_hash, review.progress())
                        continue
                    store.save_review_progress(task, input_hash, review.progress())
                    last = canonical({'accepted_sections': list(review.sections),
                                      'accepted_this_write': [section] if section != 'verdict' else [],
                                      'missing_sections': review.missing})
                    store.event('tool_action', {'task': task['id'], 'generation': task['generation'],
                                'op': 'write', 'path_or_name': conf['deliverables'][0], 'receipt': last})
                    if action is None:
                        continue
                    write_checkpoint(conf['deliverables'][0], canonical(review.document()) + '\n')
                else:
                    first = ''
                    try:
                        decoder = json.JSONDecoder()
                        header, end = decoder.raw_decode(result.text.lstrip())
                        is_raw = isinstance(header, dict) and header.get('format') == 'raw'
                        first = result.text if is_raw else result.text.lstrip()[:end]
                        if not is_raw and result.text.lstrip()[end:].strip():
                            store.event('additional_actions_discarded', {'task': task['id'], 'call': ledger.calls,
                                        'reason': 'only first JSON object is eligible; no speculative follow-up executes'})
                        action = parse_action(first)
                        if action['action'] not in ('write', 'append') and len(first.encode('utf-8')) > chunk_limit:
                            raise ModelError('encoded action exceeds chunk limit')
                    except (ModelError, ValueError) as exc:
                        # A bounded correction is fresh, not replay of malformed history.
                        last = ('PROTOCOL_ERROR: ' + str(exc) + '. Respond with exactly one JSON action; JSON documents may use object-valued data.\n'
                                + 'LAST SUCCESSFUL TOOL OBSERVATION (still authoritative):\n' + last_observation)
                        action_format_errors += 1
                        template = (intake_template(conf['deliverables'][0]) if task['stage'] == 'INTAKE' else
                                    canonical({'action': 'write', 'path': (conf.get('deliverables') or ['output.txt'])[0],
                                               'data': 'Small complete file or first chunk', 'more': True}))
                        if raw_writer:
                            template = raw_template((conf.get('deliverables') or ['output.txt'])[0])
                            last = last.replace('exactly one JSON action; JSON documents may use object-valued data.',
                                                'exactly one action; use the raw write template for file content.')
                        last = 'Action template: ' + template + '\n' + chunk_feedback + '\n' + last
                        if action_format_errors >= 3:
                            raise RuntimeError(task['stage'].lower() + '_action_format_exhausted:three invalid or oversized non-write actions; template: ' + template)
                        repeat += 1
                        if repeat >= 3:
                            raise RuntimeError('error:no_progress:invalid_actions')
                        continue
            op = action['action']
            signature = canonical(action)
            repeat = repeat + 1 if signature == previous else 0
            previous = signature
            seen_actions[signature] = seen_actions.get(signature, 0) + 1
            if repeat >= 3 or seen_actions[signature] >= 4:
                raise RuntimeError('error:no_progress:repeated_action')
            if op == 'fail':
                raise RuntimeError('honest_worker_failure:' + action['reason'])
            if op == 'finish':
                if partial:
                    raise RuntimeError('incomplete_chunked_files:' + ','.join(sorted(partial)))
                try:
                    if 'open_discrepancies' in action:
                        try:
                            from .obligations import resolutions
                            resolved = resolutions(action.get('obligation_resolutions', []),
                                                   plan.get('obligations', []),
                                                   action.get('open_discrepancies', []), source_read)
                            document = compose_handoff(action, conf.get('deliverables', []), resolved)
                        except (ValueError, PolicyError, OSError) as exc:
                            raise RuntimeError('handoff_not_ready_or_malformed:' + str(exc)) from exc
                        write_checkpoint(handoff, canonical(document) + '\n')
                    engine._check_stage(root, task, conf, checkpoints, handoff)
                except RuntimeError as exc:
                    if (task['stage'] != 'CODER' and 'open_discrepancies' not in action and
                            str(exc).startswith('error:missing_deliverables:')):
                        raise
                    if not str(exc).startswith((
                            'handoff_not_ready_or_malformed', 'error:missing_deliverables:')):
                        raise
                    coder_handoff_errors += 1
                    if coder_handoff_errors >= 3:
                        raise RuntimeError(task['stage'].lower() + '_handoff_exhausted:' + str(exc) + '; template: ' + finish_template) from exc
                    finish_feedback = ('Correct readiness failure before finish: ' + str(exc) +
                                       '; finish requires summary and open_discrepancies; template: ' + finish_template)
                    last = finish_feedback
                    store.event('handoff_validation_denied', {'task': task['id'], 'reason': str(exc)})
                    continue
                manifest.update(status='succeeded', usage=ledger.totals())
                trusted_write(manifest_path, (canonical(manifest)+'\n').encode())
                store.handoff(task, {'status': 'ready', 'evidence': evidence, 'handoff': handoff,
                                    'manifest': manifest_path, 'usage': ledger.totals(), 'budget_exceeded': False})
                store.finish(task, 'SUCCEEDED')
                engine._advance(task, plan, root)
                return
            try:
                store.assert_lease(task)
                if op == 'read':
                    offset, length = action.get('offset', 0), action.get('length', 16384)
                    # Budget includes JSON escaping; no silent truncation of pages.
                    if length > ledger.budget['max_observation_bytes'] // 2:
                        raise PolicyError('page exceeds observation budget; use smaller length')
                    content = broker.read(action['path'], offset, length)
                    full = confined(root, action['path'])
                    last = canonical({'path': action['path'], 'offset': offset, 'length': length,
                                      'total_bytes': full.stat().st_size, 'content': content,
                                      'next_offset': min(offset+len(content.encode('utf-8')), full.stat().st_size)})
                    if len(last.encode('utf-8')) > ledger.budget['max_observation_bytes']:
                        raise PolicyError('encoded read page exceeds observation budget; reduce length')
                elif op in ('write', 'append'):
                    data = action["data"]
                    path = action['path']
                    if (task['stage'] == 'INTAKE' and path in conf.get('deliverables', []) and
                            (action.get('more', False) or path in structured_chunks)):
                        if not isinstance(data, str):
                            raise PolicyError('structured chunks require text; use object data only for complete documents')
                        if op == 'append' and path not in structured_chunks:
                            raise PolicyError('structured append requires a staged first write')
                        data = (structured_chunks.get(path, '') if op == 'append' else '') + data
                        if len(data.encode('utf-8')) > broker.max_bytes:
                            raise PolicyError('write total file size exceeds file limit')
                        if action.get('more', False):
                            # Stage JSON fragments in memory. Nothing is written until
                            # the complete document passes the existing schema check.
                            structured_chunks[path] = data
                            partial.add(path)
                            last = canonical({'staged': path, 'bytes': len(data.encode('utf-8')),
                                              'more': True, 'written_bytes': 0})
                            last_observation = last
                            store.event('tool_action', {'task': task['id'], 'generation': task['generation'],
                                        'op': op, 'path_or_name': path, 'receipt': last})
                            continue
                        op = 'write'
                    if task['stage'] == 'CODER' and action['path'] == handoff:
                        try:
                            if op != 'write' or action.get('more', False):
                                raise ValueError('handoff requires one complete write with more=false')
                            data = ready_document(data)
                        except ValueError as exc:
                            coder_handoff_errors += 1
                            if coder_handoff_errors >= 3:
                                raise RuntimeError(task['stage'].lower() + '_handoff_exhausted:' + str(exc) + '; template: ' + finish_template) from exc
                            raise PolicyError('CODER handoff schema: ' + str(exc) +
                                              '. Rewrite the complete handoff object; then finish. Template: ' +
                                              raw_template(handoff, canonical({'status': 'ready', 'open_discrepancies': []}), False)) from exc
                    if task['stage'] == 'INTAKE' and action['path'] in conf.get('deliverables', []):
                        try:
                            if op != 'write':
                                raise PolicyError('INTAKE schema: use write for the complete JSON document, not append')
                            def spec_read(source):
                                if source != plan['spec']:
                                    raise PolicyError('INTAKE pointer: source must be project specification')
                                return source_read(source)
                            data = intake_document(data, spec_read)
                        except PolicyError as exc:
                            intake_format_errors += 1
                            if intake_format_errors >= 3:
                                raise RuntimeError('intake_format_exhausted:three invalid documents; no artifact accepted; template: ' + intake_template(path))
                            raise PolicyError('Template (fill with actual gaps or citations): ' +
                                              intake_template(path) + '; ' + str(exc)) from exc
                    if isinstance(data, (dict, list)):
                        data = canonical(data) + "\n"
                    intake_target = task['stage'] == 'INTAKE' and path in conf.get('deliverables', [])
                    if intake_target:
                        from .verifier import Verifier
                        target = confined(root, path)
                        if (path in checkpoints and target.is_file() and target.read_bytes() == data.encode('utf-8') and
                                not Verifier(root).intake(path, plan['spec'])):
                            raise PolicyError(INTAKE_COMPLETE)
                    payload = data.encode('utf-8')
                    chunks = []
                    while payload:
                        end = min(chunk_limit, len(payload))
                        # Keep each storage unit within the byte cap and UTF-8 aligned.
                        while end < len(payload) and payload[end] & 0xc0 == 0x80:
                            end -= 1
                        if not end:
                            raise PolicyError('storage chunk limit cannot hold a UTF-8 character')
                        chunks.append(payload[:end].decode('utf-8'))
                        payload = payload[end:]
                    count = write_checkpoint(action['path'], data, op == 'append', chunks=chunks)
                    if len(chunks) > 1:
                        store.event('oversized_accepted_split', {
                            'task': task['id'], 'generation': task['generation'],
                            'op': op, 'path': path, 'bytes': len(data.encode('utf-8')),
                            'chunk_count': len(chunks), 'chunk_limit': chunk_limit})
                    structured_chunks.pop(action['path'], None)
                    finish_feedback = ''
                    if action.get('more', False):
                        partial.add(action['path'])
                    else:
                        partial.discard(action['path'])
                    last = canonical({'more': action.get('more', False), 'written': action['path'], 'bytes': count, 'sha256': checkpoints[action['path']]})
                    if intake_target and not Verifier(root).intake(path, plan['spec']):
                        last += '\n' + INTAKE_COMPLETE
                elif op == 'command':
                    if action['name'] not in conf.get('commands', {}):
                        raise PolicyError('command not allowed')
                    receipt = await execute_command(action['name'])
                    last = canonical(receipt) + '\nFull output is at capture pointer. Read it if needed.'
                    if qa is not None:
                        qa.execution = confined(root, receipt['capture']).read_text()
                        last += '\n' + qa.execution
                last_observation = last
                store.event('tool_action', {'task': task['id'], 'generation': task['generation'],
                            'op': op, 'path_or_name': action.get('path', action.get('name')), 'receipt': last})
            except (PolicyError, OSError, ValueError) as exc:
                if (task['stage'] == 'INTAKE' and op in ('write', 'append') and
                        action.get('path') in conf.get('deliverables', []) and
                        'Template' not in str(exc)):
                    exc = PolicyError('Template (complete document; fill with actual gaps or citations): ' +
                                      intake_template(action['path']) + '; ' + str(exc))
                last = 'TOOL_DENIED: ' + str(exc)[:1500] + '\nLAST SUCCESSFUL TOOL OBSERVATION:\n' + last_observation
                if len(last.encode()) > ledger.budget['max_observation_bytes']:
                    last = 'TOOL_DENIED: ' + str(exc)[:1500] + '\nRe-read the last source using a smaller page before retrying.'
                store.event('tool_denied', {'task': task['id'], 'op': op, 'reason': str(exc)})
    except (Exception, asyncio.CancelledError) as exc:
        reason = str(exc) or type(exc).__name__
        manifest.update(status='failed', reason=reason, usage=ledger.totals())
        store.event('run_aborted', {'task': task['id'], 'generation': task['generation'], **manifest})
        try:
            trusted_write(manifest_path, (canonical(manifest)+'\n').encode())
            if handoff:
                trusted_write(handoff, (canonical({'status': 'error', 'reason': reason,
                              'open_discrepancies': [reason], 'evidence': evidence})+'\n').encode())
                if evidence:
                    old = confined(root, evidence).read_bytes()
                    trusted_write(evidence, old+(canonical({'event': 'honest_failure', 'reason': reason})+'\n').encode())
            store.handoff(task, {'status': 'error', 'reason': reason, 'handoff': handoff, 'evidence': evidence,
                                'manifest': manifest_path, 'usage': ledger.totals(),
                                'budget_stopped': reason.startswith('circuit_breaker'),
                                'budget_exceeded': (ledger.fresh > ledger.budget['max_fresh_tokens'] or
                                                    ledger.calls > ledger.budget['max_calls'] or
                                                    ledger.seconds > ledger.budget['max_seconds'])})
            store.fail_attempt(task, reason, last, model_side_failure(reason))
        except LeaseLost:
            pass
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


def model_side_failure(reason):
    """Allowlist runtime protocol failures; verifier, integrity and budgets stay terminal."""
    if reason == 'model_request_killed_on_deadline':
        return True
    if reason.startswith('error:no_progress:'):
        return True
    prefixes = ['review_section_exhausted:', 'intake_format_exhausted:']
    prefixes += [role + suffix for role in ('intake', 'coder', 'integrator', 'qa', 'review', 'fixer')
                 for suffix in ('_action_format_exhausted:', '_handoff_exhausted:')]
    return reason.startswith(tuple(prefixes))
