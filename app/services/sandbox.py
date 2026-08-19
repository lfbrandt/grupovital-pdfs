# -*- coding: utf-8 -*-
import os
import signal
import subprocess
import tempfile
from typing import List, Mapping, Optional

# Em Linux aplicamos limites via resource; nas demais plataformas, timeout.
try:
    import resource  # type: ignore
    _HAS_RESOURCE = True
except Exception:
    _HAS_RESOURCE = False

def run_in_sandbox(
    cmd: List[str],
    *,
    cwd: Optional[str] = None,
    timeout: float = 60,
    cpu_seconds: int = 30,
    mem_mb: int = 512,
    nice: int = 10,
    env: Optional[Mapping[str, str]] = None,
    file_mb: Optional[int] = None,
    max_processes: Optional[int] = None,
    output_limit_chars: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """
    Executa um comando 'cmd' com:
      - timeout (todas as plataformas)
      - prioridade reduzida (Linux)
      - limites de CPU (segundos) e memória virtual (MB) (Linux)
      - sessão/grupo próprio para limitar o encerramento ao processo atual
    NÃO levanta CalledProcessError automaticamente: retorna CompletedProcess
    com stdout/stderr e returncode (check=False). Timeouts ainda levantam
    TimeoutExpired.
    """

    if not isinstance(cmd, list) or not cmd or not all(
        isinstance(part, str) for part in cmd
    ):
        raise TypeError("cmd deve ser uma lista não vazia de strings")
    try:
        effective_timeout = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout inválido") from exc
    if effective_timeout <= 0:
        raise ValueError("timeout deve ser positivo")

    def _limits():
        # Reduz prioridade (Linux)
        try:
            os.nice(nice)
        except Exception:
            pass

        # Limites de CPU e memória (apenas Linux)
        if _HAS_RESOURCE:
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            except Exception:
                pass
            try:
                max_bytes = mem_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))
            except Exception:
                pass
            if file_mb is not None:
                try:
                    max_file_bytes = max(1, int(file_mb)) * 1024 * 1024
                    resource.setrlimit(
                        resource.RLIMIT_FSIZE,
                        (max_file_bytes, max_file_bytes),
                    )
                except Exception:
                    pass
            if max_processes is not None and hasattr(resource, "RLIMIT_NPROC"):
                try:
                    process_limit = max(1, int(max_processes))
                    resource.setrlimit(
                        resource.RLIMIT_NPROC,
                        (process_limit, process_limit),
                    )
                except Exception:
                    pass

    popen_kwargs = {
        "cwd": cwd,
        "preexec_fn": _limits if (os.name == "posix") else None,
        "env": dict(env) if env is not None else None,
        "shell": False,
        "start_new_session": os.name == "posix",
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0,
        )

    def _stop_execution(proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.terminate()
                except OSError:
                    pass
            try:
                proc.wait(timeout=0.5)
                return
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.kill()
                    except OSError:
                        pass
        else:
            # CREATE_NEW_PROCESS_GROUP is scoped to this execution. CTRL_BREAK is
            # best-effort on Windows; GUI children may not handle console events.
            ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
            if ctrl_break is not None:
                try:
                    os.kill(proc.pid, ctrl_break)
                    proc.wait(timeout=0.3)
                    return
                except (OSError, subprocess.TimeoutExpired):
                    pass
            try:
                proc.terminate()
                proc.wait(timeout=0.3)
                return
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass

    def _communicate_or_timeout(
        proc: subprocess.Popen,
    ):
        try:
            return proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired as exc:
            _stop_execution(proc)
            try:
                stdout, stderr = proc.communicate(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                stdout, stderr = exc.output, exc.stderr
            raise subprocess.TimeoutExpired(
                cmd,
                effective_timeout,
                output=stdout,
                stderr=stderr,
            ) from None

    if output_limit_chars is None:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **popen_kwargs,
        )
        stdout, stderr = _communicate_or_timeout(proc)
        return subprocess.CompletedProcess(
            cmd,
            proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )

    limit = max(0, int(output_limit_chars))
    with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(
        mode="w+b"
    ) as stderr_file:
        proc = subprocess.Popen(
            cmd,
            stdout=stdout_file,
            stderr=stderr_file,
            text=False,
            **popen_kwargs,
        )
        _communicate_or_timeout(proc)
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read(limit).decode("utf-8", errors="replace")
        stderr = stderr_file.read(limit).decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(
            cmd,
            proc.returncode,
            stdout=stdout,
            stderr=stderr,
        )
