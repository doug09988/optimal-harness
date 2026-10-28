"""Bounded fresh-context runtime, with trusted receipts and honest handoffs."""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
from .ledger import Ledger
from .model import SYSTEM_PREFIX, parse_action, ModelError
from .policy import PolicyError, atomic_write, confined, sandbox_command
from .store import canonical, LeaseLost
from .handoff import ready_document, compose_handoff
from .review import SECTIONS, validate_section, validate_document, section_template



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
    review_sections = {}
    review_errors = {}

    def review_template(section):
        return section_template(conf['deliverables'][0], section, task['brief']['prior_stage'])

    finish_template = canonical({'action': 'finish', 'summary': 'Describe completed work',
                                 'open_discrepancies': []})

    def intake_template(path):
        from .verifier import LIFECYCLE
        return canonical({'action': 'write', 'path': path, 'data': {'slots': {
            key: {'status': 'gap', 'detail': 'Describe the actual gap'} for key in LIFECYCLE}}})

    def review_reject(section, reason):
        review_errors[section] = review_errors.get(section, 0) + 1
        if review_errors[section] >= 3:
            raise RuntimeError("review_section_exhausted:" + section + "; template: " + review_template(section))
        return "REVIEW section " + section + ": template: " + review_template(section) + "; " + reason + "; invalid submissions=" + str(review_errors[section])

    def next_review_section():
        return next((key for key in SECTIONS if key not in review_sections), SECTIONS[0])
    action_format_errors = 0
    coder_handoff_errors = 0
    finish_feedback = ''
    chunk_limit = min(4096, max(1, int(ledger.budget['max_completion_tokens']) - 512))
    chunk_feedback = (f"{task['stage']} action size: action may be truncated or oversized. Use a smaller chunk; "
                      f'ONE complete JSON action at most {chunk_limit} UTF-8 bytes including escaping. '
                      'First write with more=true, then append with more=true; final append more=false. '
                      'Rejected actions wrote no bytes; retry only the rejected chunk.')
    manifest = {'task': task['id'], 'generation': task['generation'], 'role': task['stage'],
                'budget': ledger.budget, 'static_prefix_sha256': hashlib.sha256(SYSTEM_PREFIX.encode()).hexdigest(),
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

    def write_checkpoint(path, data=None, append=False):
        def effect(c):
            count = None
            if data is not None:
                count = broker.write(path, data, append=append)
            engine._checkpoint(root, task, path, checkpoints, 'runtime', c)
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

    try:
        if not engine._pins_intact(plan, root):
            raise PolicyError('pinned input or charter changed')
        broker, evidence, handoff, brief = engine._prepare(root, task)
        if task['stage'] == 'REVIEW':
            last = ('REVIEW sections — normally submit several or all four sections in one write; any subset is accepted. Example content must reflect your work:\n' +
                    '\n'.join(key + ': ' + review_template(key) for key in SECTIONS))
            last_observation = last
        manifest['evidence'], manifest['handoff'] = evidence, handoff
        trusted_write(manifest_path, (canonical(manifest)+'\n').encode())
        while True:
            store.assert_lease(task)
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
                       'Respond with ONE next JSON action only.')
            missing = [p for p in conf.get('deliverables', []) if p not in checkpoints or p in partial]
            commands = {cmd['capture']: name for name, cmd in conf.get('commands', {}).items()}
            if missing:
                target = missing[0]
                next_step = ('Run named command ' + commands[target] if target in commands else
                             ('Continue ' + target + ' using action=append; final chunk more=false' if target in partial else
                              'Create ' + target + ' using action=write, path=' + target +
                              ', data=<file text or first chunk>, more=<true if chunks remain>'))
            elif handoff not in checkpoints:
                next_step = 'Emit action=finish with summary and open_discrepancies; runtime generates ' + handoff
            else:
                next_step = 'All required outputs exist. Emit action=finish now; do not read or rewrite them.'
            if ledger.calls == 0 and plan['spec'] in task['brief']['permitted_reads']:
                next_step = 'Read ' + plan['spec'] + ' once to obtain the authoritative source before writing.'
            if task['stage'] == 'REVIEW' and missing:
                next_step = ('Write several or all remaining review sections to ' + missing[0] + ', data={' +
                             canonical(next_review_section()) + ': <section value>}; accepted sections are retained')
            if finish_feedback:
                next_step = finish_feedback
            prompt += '\nSCHEDULER CURRENT STEP (not a new run): ' + next_step
            prompt += '\nCompleted model calls in this attempt: ' + str(ledger.calls)
            if last.startswith('{') and '"content":' in last:
                prompt += '\nThe read response above is already available. NOW perform the current write step, not another read.'
            if task['stage'] == 'REVIEW':
                next_step = next_review_section()
                prompt += ('\nREVIEW SECTION WORKFLOW: Submit any subset of the four named sections per write, normally several or all, data={"<section>": <value>, ...}, '
                           'more=false; no append or chunks. Each action is bounded to ' + str(chunk_limit) +
                           ' UTF-8 bytes. Accepted sections may be rewritten independently. '
                           'counterexamples must contain at least one nonempty ATTEMPTED case even when no defect is found. '
                           'Accepted=' + canonical(list(review_sections)) + '; missing=' +
                           canonical([key for key in SECTIONS if key not in review_sections]) +
                           '; next section=' + next_step + '; finish after all sections are accepted.')
            else:
                prompt += '\nCHUNK RULE: ' + chunk_feedback
            cap, timeout = ledger.reserve(prompt, SYSTEM_PREFIX)
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
            first = ''
            try:
                decoder = json.JSONDecoder()
                _, end = decoder.raw_decode(result.text.lstrip())
                first = result.text.lstrip()[:end]
                if result.text.lstrip()[end:].strip():
                    store.event('additional_actions_discarded', {'task': task['id'], 'call': ledger.calls,
                                'reason': 'only first JSON object is eligible; no speculative follow-up executes'})
                if len(first.encode('utf-8')) > chunk_limit:
                    raise ModelError('encoded action exceeds chunk limit')
                action = parse_action(first)
            except (ModelError, ValueError) as exc:
                # A bounded correction is fresh, not replay of malformed history.
                last = ('PROTOCOL_ERROR: ' + str(exc) + '. Respond with exactly one JSON action; JSON documents may use object-valued data.\n'
                        + 'LAST SUCCESSFUL TOOL OBSERVATION (still authoritative):\n' + last_observation)
                if task['stage'] == 'REVIEW':
                    section = next_review_section()
                    try:
                        candidate = json.loads(first)
                        payload = candidate.get('data', {})
                        if isinstance(payload, str):
                            payload = json.loads(payload)
                        if isinstance(payload, dict) and len(payload) == 1 and next(iter(payload)) in SECTIONS:
                            section = next(iter(payload))
                    except (ValueError, TypeError, AttributeError):
                        pass
                    last = (review_reject(section, str(exc)) + '\nSubmit a smaller complete subset of sections per write; accepted sections are retained. '
                            'counterexamples requires at least one nonempty ATTEMPTED case even when no defect is found.'
                            '\nLAST SUCCESSFUL TOOL OBSERVATION:\n' + last_observation)
                    continue
                action_format_errors += 1
                template = (intake_template(conf['deliverables'][0]) if task['stage'] == 'INTAKE' else
                            canonical({'action': 'write', 'path': (conf.get('deliverables') or ['output.txt'])[0],
                                       'data': 'Small complete file or first chunk', 'more': True}))
                last = 'Action template: ' + template + '\n' + chunk_feedback + '\n' + last
                if action_format_errors >= 3:
                    raise RuntimeError(task['stage'].lower() + '_action_format_exhausted:three invalid or oversized actions; use smaller chunks; template: ' + template)
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
                            raise PolicyError('write exceeds file limit')
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
                                              canonical({'action': 'write', 'path': handoff, 'data': {
                                                  'status': 'ready', 'open_discrepancies': []}})) from exc
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
                    if task['stage'] == 'REVIEW' and path in conf.get('deliverables', []):
                        try:
                            payload = json.loads(data) if isinstance(data, str) else data
                            if op != 'write' or action.get('more', False) or not isinstance(payload, dict):
                                raise PolicyError('submit a complete object containing any subset of review sections; no append or chunks')
                        except (PolicyError, ValueError) as exc:
                            raise PolicyError(review_reject(next_review_section(), str(exc))) from exc
                        accepted, rejected, exhausted = [], [], []
                        candidate = dict(review_sections)
                        for section, value in payload.items():
                            if section not in SECTIONS:
                                rejected.append('REVIEW unknown section ' + repr(section) + ': rejected; no section retry charged')
                                continue
                            try:
                                validate_section(section, value, task['brief']['prior_stage'])
                            except PolicyError as exc:
                                try:
                                    rejected.append(review_reject(section, str(exc)))
                                except RuntimeError as terminal:
                                    exhausted.append(str(terminal))
                            else:
                                candidate[section] = value
                                accepted.append(section)
                        newly_accepted = set(candidate) - set(review_sections)
                        if len(candidate) == len(SECTIONS):
                            validate_document(candidate, task['brief']['prior_stage'])
                            write_checkpoint(path, canonical(candidate) + '\n')
                        review_sections = candidate
                        if newly_accepted:
                            repeat = 0
                            seen_actions.clear()
                        finish_feedback = ''
                        last = canonical({'accepted_sections': list(review_sections),
                                          'accepted_this_write': accepted,
                                          'missing_sections': [key for key in SECTIONS if key not in review_sections],
                                          'assembled': path if len(review_sections) == len(SECTIONS) else None})
                        if rejected:
                            last += '\n' + '\n'.join(rejected)
                        if exhausted:
                            raise RuntimeError('; '.join(exhausted))
                        last_observation = last
                        store.event('tool_action', {'task': task['id'], 'generation': task['generation'],
                                    'op': op, 'path_or_name': path, 'receipt': last})
                        continue
                    if isinstance(data, (dict, list)):
                        data = canonical(data) + "\n"
                    intake_target = task['stage'] == 'INTAKE' and path in conf.get('deliverables', [])
                    if intake_target:
                        from .verifier import Verifier
                        target = confined(root, path)
                        if (target.is_file() and target.read_bytes() == data.encode('utf-8') and
                                not Verifier(root).intake(path, plan['spec'])):
                            raise PolicyError(INTAKE_COMPLETE)
                    count = write_checkpoint(action['path'], data, op == 'append')
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
                    command = conf['commands'][action['name']]
                    # Non-isolated commands are synchronous under lease fence: reclaimed
                    # workers cannot commit staged outputs after another worker claims.
                    def command_effect(c):
                        output = sandbox_command(root, command, min(ledger.budget['max_command_seconds'],
                                                 ledger.budget['max_seconds']-ledger.seconds))
                        capture = command['capture']
                        payload = (canonical(output)+'\n').encode()
                        owned_capture = f"vault/runs/{task['id']}/a{task['generation']}/commands/{action['name']}.json"
                        atomic_write(confined(root, owned_capture), payload)
                        engine._checkpoint(root, task, owned_capture, checkpoints, 'command', c)
                        atomic_write(confined(root, capture), payload)
                        engine._checkpoint(root, task, capture, checkpoints, 'command_alias', c)
                        for path in command.get('writes', []):
                            if confined(root, path).is_file():
                                engine._checkpoint(root, task, path, checkpoints, 'command_write', c)
                        return {'capture': owned_capture, 'declared_capture': capture, 'exit_code': output['exit_code'], 'reason': output['reason'],
                                'containment': output.get('containment', 'test-double')}
                    receipt = await asyncio.to_thread(store.commit, task, command_effect)
                    last = canonical(receipt) + '\nFull output is at capture pointer. Read it if needed.'
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
            store.finish(task, 'FAILED', reason)
            store.project_status(task['project_id'], 'ESCALATED', reason)
        except LeaseLost:
            pass
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
