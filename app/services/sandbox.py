# -*- coding: utf-8 -*-
import os
import subprocess
from typing import List, Optional

# Em Linux aplicamos limites de CPU/RAM via resource; em Windows/macOS, só timeout.
try:
    import resource  # type: ignore
    _HAS_RESOURCE = True
except Exception:
    _HAS_RESOURCE = False

def run_in_sandbox(
    cmd: List[str],
    *,
    cwd: Optional[str] = None,
    timeout: int = 60,
    cpu_seconds: int = 30,
    mem_mb: int = 512,
    nice: int = 10,
) -> subprocess.CompletedProcess:
    """
    Executa um comando 'cmd' com:
      - timeout (todas as plataformas)
      - prioridade reduzida (Linux)
      - limites de CPU (segundos) e memória virtual (MB) (Linux)
    NÃO levanta CalledProcessError automaticamente: retorna CompletedProcess
    com stdout/stderr e returncode (check=False). Timeouts ainda levantam
    TimeoutExpired.
    """

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

    return subprocess.run(
        cmd,
        cwd=cwd,
        check=False,                  # <- importante para o service inspecionar returncode/stderr
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        preexec_fn=_limits if (os.name == "posix") else None,
        text=True,
    )