"""Runtime settings, read from the environment on each call so tests can override them."""
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DETECTOR_MODES = ("auto", "claude", "rules")
GATE_MODES = ("off", "once", "strict")


@dataclass(frozen=True)
class Settings:
    home: Path
    host: str
    port: int
    detector: str
    model: str
    gate: str
    llm_timeout: float

    @property
    def db_path(self) -> Path:
        return self.home / "align.db"

    @property
    def token_path(self) -> Path:
        return self.home / "token"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def settings() -> Settings:
    detector = os.environ.get("ALIGN_DETECTOR", "auto")
    gate = os.environ.get("ALIGN_GATE", "off")
    return Settings(
        home=Path(os.environ.get("ALIGN_HOME", ROOT / ".align")),
        host="127.0.0.1",  # Never bind publicly: the API has only a local shared-secret token.
        port=int(os.environ.get("ALIGN_PORT", "8765")),
        detector=detector if detector in DETECTOR_MODES else "auto",
        model=os.environ.get("ALIGN_MODEL", "claude-opus-5-5"),
        gate=gate if gate in GATE_MODES else "off",
        llm_timeout=float(os.environ.get("ALIGN_LLM_TIMEOUT", "20")),
    )


def ensure_token(s: Settings | None = None) -> str:
    """Return the local API token, creating it on first use."""
    s = s or settings()
    env = os.environ.get("ALIGN_TOKEN")
    if env:
        return env
    s.home.mkdir(parents=True, exist_ok=True)
    if not s.token_path.exists():
        s.token_path.write_text(secrets.token_urlsafe(24))
        s.token_path.chmod(0o600)
    return s.token_path.read_text().strip()
