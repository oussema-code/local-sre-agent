import os
from dataclasses import dataclass, fields


@dataclass(frozen=True)
class Settings:
    """All configuration. Each field can be overridden by the env var of the same name in upper case."""
    prometheus_url: str = "http://prometheus:9090"
    app_url: str = "http://edge-app:8080"
    app_container: str = "edge-app"
    data_dir: str = "/data"
    config_path: str = "/config/app.yaml"
    state_dir: str = "/var/lib/sre-agent"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5:1.5b"
    llm_timeout_s: float = 60.0
    interval_s: float = 10.0
    latency_threshold_s: float = 0.5
    disk_threshold: float = 0.85
    disk_critical: float = 0.95
    cleanup_target: float = 0.60
    restart_budget: int = 3
    restart_window_s: float = 600.0
    cooldown_s: float = 600.0       # after an escalation, ignore that incident type for this long
    settle_s: float = 45.0          # after a fix, let metrics catch up (Prometheus rate window is 30 s)
    approval_timeout_s: float = 300.0
    verify_attempts: int = 6
    verify_interval_s: float = 5.0
    soak_cycles: int = 6
    dry_run: bool = False
    metrics_port: int = 9200

    @classmethod
    def from_env(cls) -> "Settings":
        kw = {}
        for f in fields(cls):
            raw = os.environ.get(f.name.upper())
            if raw is None:
                continue
            typ = type(f.default)
            kw[f.name] = raw.strip().lower() in ("1", "true", "yes") if typ is bool else typ(raw)
        return cls(**kw)
