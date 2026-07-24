import io
import tarfile

from rufo_control_plane.gcp.source_build import inject_dockerfile


def _make_tar(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _read_member(tar_bytes: bytes, name: str) -> bytes | None:
    tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz")
    try:
        member = tar.getmember(name)
    except KeyError:
        return None
    extracted = tar.extractfile(member)
    return extracted.read() if extracted else None


def test_inject_dockerfile_adds_dockerfile_at_root():
    original = _make_tar({"rufo.toml": b"[agent]\nname='x'\n", "agent.py": b"agent = None\n"})
    packed = inject_dockerfile(original, "FROM python:3.11-slim\n")
    assert _read_member(packed, "Dockerfile") == b"FROM python:3.11-slim\n"


def test_inject_dockerfile_preserves_original_files():
    original = _make_tar({"rufo.toml": b"[agent]\nname='x'\n", "agent.py": b"agent = None\n"})
    packed = inject_dockerfile(original, "FROM python:3.11-slim\n")
    assert _read_member(packed, "rufo.toml") == b"[agent]\nname='x'\n"
    assert _read_member(packed, "agent.py") == b"agent = None\n"


def test_inject_dockerfile_replaces_customer_provided_dockerfile():
    original = _make_tar({"rufo.toml": b"[agent]\n", "Dockerfile": b"FROM scratch\n"})
    packed = inject_dockerfile(original, "FROM python:3.11-slim\n")
    assert _read_member(packed, "Dockerfile") == b"FROM python:3.11-slim\n"
