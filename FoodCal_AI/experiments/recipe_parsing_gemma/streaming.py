"""Observable, cancellable NDJSON transport for one local Ollama request."""
import http.client
import json
import math
import queue
import socket
import threading
import time
from urllib.parse import urlparse


class HTTPStream:
    def __init__(self, base_url, payload, total_timeout):
        parsed = urlparse(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1') or
                parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Only loopback HTTP Ollama endpoints are allowed')
        self.host, self.port = parsed.hostname, parsed.port or 80
        self.payload, self.timeout = payload, total_timeout
        self._stop = threading.Event()
        self._socket = None
        self._thread = None

    def start(self, events):
        def worker():
            connection = http.client.HTTPConnection(self.host, self.port, timeout=min(10, self.timeout))
            try:
                connection.connect()
                self._socket = connection.sock
                if self._stop.is_set():
                    return
                # Header/token waiting may legitimately take a long time. The
                # controller enforces elapsed timeout and stall/cancel limits.
                self._socket.settimeout(self.timeout)
                body = json.dumps(self.payload, ensure_ascii=False).encode('utf-8')
                connection.request('POST', '/api/chat', body, {'Content-Type': 'application/json'})
                events.put(('transport', 'request_sent_waiting_headers'))
                response = connection.getresponse()
                events.put(('transport', 'response_headers_received'))
                if response.status != 200:
                    raise OSError(f'Ollama HTTP {response.status}: {response.read(4096).decode("utf-8", errors="replace")}')
                received = 0
                while not self._stop.is_set():
                    line = response.readline(1024*1024+1)
                    if not line:
                        events.put(('eof', None))
                        return
                    received += len(line)
                    if len(line) > 1024*1024 or received > 16*1024*1024:
                        raise ValueError('Stream exceeded safe byte bound')
                    if line.strip():
                        chunk = json.loads(line)
                        if not isinstance(chunk, dict):
                            raise ValueError('Expected NDJSON object')
                        events.put(('chunk', chunk))
                        if chunk.get('done') is True:
                            return
            except Exception as error:
                events.put(('error', error))
            finally:
                connection.close()
        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def cancel(self):
        self._stop.set()
        sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=1)


def collect_stream(transport, *, timeout, stall_timeout=1800, progress_interval=60,
                   cancelled=lambda: False, on_chunk=lambda chunk: None,
                   on_progress=lambda state: None):
    """Track actual text fragments, never infer loading from silence.

    first_token_seconds is client time to first nonempty content/thinking delta,
    not the server's internal tokenizer timestamp. Empty chunks do not reset stall.
    """
    if (not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(stall_timeout) or
            stall_timeout < 0 or progress_interval <= 0):
        raise ValueError('Invalid elapsed/stall/progress limits')
    events = queue.Queue()
    start = time.perf_counter()
    last_text = start
    next_progress = start + progress_interval
    content, thinking = [], []
    result = {'status': 'failed', 'raw_reply': '', 'thinking_reply': '', 'stream_chunks': 0,
              'first_token_seconds': None, 'first_content_seconds': None,
              'first_token_definition': 'client_first_nonempty_content_or_thinking_fragment',
              'final_response': None, 'transport_state': 'not_started', 'progress': []}

    def state(now, phase=None):
        idle = now-last_text
        if phase is None:
            phase = ('awaiting_first_text_stage_unknown' if result['first_token_seconds'] is None else
                     'waiting_no_new_text_stage_unknown' if idle >= progress_interval else 'generating_text_observed')
        return {'elapsed_seconds': round(now-start, 3), 'phase': phase,
                'transport_state': result['transport_state'], 'seconds_without_text': round(idle, 3),
                'content_characters': sum(map(len, content)), 'thinking_characters': sum(map(len, thinking)),
                'stream_chunks': result['stream_chunks'], 'first_token_seconds': result['first_token_seconds'],
                'first_content_seconds': result['first_content_seconds']}

    def progress(now, phase=None):
        snapshot = state(now, phase)
        result['progress'].append(snapshot)
        on_progress({**snapshot, 'raw_reply': ''.join(content), 'thinking_reply': ''.join(thinking)})

    try:
        progress(start)
        if cancelled():
            result.update(status='cancelled', error='Cancellation requested before model submission', error_type='Cancelled')
        else:
            transport.start(events)
            while True:
                now = time.perf_counter()
                if cancelled():
                    result.update(status='cancelled', error='Cancellation file requested stop', error_type='Cancelled')
                    break
                if now-start >= timeout:
                    result.update(status='timeout', error=f'Total inference deadline {timeout:g}s exceeded; not retried', error_type='TimeoutError')
                    break
                if stall_timeout and now-last_text >= stall_timeout:
                    progress(now, 'possible_stall')
                    result.update(status='stalled', error=f'No new content/thinking text for {stall_timeout:g}s; stage unconfirmed', error_type='StallDetected')
                    break
                if now >= next_progress:
                    progress(now)
                    next_progress = now + progress_interval
                wait = min(.25, max(.001, timeout-(now-start)), max(.001, next_progress-now))
                if stall_timeout:
                    wait = min(wait, max(.001, stall_timeout-(now-last_text)))
                try:
                    kind, value = events.get(timeout=wait)
                except queue.Empty:
                    continue
                if kind == 'transport':
                    result['transport_state'] = value
                elif kind == 'error':
                    raise value
                elif kind == 'eof':
                    raise OSError('Stream ended without done=true; partial text preserved')
                elif kind == 'chunk':
                    on_chunk(value)
                    result['stream_chunks'] += 1
                    message = value.get('message', {})
                    if not isinstance(message, dict):
                        raise ValueError('Invalid stream message object')
                    delta_content, delta_thinking = message.get('content', ''), message.get('thinking', '')
                    if not isinstance(delta_content, str) or not isinstance(delta_thinking, str):
                        raise ValueError('Invalid text delta')
                    received_at = time.perf_counter()
                    if delta_content or delta_thinking:
                        last_text = received_at
                        if result['first_token_seconds'] is None:
                            result['first_token_seconds'] = round(received_at-start, 3)
                        if delta_content and result['first_content_seconds'] is None:
                            result['first_content_seconds'] = round(received_at-start, 3)
                        content.append(delta_content)
                        thinking.append(delta_thinking)
                    if value.get('error'):
                        raise OSError(str(value['error']))
                    if value.get('done') is True:
                        result.update(status='completed', final_response=value)
                        break
    except KeyboardInterrupt:
        result.update(status='cancelled', error='Ctrl+C requested stop', error_type='KeyboardInterrupt')
    except Exception as error:
        result.update(status='failed', error=str(error), error_type=type(error).__name__)
    finally:
        transport.cancel()
        result.update(raw_reply=''.join(content), thinking_reply=''.join(thinking),
                      inference_elapsed_seconds=round(time.perf_counter()-start, 3))
        progress(time.perf_counter(), result['status'])
    return result
