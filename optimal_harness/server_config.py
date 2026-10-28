"""Read-only provenance for the shared MLX server's prompt-cache settings."""
from __future__ import annotations
import shlex
import subprocess


def discover_cache_configuration(port=8080):
    try:
        result = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {'source': 'live_process_argv', 'verified': False, 'reason': 'process inspection unavailable'}
    if result.returncode != 0:
        return {'source': 'live_process_argv', 'verified': False, 'reason': 'process inspection failed'}
    candidates = []
    for line in result.stdout.splitlines():
        # ps is a display string, not shell-escaped argv; never shlex-parse it.
        words = line.split()
        if not any(w.endswith('mlx_lm.server') for w in words[1:3]) or '--port' not in words:
            continue
        def value(key):
            try: return words[words.index(key)+1]
            except (ValueError, IndexError): return None
        if value('--port') != str(port):
            continue
        candidates.append({'source': 'live_process_argv', 'pid': int(words[0]),
                           'prompt_cache_bytes': int(value('--prompt-cache-bytes') or 0),
                           'prompt_cache_size': value('--prompt-cache-size'),
                           'decode_concurrency': value('--decode-concurrency'),
                           'prompt_concurrency': value('--prompt-concurrency')})
    if len(candidates) != 1:
        return {'source': 'live_process_argv', 'verified': False, 'reason': 'no unique matching MLX server'}
    config = candidates[0]
    config['verified'] = (0 < config['prompt_cache_bytes'] <= 268435456 and
                          config['decode_concurrency'] == '1' and config['prompt_concurrency'] == '1')
    return config
