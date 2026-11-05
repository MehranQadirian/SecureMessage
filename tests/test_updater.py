import hashlib, sys, threading, http.server, socketserver, json, os
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import updater as up


def test_version_order():
    assert up.is_newer("v2.0.1", "2.0.0") and up.is_newer("2.1.0", "2.0.9") and up.is_newer("3.0.0", "2.9.9")
    assert not up.is_newer("2.0.0", "2.0.0") and not up.is_newer("1.9.9", "2.0.0")
    assert up.is_newer("2.0.0", "2.0.0-beta.1") and not up.is_newer("2.0.0-beta.1", "2.0.0")
    assert not up.is_newer("garbage", "2.0.0")


ASSETS = {
    "Secure-Message-2.1.0-Win-x64.msi": "u/msi", "Secure-Message-2.1.0-amd64.deb": "u/deb",
    "Secure-Message-2.1.0-x86_64.rpm": "u/rpm", "Secure-Message-2.1.0-x86_64.AppImage": "u/ai",
    "SHA256SUMS.txt": "u/sums",
}


@pytest.mark.parametrize("flavour,name", [("windows", "Secure-Message-2.1.0-Win-x64.msi"),
                                          ("deb", "Secure-Message-2.1.0-amd64.deb"),
                                          ("rpm", "Secure-Message-2.1.0-x86_64.rpm"),
                                          ("appimage", "Secure-Message-2.1.0-x86_64.AppImage")])
def test_pick_asset(flavour, name):
    assert up.pick_asset(flavour, ASSETS, "x86_64")[0] == name


def test_pick_asset_unsupported():
    assert up.pick_asset("deb", ASSETS, "aarch64") == (None, None)
    assert up.pick_asset("source", ASSETS, "x86_64") == (None, None)


def test_parse_checksums():
    h = "a" * 64
    assert up.parse_checksums(f"{h}  file.msi\n{h} *other.deb\njunk") == {"file.msi": h, "other.deb": h}


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


def _serve(root):
    os.chdir(root)
    srv = socketserver.TCPServer(("127.0.0.1", 0), _Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _update(base, name, payload, sums):
    rel = up.Release("2.1.0", "", "p", {name: f"{base}/{name}", up.CHECKSUMS_NAME: f"{base}/{up.CHECKSUMS_NAME}"})
    return up.Update(rel, "deb", name, f"{base}/{name}")


def test_download_verifies(tmp_path):
    srv_dir = tmp_path / "srv"; srv_dir.mkdir()
    name = "Secure-Message-2.1.0-amd64.deb"; data = os.urandom(300_000)
    (srv_dir / name).write_bytes(data)
    (srv_dir / "SHA256SUMS.txt").write_text(f"{hashlib.sha256(data).hexdigest()}  {name}\n")
    cwd = os.getcwd(); srv, base = _serve(srv_dir)
    try:
        seen = []
        path = up.download(_update(base, name, data, None), lambda f, t: seen.append(f), dest_dir=tmp_path / "dl")
        assert path.read_bytes() == data and seen[-1] == 1.0
        (srv_dir / name).write_bytes(data + b"tampered")
        with pytest.raises(up.UpdateError, match="checksum"):
            up.download(_update(base, name, data, None), dest_dir=tmp_path / "dl2")
        assert not (tmp_path / "dl2" / name).exists()
    finally:
        srv.shutdown(); os.chdir(cwd)
