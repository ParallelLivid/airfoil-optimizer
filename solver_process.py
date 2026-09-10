"""Supervise a solver without unbounded stdout buffering or log-file growth."""
import os
import subprocess
import threading
import time

import config


def run_solver(executable, work, script, timeout, log_path, cancel_event=None, max_log_bytes=None):
    limit = config.MAX_LOG_BYTES if max_log_bytes is None else max_log_bytes
    if limit < 1024:
        raise ValueError('Log budget must be at least 1024 bytes.')
    error = []
    process = None

    def kill():
        try:
            process.kill()
        except OSError:
            pass

    with open(log_path, 'wb', buffering=0) as log:
        process = subprocess.Popen([executable], cwd=work, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

        def read_output():
            written = 0
            try:
                while True:
                    chunk = process.stdout.read(65536)
                    if not chunk:
                        break
                    allowed = max(0, limit - 512 - written)
                    log.write(chunk[:allowed])
                    written += min(len(chunk), allowed)
                    if len(chunk) > allowed:
                        error.append(f'XFOIL log exceeded {limit} bytes; output truncated and solver stopped.')
                        kill()
                        break
            except OSError as exc:
                error.append(f'Cannot write solver diagnostics: {exc}')
                kill()

        reader = threading.Thread(target=read_output, name='xfoil-log', daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        try:
            try:
                process.stdin.write(script.encode('ascii'))
                process.stdin.close()
            except BrokenPipeError:
                pass  # The exit code and captured diagnostic will describe an early exit.
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    error.append('XFOIL cancelled.')
                    kill()
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    error.append(f'XFOIL timed out after {timeout} seconds.')
                    kill()
                    break
                try:
                    process.wait(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    pass
            process.wait()
        except BaseException:
            kill()
            process.wait()
            raise
        finally:
            reader.join()
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()
        return {'returncode': process.returncode,
                'error': error[0] if error else (f'XFOIL exited with code {process.returncode}.'
                                                if process.returncode else '')}
