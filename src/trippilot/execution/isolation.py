"""OS-enforced file access: macOS Seatbelt or Linux Landlock (fail closed)."""
import ctypes
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[3]


def read_paths(worker):
    upstream = ROOT / "upstream/ChinaTravel"
    paths = [Path(sys.prefix), Path(sys.base_prefix), ROOT / "src", Path(worker),
             ROOT / "scripts/restricted_worker.py", upstream / "agent_env",
             upstream / "chinatravel/environment", upstream / "chinatravel/evaluation/output_schema.json"]
    for path in ["/System", "/usr", "/bin", "/sbin", "/Library", "/private/etc", "/lib", "/lib64", "/etc", "/dev", "/proc/cpuinfo", "/proc/meminfo"]:
        if Path(path).exists():
            paths.append(Path(path))
    return list(dict.fromkeys(str(p.resolve()) for p in paths if p.exists()))


def prepare(command, directory):
    """Build a policy with read-only code/data and one writable task directory."""
    directory = Path(directory).resolve()
    temporary = directory / "tmp"
    temporary.mkdir(exist_ok=True)
    allowed = {'PATH','LANG','LC_ALL','LC_CTYPE','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
               'http_proxy','https_proxy','all_proxy','no_proxy','CHINATRAVEL_OPENAI_API_KEY',
               'CHINATRAVEL_OPENAI_BASE_URL','CHINATRAVEL_OPENAI_MODEL'}
    env = {key:value for key,value in os.environ.items() if key in allowed}
    env.update(PYTHONDONTWRITEBYTECODE="1",PYTHONNOUSERSITE="1",TRIPPILOT_RESTRICTED="1",
               OPENBLAS_NUM_THREADS="1",OMP_NUM_THREADS="1",HOME=str(directory),
               TMPDIR=str(temporary),TMP=str(temporary),TEMP=str(temporary))
    env.pop("TRIPPILOT_CONFIG_FILE",None)
    reads = read_paths(command[1])
    spec = {"read": reads, "write": [str(directory)], "platform": platform.system()}
    spec_path = directory / "access-policy.json"
    spec_path.write_text(json.dumps(spec, indent=2))
    if platform.system() == "Darwin":
        # Restrict file content while leaving OS runtime services intact.
        readable = []
        for path in reads:
            selector = "subpath" if Path(path).is_dir() else "literal"
            readable.append("(%s %s)" % (selector, json.dumps(path)))
        readable.append("(subpath %s)" % json.dumps(str(directory)))
        for parent in [ROOT, ROOT / "upstream", ROOT / "upstream/ChinaTravel", ROOT / "upstream/ChinaTravel/chinatravel"]:
            readable.append("(literal %s)" % json.dumps(str(parent)))
        rules = ["(version 1)", "(allow default)",
                 '(deny file-read-data (require-all (subpath "/Users") (require-not (require-any %s))))' % " ".join(readable),
                 '(deny file-write* (require-not (require-any (subpath %s) (literal "/dev/null") (literal "/dev/tty"))))' % json.dumps(str(directory))]
        return ["/usr/bin/sandbox-exec", "-p", "\n".join(rules), *command], env
    if platform.system() == "Linux":
        return [command[0], str(ROOT / "scripts/restricted_worker.py"), str(spec_path), *command[1:]], env
    raise RuntimeError("OS file restrictions are unavailable on this platform")


def enter_landlock(spec):
    """Use the stable Landlock filesystem ABI, before executing user worker code."""
    if platform.machine() not in ("x86_64", "aarch64", "arm64"):
        raise RuntimeError("unsupported Landlock syscall architecture")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 3:
        raise RuntimeError("Linux kernel must enable Landlock ABI >= 3")
    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    handled = (1 << 15) - 1
    fd = libc.syscall(444, ctypes.byref(Ruleset(handled)), ctypes.sizeof(Ruleset), 0)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "create Landlock policy")
    try:
        # READ_DIR alone grants namespace lookup, never file content access.
        for directory in [ROOT, ROOT/'upstream', ROOT/'upstream/ChinaTravel', ROOT/'upstream/ChinaTravel/chinatravel']:
            target=os.open(str(directory),os.O_PATH|os.O_CLOEXEC)
            try:
                rule=Beneath(8,target)
                if libc.syscall(445,fd,1,ctypes.byref(rule),0):raise OSError(ctypes.get_errno(),'add namespace directory')
            finally:os.close(target)
        for writable, paths in [(False, spec["read"]), (True, spec["write"])]:
            for name in paths:
                path = Path(name)
                access = handled if writable else (1 | 4 | (8 if path.is_dir() else 0))
                if not path.is_dir():
                    access &= 1 | 2 | 4 | (1 << 14)
                target = os.open(name, os.O_PATH | os.O_CLOEXEC)
                try:
                    rule = Beneath(access, target)
                    if libc.syscall(445, fd, 1, ctypes.byref(rule), 0):
                        raise OSError(ctypes.get_errno(), "add Landlock path")
                finally:
                    os.close(target)
        if libc.prctl(38, 1, 0, 0, 0) or libc.syscall(446, fd, 0):
            raise OSError(ctypes.get_errno(), "enforce Landlock policy")
    finally:
        os.close(fd)
