#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RADAR 图形化部署助手。

把「本地打包 -> SFTP 上传 -> 远端执行 deploy.sh -> 健康检查」压成一次点击，
面向不想记命令行的使用者。除 paramiko（SSH/SFTP）外只用标准库，界面是
tkinter，Windows / macOS / Linux 都能直接跑。

    python webapp/deploy/deploy_gui.py                  # 打开界面
    python webapp/deploy/deploy_gui.py --selftest       # 只构建界面自检，不进事件循环
    python webapp/deploy/deploy_gui.py --package-only out.tar.gz
                                                        # 只打包，用于核对排除规则

设计取舍：

- **上传而不是 git clone**：服务器在内网、连不上 GitHub 是常态，且本仓库
  还有 152 个未提交文件。打包上传不依赖外网，也顺带绕开了 `.gitignore`
  排除掉 `webapp/frontend/dist/` 的问题——前端产物必须带上，否则服务器
  没有 Node 时连界面都出不来。
- **不传模型权重**：`ckpt` 数 GB，走后端下载脚本比 SFTP 快得多，也让
  部署包保持在 10 MB 量级。权重缺失时由 `deploy.sh --download-ckpt` 补。
- **日志用队列回传主线程**：tkinter 不是线程安全的，任何 widget 操作
  都必须在主线程，网络线程只能往队列里塞消息。
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shlex
import sys
import tarfile
import tempfile
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

try:
    import paramiko

    HAS_PARAMIKO = True
except ImportError:  # 首次使用时引导安装，不直接崩掉
    paramiko = None  # type: ignore[assignment]
    HAS_PARAMIKO = False

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path.home() / ".radar_deploy.json"

# ---------------------------------------------------------------- 打包规则

#: 相对项目根、以 `/` 结尾的排除前缀。用前缀而不是目录名匹配，避免误伤
#: 同名目录（例如将来 frontend 里出现一个叫 data 的资源目录）。
_EXCLUDE_PREFIXES = (
    ".git/",
    ".venv/",
    ".venv_preview/",
    ".playwright-cli/",
    "webapp/runtime/",
    "webapp/frontend/node_modules/",
    "training_results/",
    # 注意：这里**不能**排除整个 DAMO-RADAR/ckpt/。该目录下除了从 HuggingFace
    # 下载的大权重，还有随本仓库分发的模型资产（infer_text_embedding_radar.pt，
    # 约 0.33 MB，以及报告模板 JSON）——那个 .pt 在 HF 上根本不存在，排除了就
    # 只能靠人工拷贝。大权重由 _EXCLUDE_SUFFIXES 里的 .pth/.bin/.safetensors 挡住。
    "DAMO-RADAR/data/",
    "DAMO-RADAR/results/",
    "DAMO-RADAR/bert-base-chinese/",
    "DAMO-RADAR/bert-base-uncased/",
)

_EXCLUDE_NAMES = {"__pycache__", ".DS_Store", "Thumbs.db", ".pytest_cache"}

#: 权重、日志、编译产物：体积大或与运行环境绑定，传上去没有意义
_EXCLUDE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".err",
    ".pth",
    ".bin",
    ".safetensors",
    ".part",
}

#: 打包后必须存在的关键文件，缺任何一个都会让远端部署失败得体无完肤
_REQUIRED_IN_PACKAGE = (
    "webapp/backend/main.py",
    "webapp/backend/requirements.txt",
    "webapp/deploy/deploy.sh",
    "webapp/deploy/Dockerfile",
    "webapp/deploy/docker-compose.yml",
)

#: 远端装 huggingface_hub 时走的 pip 源。PyPI 直连在国内经常只有几十 KB/s
#: 甚至直接超时，而这一步失败会让整个部署中止，所以默认走清华镜像。
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _is_excluded(rel: str) -> bool:
    """`rel` 是 POSIX 相对路径，目录需带结尾 `/`。"""
    if any(rel == p or rel.startswith(p) for p in _EXCLUDE_PREFIXES):
        return True

    name = rel.rstrip("/").rsplit("/", 1)[-1]
    if name in _EXCLUDE_NAMES:
        return True

    if rel.endswith("/"):
        return False
    return Path(rel).suffix.lower() in _EXCLUDE_SUFFIXES


def iter_payload(root: Path = ROOT) -> Iterator[tuple[Path, str]]:
    """遍历待上传文件，产出 (绝对路径, 包内相对路径)。

    用 os.walk 而不是 Path.rglob，因为需要在进入目录前剪枝——`node_modules`
    动辄几万文件，逐个判断再排除会白等很久。
    """
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root).as_posix()
        prefix = "" if rel_dir == "." else rel_dir + "/"

        dirnames[:] = sorted(d for d in dirnames if not _is_excluded(prefix + d + "/"))

        for name in sorted(filenames):
            rel = prefix + name
            if not _is_excluded(rel):
                yield Path(dirpath) / name, rel


@dataclass
class PackageStats:
    files: int = 0
    raw_bytes: int = 0
    archive_bytes: int = 0
    archive: Path | None = None
    top_level: dict[str, int] = field(default_factory=dict)


def build_package(out: Path, root: Path = ROOT, on_progress: Callable[[str], None] | None = None) -> PackageStats:
    """把项目打成 tar.gz，返回统计信息。

    不做符号链接与权限保留：上传的是源码，远端解压后由 deploy.sh 自己
    处理执行位，跨平台（Windows 打包、Linux 解压）时省掉一堆兼容问题。
    """
    stats = PackageStats(archive=out)
    out.parent.mkdir(parents=True, exist_ok=True)

    log = on_progress or (lambda _msg: None)

    with tarfile.open(out, "w:gz", compresslevel=6) as tar:
        for path, rel in iter_payload(root):
            try:
                tar.add(path, arcname=rel, recursive=False)
                size = path.stat().st_size
            except OSError as exc:  # 文件被占用/删除，跳过不影响整体
                log(f"跳过 {rel}：{exc}")
                continue

            stats.files += 1
            stats.raw_bytes += size
            top = rel.split("/", 1)[0]
            stats.top_level[top] = stats.top_level.get(top, 0) + size

    stats.archive_bytes = out.stat().st_size

    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())
    missing = [rel for rel in _REQUIRED_IN_PACKAGE if rel not in names]
    if missing:
        raise RuntimeError("打包结果缺少关键文件，部署必然失败：" + "、".join(missing))

    return stats


# ---------------------------------------------------------------- 服务器配置


@dataclass
class ServerConfig:
    host: str = ""
    port: int = 22
    user: str = "root"
    auth: str = "password"  # password | key
    password: str = ""
    key_path: str = ""
    key_password: str = ""
    remote_dir: str = "RadarServe"
    mode: str = "docker"  # docker | native | macos
    service_port: int = 8000
    upload_code: bool = True
    download_ckpt: bool = False
    hf_mirror: bool = True
    pip_mirror: bool = True
    use_sudo: bool = False
    sudo_password: str = ""
    remember_password: bool = False


# ---------------------------------------------------------------- SSH 执行


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

def _progress_percent(text: str) -> str | None:
    """从进度条行里提取百分比；不是进度行则返回 None。

    两种格式都要认。它们都会用 \\r 反复刷新同一行，原样写进日志会把日志区刷爆，
    所以统一识别出来、只送状态栏：

    - huggingface_hub（tqdm 风格）：`Downloading x.pth: 45%|████ | 1.2G/2.6G`
    - pip：`  ━━━━━━━━━ 45% 1.2/2.6 GB 2.1 MB/s eta 0:10:30`
    """
    if "%|" in text:
        match = re.search(r"(\d{1,3})%\|", text)
        return match.group(1) if match else None

    if any(mark in text for mark in ("━━", "B/s", "eta ")):
        match = re.search(r"(\d{1,3})%", text)
        return match.group(1) if match else None

    return None


def _fmt_duration(seconds: float) -> str:
    total = int(max(seconds, 0))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"


def _load_private_key(path: str, password: str):
    """按常见类型依次尝试加载私钥，全部失败才报错。

    paramiko 没有统一的自动探测入口（新版 `PKey.from_path` 覆盖不全），
    手动依次尝试反而最省事，错误信息也更具体。
    """
    errors: list[str] = []
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key_file(path, password=password or None)
        except paramiko.PasswordRequiredException:
            raise RuntimeError("私钥已加密，请填写密钥口令") from None
        except Exception as exc:  # 类型不匹配属正常，继续试下一个
            errors.append(f"{cls.__name__}: {exc}")

    raise RuntimeError("无法解析私钥：\n  " + "\n  ".join(errors))


class RemoteError(RuntimeError):
    """远端命令返回非零，带上退出码与最后几行输出。"""


class Deployer:
    """一次部署会话。方法只能被单个线程串行调用。"""

    def __init__(self, on_line: Callable[[str], None]):
        self.client = None
        self.sftp = None
        self._on_line = on_line

    # ------------------------------------------------------------ 连接
    def connect(self, cfg: ServerConfig) -> None:
        client = paramiko.SSHClient()
        # 首次连接自动信任主机公钥。内网自建服务器没有已知主机记录，
        # 不这样设会直接抛 HostKeyUnknown。
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kw: dict = {
            "hostname": cfg.host,
            "port": int(cfg.port),
            "username": cfg.user,
            "timeout": 20,
            "banner_timeout": 25,
            "auth_timeout": 25,
        }
        if cfg.auth == "key":
            kw["pkey"] = _load_private_key(cfg.key_path, cfg.key_password)
        else:
            kw["password"] = cfg.password
            # 关掉本地密钥/agent 探测，否则密码错时会静默改试本机密钥，
            # 报出来的错误和实际原因对不上
            kw["look_for_keys"] = False
            kw["allow_agent"] = False

        client.connect(**kw)
        self.client = client
        self.sftp = client.open_sftp()

    def close(self) -> None:
        for obj in (self.sftp, self.client):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self.sftp = None
        self.client = None

    # ------------------------------------------------------------ 执行
    def run(
        self,
        command: str,
        on_line: Callable[[str], None] | None = None,
        sudo_password: str = "",
        check: bool = True,
    ) -> int:
        """执行远端命令并流式回传输出，返回退出码。

        开 PTY：不开的话很多程序会按管道全缓冲，进度长时间看不到。
        `sudo` 场景统一用 `sudo -S` 从 stdin 读密码——把密码写进命令行会
        出现在 `ps` 里，比这危险得多。
        """
        if self.client is None:
            raise RuntimeError("尚未连接服务器")

        sink = on_line or self._on_line
        stdin, stdout, stderr = self.client.exec_command(command, get_pty=True, timeout=None)

        if sudo_password:
            stdin.write(sudo_password + "\n")
            stdin.flush()

        chan = stdout.channel
        pending = b""

        def emit(raw: bytes) -> None:
            text = _ANSI_RE.sub("", raw.decode("utf-8", "replace"))
            text = text.rstrip("\r\n")
            if "\r" in text:
                # `\r` 是覆盖式刷新（进度条），保留最后一段才不会被刷屏
                text = text.split("\r")[-1]
            if text.strip():
                sink(text)

        while not chan.exit_status_ready() or chan.recv_ready():
            if chan.recv_ready():
                chunk = chan.recv(65536)
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    emit(line)
            else:
                time.sleep(0.05)

        if pending:
            emit(pending)

        code = chan.recv_exit_status()
        if check and code != 0:
            raise RemoteError(f"远端命令失败（退出码 {code}）")
        return code

    def run_capture(self, command: str) -> str:
        """执行命令并返回合并后的输出，用于探测环境这类短命令。"""
        if self.client is None:
            raise RuntimeError("尚未连接服务器")
        _stdin, stdout, _stderr = self.client.exec_command(command, timeout=60)
        return stdout.read().decode("utf-8", "replace").strip()

    def home(self) -> str:
        if self.sftp is None:
            raise RuntimeError("尚未连接服务器")
        home = (self.sftp.normalize(".") or "").strip()
        # 个别 SFTP 实现（某些跳板机、容器化 sshd）对 REALPATH '.' 会返回 '/'，
        # 照它拼路径等于往根目录写，报错还很难懂，这里直接拦下
        if not home.startswith("/") or home == "/":
            raise RuntimeError(
                f"无法确定服务器家目录（REALPATH '.' 返回 {home!r}）；"
                "请把「部署目录」填成绝对路径，例如 /opt/RadarServe"
            )
        return home

    def resolve_dir(self, remote_dir: str) -> str:
        """把部署目录规整成绝对路径。

        两种输入都要处理：

        - `~` 开头的路径：SFTP 的路径接口不认 `~`，不展开的话会真的创建一个
          名为 `~` 的目录；
        - 纯相对路径（如 `RadarServe`）：不能直接交给服务端按 cwd 解析——
          SFTP 子系统的 cwd 与 exec 登录后的 cwd 未必一致，会出现"目录建好了
          但文件传到别处"，症状是远端报 No such file，很难查。统一基于家目录
          展开，行为就确定了。
        """
        path = (remote_dir or "").strip()
        if not path:
            raise RuntimeError("请填写服务器上的部署目录")
        if path == "~":
            return self.home()
        if path.startswith("~/"):
            return self.home().rstrip("/") + "/" + path[2:]
        if not path.startswith("/"):
            return self.home().rstrip("/") + "/" + path
        return path

    # ------------------------------------------------------------ 探测
    def probe(self, cfg: ServerConfig) -> dict[str, str]:
        """收集环境信息，用于在连接测试里给出一份「体检报告」。"""
        info: dict[str, str] = {}
        info["系统"] = self.run_capture("uname -srm 2>/dev/null || uname -a") or "未知"

        if self.run_capture("command -v docker >/dev/null 2>&1 && echo yes") == "yes":
            version = self.run_capture("docker --version 2>/dev/null")
            info["Docker"] = version or "已安装"
            if self.run_capture("docker compose version >/dev/null 2>&1 && echo yes") == "yes":
                info["Compose"] = "v2 插件可用"
            elif self.run_capture("docker-compose --version >/dev/null 2>&1 && echo yes") == "yes":
                info["Compose"] = "v1 独立版可用"
            else:
                info["Compose"] = "缺失（deploy.sh 需要 docker compose）"
        else:
            info["Docker"] = "未安装"

        if self.run_capture("command -v nvidia-smi >/dev/null 2>&1 && echo yes") == "yes":
            gpu = self.run_capture(
                "nvidia-smi --query-gpu=name,memory.total,driver_version "
                "--format=csv,noheader 2>/dev/null | head -n 3"
            )
            info["GPU"] = gpu or "nvidia-smi 存在但读不到信息"
            info["CUDA"] = self.run_capture(
                "nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1"
            )
        else:
            info["GPU"] = "未检测到（只能 CPU 推理，单例可能数十分钟）"

        if cfg.mode in ("native", "macos"):
            info["Python"] = self.run_capture(
                "python3 -c 'import sys;print(\"%d.%d.%d\"%sys.version_info[:3])' 2>/dev/null || echo 未安装"
            )
            info["Node"] = self.run_capture("node --version 2>/dev/null || echo 未安装")

        # 部署目录首次部署时还不存在，而 df 对不存在的路径直接失败；
        # 原写法用 `|| ` 兜底是无效的——管道末尾的 awk 无论如何都返回 0，
        # `||` 根本不会触发，结果磁盘信息永远是空的。改成先沿父目录向上
        # 找到第一个真实存在的目录再查。
        target = self.resolve_dir(cfg.remote_dir)
        info["磁盘可用"] = self.run_capture(
            f"d={shlex.quote(target)}; until [ -d \"$d\" ]; do d=$(dirname \"$d\"); done; "
            "df -h \"$d\" 2>/dev/null | tail -n 1 | awk '{print $4\" 可用 / \"$2\" 总量\"}'"
        )
        info["已部署"] = (
            "是"
            if self.run_capture(
                f"test -f {shlex.quote(target)}/webapp/deploy/deploy.sh && echo yes"
            )
            == "yes"
            else "否"
        )
        return info

    # ------------------------------------------------------------ 传输
    def upload(self, local: Path, remote: str, on_progress: Callable[[int, int], None]) -> None:
        if self.sftp is None:
            raise RuntimeError("尚未连接服务器")
        self.sftp.put(str(local), remote, callback=on_progress)

    def mkdirs(self, path: str) -> None:
        self.run(f"mkdir -p {shlex.quote(path)}")


# ---------------------------------------------------------------- 部署流程


def build_deploy_command(cfg: ServerConfig, remote_dir: str, archive_name: str) -> str:
    """拼出远端要执行的整条命令。

    解压用 `tar -xzf`：包内是相对路径，覆盖已有文件但不删其他内容，所以服务器上
    已下载的 `ckpt` 不会被这次上传清掉。

    勾了 sudo 时**整条命令**都交给 sudo，不只是 deploy.sh。因为只要之前用 root
    跑过一次，`ckpt/` 这类目录就归了 root，普通用户连解压都写不进去（tar 报一串
    Permission denied）。另外 sudo 默认会重置环境变量，所以 export 必须放在
    `bash -c` 内部，不能靠外层 export。
    """
    steps = [
        f"cd {shlex.quote(remote_dir)}",
        f"tar -xzf {shlex.quote(archive_name)}",
        f"rm -f {shlex.quote(archive_name)}",
    ]

    exports = []
    if cfg.hf_mirror:
        exports.append("export HF_ENDPOINT=https://hf-mirror.com")
    if cfg.pip_mirror:
        exports.append(f"export PIP_INDEX_URL={PIP_MIRROR}")
    exports.append(f"export PORT={int(cfg.service_port)}")

    script = f"bash webapp/deploy/deploy.sh {cfg.mode}"
    if cfg.download_ckpt:
        script += " --download-ckpt"

    inner = " && ".join(steps + exports + [script])
    if cfg.use_sudo:
        # -p '' 去掉密码提示；密码由 Deployer.run 从 stdin 写入
        return f"sudo -S -p '' bash -c {shlex.quote(inner)}"
    return inner


@dataclass
class DeployResult:
    ok: bool
    message: str
    url: str = ""


class DeployTask:
    """把部署流程跑在线程里，通过 post 回调向界面回报。

    分成若干步而不是一个大函数，是为了让界面能逐条显示「正在做什么」，
    出错时也容易指出停在哪一步。
    """

    #: 部署阶段划分，给界面一个「第几步 / 共几步」的进度。上传与远端执行
    #: 这两步耗时最长，进度条在这两步里还会细分推进。
    STAGES = (
        "连接服务器",
        "检查服务器环境",
        "打包本地代码",
        "上传到服务器",
        "在服务器上执行 deploy.sh",
        "验证服务",
    )

    def __init__(self, cfg: ServerConfig, post: Callable[..., None]):
        self.cfg = cfg
        self.post = post
        self.local_archive: Path | None = None
        self._stage = 0
        self._total = len(self.STAGES)

    def _log(self, text: str, level: str = "info") -> None:
        # 下载进度条（huggingface_hub 的 tqdm、pip 的进度条）只送状态栏，不进日志区
        percent = _progress_percent(text)
        if percent is not None:
            self.post("notice", text=f"下载中 {percent}%")
            return

        # deploy.sh 自己的 `==> xxx` 阶段标题，映射成子状态显示，
        # 这样部署跑几分钟时用户能看出当前卡在哪个子步骤
        if text.startswith("==> "):
            self.post("substage", text=text[4:].strip())
        self.post("log", text=text, level=level)

    def _step(self, index: int, text: str) -> None:
        self._stage = index
        self.post("stage", index=index, total=self._total, text=text)
        # 进入第 n 步说明前 n-1 步已经做完，进度条先推到那个位置，
        # 免得出现"阶段跳到第 5 步、进度条却还停在 20%"这种自相矛盾
        self.post("progress", value=(index - 1) * 100.0 / self._total)

    def _progress(self, value: float | None) -> None:
        """把阶段内进度折算成总进度，保证进度条整体单调递增。"""
        if self._total <= 0:
            return
        span = 100.0 / self._total
        base = max(self._stage - 1, 0) * span
        inner = 0.0 if value is None else max(0.0, min(100.0, value))
        self.post("progress", value=base + inner * span / 100.0)

    # ------------------------------------------------------------ 入口
    def run(self, upload_only: bool = False) -> DeployResult:
        cfg = self.cfg
        # 只上传时后面两步不会走，总步数要相应减少，否则进度条永远到不了 100%
        self._total = 4 if upload_only else len(self.STAGES)
        deployer = Deployer(lambda text: self._log(text))
        try:
            self._step(1, self.STAGES[0])
            deployer.connect(cfg)
            self._log(f"已连接 {cfg.user}@{cfg.host}:{cfg.port}", "ok")

            remote_dir = deployer.resolve_dir(cfg.remote_dir)
            self._log(f"部署目录：{remote_dir}")
            self._check_writable(deployer, remote_dir, cfg)

            self._step(2, self.STAGES[1])
            info = deployer.probe(cfg)
            for key, value in info.items():
                level = "warn" if "未" in value and key in ("Docker", "GPU") else "info"
                self._log(f"  {key}：{value}", level)

            if cfg.mode == "docker" and info.get("Docker") == "未安装":
                raise RuntimeError(
                    "docker 模式要求服务器已安装 Docker + NVIDIA Container Toolkit；"
                    "若不想装容器，可把模式改成 native"
                )
            if cfg.mode in ("native", "macos") and info.get("Python") == "未安装":
                raise RuntimeError("native 模式要求服务器已装 Python 3.10+")

            if not cfg.upload_code:
                self._log("已跳过代码上传，直接复用服务器上的现有代码", "warn")
            else:
                self._package()
                self._upload(deployer, remote_dir)

            if upload_only:
                self._progress(100.0)
                return DeployResult(True, "代码已上传完成", self._site_url(cfg))

            self._remote_deploy(deployer, remote_dir)
            self._verify(deployer, cfg)
            self._progress(100.0)
            return DeployResult(True, "部署完成", self._site_url(cfg))

        except Exception as exc:
            return DeployResult(False, f"{type(exc).__name__}: {exc}")
        finally:
            deployer.close()
            self._cleanup()

    # ------------------------------------------------------------ 各步骤
    def _check_writable(
        self, deployer: Deployer, remote_dir: str, cfg: ServerConfig
    ) -> None:
        """上传前先确认目标目录可写。

        不查的话症状是 tar 抛一串 `Cannot open: Permission denied`，用户很难联想到
        "目录归 root"——而这恰恰是用过 sudo 部署之后最常见的残留。所以提前查，
        并把两种解法都摆出来。
        """
        if cfg.use_sudo:
            # 整条远端命令都以 root 执行，对登录用户做写权限检查没有意义
            return

        # 目录可能还不存在（首次部署），沿父目录向上找到第一个存在的再测
        probe = (
            f"d={shlex.quote(remote_dir)}; until [ -d \"$d\" ]; do d=$(dirname \"$d\"); done; "
            "test -w \"$d\" && echo YES || echo NO"
        )
        if deployer.run_capture(probe) == "YES":
            return

        owner = deployer.run_capture(
            f"stat -c '%U:%G %a' {shlex.quote(remote_dir)} 2>/dev/null || echo 未知"
        )
        who = deployer.run_capture("id -un")
        raise RuntimeError(
            f"部署目录不可写：{remote_dir}（属主 {owner}，当前用户 {who}）。\n"
            "多半是之前用 sudo / root 部署过，目录被 root 占住了。两种解法：\n"
            "  1) 勾选「远端使用 sudo」，让上传与解压同样以 root 执行；\n"
            "  2) 或在服务器上把目录交还给当前用户：\n"
            f"     sudo chown -R $(id -un):$(id -gn) {shlex.quote(remote_dir)}"
        )

    def _package(self) -> None:
        self._step(3, self.STAGES[2])
        if not (ROOT / "webapp/frontend/dist/index.html").exists():
            raise RuntimeError(
                "webapp/frontend/dist/ 不存在，无法上传前端产物。\n"
                "请先在本地执行：cd webapp/frontend && npm run build"
            )

        # 清掉上次异常退出留下的包：任务跑到一半被强杀时 finally 里的清理
        # 执行不到，否则临时目录里会攒下一堆几 MB 的压缩包
        for stale in Path(tempfile.gettempdir()).glob("radar-serve-*.tar.gz"):
            try:
                stale.unlink()
            except OSError:
                pass

        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        out = Path(tempfile.gettempdir()) / f"radar-serve-{stamp}.tar.gz"

        stats = build_package(out, on_progress=lambda msg: self._log(f"  {msg}", "muted"))
        self.local_archive = out

        self._log(
            f"已打包 {stats.files} 个文件，压缩后 {stats.archive_bytes / 1024 / 1024:.1f} MB"
            f"（原始 {stats.raw_bytes / 1024 / 1024:.1f} MB）",
            "ok",
        )
        for name, size in sorted(stats.top_level.items(), key=lambda kv: -kv[1])[:6]:
            self._log(f"  {name:<18} {size / 1024 / 1024:>6.2f} MB", "muted")

    def _upload(self, deployer: Deployer, remote_dir: str) -> None:
        assert self.local_archive is not None
        self._step(4, self.STAGES[3])

        # 压缩包直接落在部署目录里：不依赖家目录探测（跳板机上的 REALPATH 未必可靠），
        # 而且这个目录的写权限本来就是部署的前提
        deployer.mkdirs(remote_dir)
        remote_tmp = f"{remote_dir.rstrip('/')}/{self.local_archive.name}"
        total = self.local_archive.stat().st_size
        state = {"last": 0.0}

        def on_progress(sent: int, size: int) -> None:
            now = time.time()
            # 回调节奏很密，限流到 0.2 秒一次，否则主线程全在处理进度消息
            if now - state["last"] < 0.2 and sent < size:
                return
            state["last"] = now
            self._progress(sent * 100.0 / size if size else 0.0)

        started = time.time()
        deployer.upload(self.local_archive, remote_tmp, on_progress)
        elapsed = max(time.time() - started, 0.001)
        self._log(
            f"上传完成：{total / 1024 / 1024:.1f} MB，耗时 {elapsed:.1f}s"
            f"（{total / 1024 / elapsed:.0f} KB/s）",
            "ok",
        )
        self._progress(100.0)

    def _remote_deploy(self, deployer: Deployer, remote_dir: str) -> None:
        assert self.local_archive is not None
        self._step(5, f"{self.STAGES[4]}（{self.cfg.mode} 模式）")

        command = build_deploy_command(self.cfg, remote_dir, self.local_archive.name)
        self._log(f"$ {command}", "muted")

        # deploy.sh 里的权重下载可能跑很久，这里不设超时
        deployer.run(
            command,
            on_line=lambda text: self._log(text),
            sudo_password=self.cfg.sudo_password if self.cfg.use_sudo else "",
        )
        self._log("deploy.sh 执行完毕", "ok")

    def _verify(self, deployer: Deployer, cfg: ServerConfig) -> None:
        self._step(6, self.STAGES[5])
        port = int(cfg.service_port)
        probe = (
            f"for i in $(seq 1 20); do "
            f"code=$(curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/api/health || true); "
            f'if [ "$code" = "200" ]; then echo "HEALTHY:$code"; exit 0; fi; '
            f"sleep 3; done; echo UNHEALTHY"
        )
        out = deployer.run_capture(probe)

        if "HEALTHY" in out:
            self._log(f"服务已在服务器本机 {port} 端口响应 /api/health", "ok")
            detail = deployer.run_capture(
                f"curl -s http://127.0.0.1:{port}/api/health || true"
            )
            if detail:
                self._log(f"  健康检查：{detail[:400]}", "muted")
        else:
            self._log(
                "服务未在 20 次探测内就绪。常见原因：权重下载耗时较长仍在进行、"
                "容器启动失败、端口被占用。可查看上方 deploy.sh 的输出定位。",
                "warn",
            )

    @staticmethod
    def _site_url(cfg: ServerConfig) -> str:
        return f"http://{cfg.host}:{int(cfg.service_port)}"

    def _cleanup(self) -> None:
        if self.local_archive and self.local_archive.exists():
            try:
                self.local_archive.unlink()
            except OSError:
                pass
        self.local_archive = None


# ---------------------------------------------------------------- 界面

LEVEL_TAGS = {
    "info": ("#334155", None),
    "ok": ("#15803d", None),
    "warn": ("#b45309", None),
    "err": ("#b91c1c", None),
    "muted": ("#94a3b8", None),
}


class DeployApp:
    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title("RADAR 部署助手")
        self.root.geometry("980x760")
        self.root.minsize(860, 640)

        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.busy = False
        self._stage_label = ""  # 形如 `[3/6] 上传到服务器`
        self._stage_started = 0.0  # 当前阶段的起始时刻，用于显示已运行时长

        self.cfg = self._load_config()
        self._build_vars()
        self._build_ui(ttk)
        self._apply_config(self.cfg)
        self.root.after(80, self._drain)
        self.root.after(1000, self._tick_clock)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------ 配置读写
    @staticmethod
    def _load_config() -> dict:
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_config(self) -> None:
        cfg = self._collect(gui_only=True)
        if not self.remember_password.get():
            # 非明文保存密码是标准做法，但这里连混淆都没做，索性不落盘
            cfg["password"] = ""
            cfg["sudo_password"] = ""
            cfg["key_password"] = ""
        try:
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            self._append(f"配置已保存到 {CONFIG_PATH}", "ok")
        except OSError as exc:
            self._append(f"配置保存失败：{exc}", "err")

    def _build_vars(self) -> None:
        tk = self._tk
        self.host = tk.StringVar()
        self.port = tk.StringVar(value="22")
        self.user = tk.StringVar(value="root")
        self.auth = tk.StringVar(value="password")
        self.password = tk.StringVar()
        self.key_path = tk.StringVar()
        self.key_password = tk.StringVar()
        self.remote_dir = tk.StringVar(value="RadarServe")
        self.mode = tk.StringVar(value="docker")
        self.service_port = tk.StringVar(value="8000")
        self.upload_code = tk.BooleanVar(value=True)
        self.download_ckpt = tk.BooleanVar(value=False)
        self.hf_mirror = tk.BooleanVar(value=True)
        self.pip_mirror = tk.BooleanVar(value=True)
        self.use_sudo = tk.BooleanVar(value=False)
        self.sudo_password = tk.StringVar()
        self.remember_password = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="就绪")
        self.sub_text = tk.StringVar(value="")
        self.percent_text = tk.StringVar(value="")
        self.progress = tk.DoubleVar(value=0.0)

    def _apply_config(self, cfg: dict) -> None:
        mapping = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "auth": self.auth,
            "key_path": self.key_path,
            "remote_dir": self.remote_dir,
            "mode": self.mode,
            "service_port": self.service_port,
            "upload_code": self.upload_code,
            "download_ckpt": self.download_ckpt,
            "hf_mirror": self.hf_mirror,
            "pip_mirror": self.pip_mirror,
            "use_sudo": self.use_sudo,
            "remember_password": self.remember_password,
        }
        for key, var in mapping.items():
            value = cfg.get(key)
            if value is None:
                continue
            try:
                var.set(value)
            except Exception:
                pass
        if cfg.get("remember_password"):
            for key, var in (
                ("password", self.password),
                ("key_password", self.key_password),
                ("sudo_password", self.sudo_password),
            ):
                if cfg.get(key):
                    var.set(cfg[key])

    def _collect(self, gui_only: bool = False) -> dict:
        def to_int(value: str, fallback: int) -> int:
            try:
                return int(str(value).strip())
            except ValueError:
                return fallback

        data = {
            "host": self.host.get().strip(),
            "port": to_int(self.port.get(), 22),
            "user": self.user.get().strip(),
            "auth": self.auth.get(),
            "key_path": self.key_path.get().strip(),
            "remote_dir": self.remote_dir.get().strip(),
            "mode": self.mode.get(),
            "service_port": to_int(self.service_port.get(), 8000),
            "upload_code": bool(self.upload_code.get()),
            "download_ckpt": bool(self.download_ckpt.get()),
            "hf_mirror": bool(self.hf_mirror.get()),
            "pip_mirror": bool(self.pip_mirror.get()),
            "use_sudo": bool(self.use_sudo.get()),
            "remember_password": bool(self.remember_password.get()),
        }
        if gui_only:
            # 供保存配置使用：按「记住密码」决定是否带上敏感字段
            data["password"] = self.password.get()
            data["key_password"] = self.key_password.get()
            data["sudo_password"] = self.sudo_password.get()
        return data

    def _server_config(self) -> ServerConfig:
        data = self._collect()
        return ServerConfig(
            **data,
            password=self.password.get(),
            key_password=self.key_password.get(),
            sudo_password=self.sudo_password.get(),
        )

    def _validate(self) -> str | None:
        cfg = self._server_config()
        if not cfg.host:
            return "请填写服务器地址"
        if not cfg.user:
            return "请填写登录用户名"
        if cfg.auth == "key":
            if not cfg.key_path:
                return "请选择私钥文件"
            if not Path(os.path.expanduser(cfg.key_path)).exists():
                return f"私钥文件不存在：{cfg.key_path}"
        elif not cfg.password:
            return "请填写登录密码（或改用密钥认证）"
        if not cfg.remote_dir:
            return "请填写服务器上的部署目录"
        return None

    # ------------------------------------------------------------ 构建界面
    def _build_ui(self, ttk) -> None:
        tk = self._tk

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TLabel", padding=1)
        style.configure("Hint.TLabel", foreground="#64748b")
        style.configure("Run.TButton", font=("", 10, "bold"))
        style.configure("Title.TLabel", font=("", 15, "bold"))
        style.configure("Sub.TLabel", foreground="#64748b")

        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="RADAR 部署助手", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="填写服务器信息后点「一键部署」：本地打包 → 上传 → 远端执行 deploy.sh → 健康检查",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        if not HAS_PARAMIKO:
            warn = tk.Frame(outer, bg="#fef3c7", highlightthickness=1, highlightbackground="#fcd34d")
            warn.pack(fill="x", pady=(0, 10))
            tk.Label(
                warn,
                text="缺少 paramiko（SSH/SFTP 依赖），无法连接服务器。",
                bg="#fef3c7",
                fg="#92400e",
                anchor="w",
                padx=10,
                pady=6,
            ).pack(side="left")
            ttk.Button(warn, text="安装依赖", command=self._install_paramiko).pack(
                side="right", padx=10, pady=4
            )

        self._build_server_box(outer, ttk)
        self._build_option_box(outer, ttk)
        self._build_actions(outer, ttk)
        self._build_log(outer, ttk)

        self.root.bind("<F5>", lambda _e: self._start(mode="deploy"))

    def _build_server_box(self, parent, ttk) -> None:
        tk = self._tk
        box = ttk.LabelFrame(parent, text=" 服务器 ", padding=12)
        box.pack(fill="x")

        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)

        ttk.Label(box, text="地址").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.host).grid(row=0, column=1, sticky="ew", padx=(6, 14))
        ttk.Label(box, text="SSH 端口").grid(row=0, column=2, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.port, width=10).grid(row=0, column=3, sticky="w", padx=6)

        ttk.Label(box, text="用户名").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.user).grid(row=1, column=1, sticky="ew", padx=(6, 14))
        ttk.Label(box, text="部署目录").grid(row=1, column=2, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.remote_dir).grid(row=1, column=3, sticky="ew", padx=6)

        auth_row = ttk.Frame(box)
        auth_row.grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 2))
        ttk.Label(auth_row, text="认证方式").pack(side="left")
        ttk.Radiobutton(auth_row, text="密码", value="password", variable=self.auth,
                        command=self._sync_auth).pack(side="left", padx=(10, 4))
        ttk.Radiobutton(auth_row, text="密钥文件", value="key", variable=self.auth,
                        command=self._sync_auth).pack(side="left", padx=4)

        self.pw_label = ttk.Label(box, text="登录密码")
        self.pw_label.grid(row=3, column=0, sticky="w", pady=4)
        self.pw_entry = ttk.Entry(box, textvariable=self.password, show="*")
        self.pw_entry.grid(row=3, column=1, sticky="ew", padx=(6, 14))

        self.key_label = ttk.Label(box, text="私钥文件")
        self.key_label.grid(row=3, column=2, sticky="w", pady=4)
        key_row = ttk.Frame(box)
        key_row.grid(row=3, column=3, sticky="ew", padx=6)
        key_row.columnconfigure(0, weight=1)
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_path)
        self.key_entry.grid(row=0, column=0, sticky="ew")
        self.key_btn = ttk.Button(key_row, text="浏览", width=6, command=self._pick_key)
        self.key_btn.grid(row=0, column=1, padx=(6, 0))

        self.keypw_label = ttk.Label(box, text="密钥口令")
        self.keypw_label.grid(row=4, column=0, sticky="w", pady=(4, 2))
        self.keypw_entry = ttk.Entry(box, textvariable=self.key_password, show="*")
        self.keypw_entry.grid(row=4, column=1, sticky="ew", padx=(6, 14), pady=(4, 2))
        ttk.Label(box, text="留空表示私钥未加密", style="Hint.TLabel").grid(
            row=4, column=2, columnspan=2, sticky="w", pady=(4, 2)
        )

        ttk.Checkbutton(box, text="记住密码（明文写入 ~/.radar_deploy.json，公用电脑不要勾）",
                        variable=self.remember_password).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )

        self._auth_widgets = [self.pw_label, self.pw_entry,
                              self.key_label, self.key_entry, self.key_btn,
                              self.keypw_label, self.keypw_entry]
        self._sync_auth()

    def _build_option_box(self, parent, ttk) -> None:
        box = ttk.LabelFrame(parent, text=" 部署选项 ", padding=12)
        box.pack(fill="x", pady=(10, 0))

        box.columnconfigure(1, weight=1)
        box.columnconfigure(3, weight=1)

        ttk.Label(box, text="部署模式").grid(row=0, column=0, sticky="w", pady=4)
        combo = ttk.Combobox(box, textvariable=self.mode, state="readonly",
                             values=("docker", "native", "macos"), width=12)
        combo.grid(row=0, column=1, sticky="w", padx=6)
        combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_mode())

        ttk.Label(box, text="服务端口").grid(row=0, column=2, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.service_port, width=10).grid(
            row=0, column=3, sticky="w", padx=6
        )

        ttk.Label(box, text="", style="Hint.TLabel").grid(row=1, column=1, sticky="w")
        self.mode_hint = ttk.Label(box, text="", style="Hint.TLabel")
        self.mode_hint.grid(row=1, column=0, columnspan=4, sticky="w")

        opts = ttk.Frame(box)
        opts.grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Checkbutton(opts, text="上传代码", variable=self.upload_code).pack(side="left")
        ttk.Checkbutton(opts, text="下载模型权重（首次部署必须）",
                        variable=self.download_ckpt).pack(side="left", padx=(14, 0))
        ttk.Checkbutton(opts, text="使用 HF 国内镜像", variable=self.hf_mirror).pack(
            side="left", padx=(14, 0)
        )
        ttk.Checkbutton(opts, text="使用 pip 国内镜像", variable=self.pip_mirror).pack(
            side="left", padx=(14, 0)
        )

        sudo_row = ttk.Frame(box)
        sudo_row.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Checkbutton(sudo_row, text="远端使用 sudo",
                        variable=self.use_sudo, command=self._sync_sudo).pack(side="left")
        ttk.Label(sudo_row, text="sudo 密码").pack(side="left", padx=(14, 4))
        self.sudo_entry = ttk.Entry(sudo_row, textvariable=self.sudo_password, show="*", width=18)
        self.sudo_entry.pack(side="left")
        ttk.Label(
            sudo_row,
            text="（推荐改用非 root 用户并把其加入 docker 组，省掉这一步）",
            style="Hint.TLabel",
        ).pack(side="left", padx=6)
        self._sync_sudo()

        self._sync_mode()

    def _build_actions(self, parent, ttk) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(12, 0))

        self.btn_test = ttk.Button(row, text="测试连接", command=self._on_test)
        self.btn_test.pack(side="left")

        self.btn_deploy = ttk.Button(row, text="一键部署", style="Run.TButton",
                                     command=lambda: self._start("deploy"))
        self.btn_deploy.pack(side="left", padx=8)

        self.btn_upload = ttk.Button(row, text="只上传代码", command=lambda: self._start("upload"))
        self.btn_upload.pack(side="left")

        self.btn_site = ttk.Button(row, text="打开站点", command=self._open_site)
        self.btn_site.pack(side="left", padx=8)

        ttk.Button(row, text="保存配置", command=self._save_config).pack(side="left")
        ttk.Button(row, text="清空日志", command=self._clear_log).pack(side="right")

        status_row = ttk.Frame(parent)
        status_row.pack(fill="x", pady=(10, 0))
        ttk.Label(status_row, textvariable=self.status, width=34).pack(side="left")
        self.bar = ttk.Progressbar(status_row, variable=self.progress, maximum=100.0)
        self.bar.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Label(status_row, textvariable=self.percent_text, width=5,
                  anchor="e").pack(side="left")

        # 子状态：远端脚本自己的阶段标题、权重下载百分比等
        ttk.Label(parent, textvariable=self.sub_text, style="Hint.TLabel").pack(
            anchor="w", pady=(4, 0)
        )

    def _build_log(self, parent, ttk) -> None:
        tk = self._tk
        box = ttk.LabelFrame(parent, text=" 日志 ", padding=6)
        box.pack(fill="both", expand=True, pady=(10, 0))

        self.log_text = tk.Text(
            box, wrap="word", height=16, background="#0f172a", foreground="#cbd5e1",
            insertbackground="#cbd5e1", borderwidth=0, font=("Consolas", 9),
            state="disabled",
        )
        scroll = ttk.Scrollbar(box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        for tag, (color, _bg) in LEVEL_TAGS.items():
            self.log_text.tag_configure(tag, foreground=color)
        self.log_text.tag_configure("step", foreground="#38bdf8")
        self.log_text.tag_configure("muted", foreground="#64748b")

    # ------------------------------------------------------------ 联动
    def _sync_auth(self) -> None:
        key_mode = self.auth.get() == "key"
        for widget in (self.pw_label, self.pw_entry):
            widget.grid() if not key_mode else widget.grid_remove()
        for widget in (self.key_label, self.key_entry, self.key_btn,
                       self.keypw_label, self.keypw_entry):
            widget.grid() if key_mode else widget.grid_remove()

    def _sync_sudo(self) -> None:
        self.sudo_entry.configure(state="normal" if self.use_sudo.get() else "disabled")

    def _sync_mode(self) -> None:
        hints = {
            "docker": "推荐。服务器只需 Docker + NVIDIA Container Toolkit，前端在镜像内构建。",
            "native": "裸机。要求服务器已装 Python 3.10+ 与 Node，且自备 CUDA 环境。",
            "macos": "仅在 macOS 上使用；Linux 服务器请选 docker 或 native。",
        }
        self.mode_hint.configure(text=hints.get(self.mode.get(), ""))

    def _pick_key(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            title="选择私钥文件",
            filetypes=[("私钥", "id_rsa id_ed25519 id_ecdsa *.pem *.key"), ("所有文件", "*.*")],
        )
        if path:
            self.key_path.set(path)

    def _open_site(self) -> None:
        cfg = self._collect()
        url = f"http://{cfg['host']}:{cfg['service_port']}"
        self._append(f"在浏览器中打开 {url}", "info")
        webbrowser.open(url)

    # ------------------------------------------------------------ 日志
    def _append(self, text: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        tag = level if level in LEVEL_TAGS or level == "step" else "info"
        self.log_text.configure(state="normal")
        for line in str(text).splitlines() or [""]:
            self.log_text.insert("end", f"{ts}  {line}\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------ 线程通信
    def _drain(self) -> None:
        """在主线程消费工作线程投递的消息。"""
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self._append(payload["text"], payload.get("level", "info"))
                elif kind == "step":
                    self._append("", "info")
                    self._append(f"==> {payload['text']}", "step")
                elif kind == "stage":
                    self._stage_label = (
                        f"[{payload['index']}/{payload['total']}] {payload['text']}"
                    )
                    self._stage_started = time.time()
                    self.status.set(self._stage_label)
                    self.sub_text.set("")
                    self._append("", "info")
                    self._append(f"==> {payload['text']}", "step")
                elif kind == "substage":
                    self.sub_text.set(f"当前：{payload['text']}")
                elif kind == "notice":
                    self.sub_text.set(payload["text"])
                elif kind == "progress":
                    value = payload.get("value") or 0.0
                    self.progress.set(value)
                    self.percent_text.set(f"{value:.0f}%")
                elif kind == "status":
                    self.status.set(payload["text"])
                elif kind == "finish":
                    self._finish(payload)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _post(self, kind: str, **payload) -> None:
        self.queue.put((kind, payload))

    def _tick_clock(self) -> None:
        """每秒刷新当前阶段的运行时长。

        远端执行 deploy.sh 可能要跑几分钟（下载权重尤其久），期间日志几乎
        不动，显示「已运行 3m12s」能让用户看出程序还活着而不是卡死。
        """
        if self.busy and self._stage_label:
            elapsed = time.time() - self._stage_started
            self.status.set(f"{self._stage_label} · 已运行 {_fmt_duration(elapsed)}")
        self.root.after(1000, self._tick_clock)

    # ------------------------------------------------------------ 任务
    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_test, self.btn_deploy, self.btn_upload):
            btn.configure(state=state)
        self.progress.set(0.0)
        self.percent_text.set("")
        if not busy:
            self._stage_label = ""
            self.sub_text.set("")

    def _start(self, mode: str) -> None:
        if self.busy:
            return
        if not HAS_PARAMIKO:
            self._append("缺少 paramiko，请先点上方「安装依赖」", "err")
            return
        error = self._validate()
        if error:
            self._append(error, "err")
            self.status.set(error)
            return

        cfg = self._server_config()
        if mode == "deploy" and cfg.download_ckpt:
            self._append("已勾选「下载模型权重」，首次部署这一步可能耗时较久，请耐心等待", "warn")

        self._set_busy(True)
        self._append("", "info")
        self._append(f"===== {datetime.now():%Y-%m-%d %H:%M:%S} 开始 =====", "step")

        def worker() -> None:
            try:
                if mode == "test":
                    result = self._run_test(cfg)
                else:
                    self._post("status", text="部署中…")
                    result = DeployTask(cfg, self._post).run(upload_only=(mode == "upload"))
            except Exception as exc:  # 兜底，避免线程静默死掉而界面一直转圈
                result = DeployResult(False, f"{type(exc).__name__}: {exc}")
            self._post("finish", ok=result.ok, message=result.message, url=result.url)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def _on_test(self) -> None:
        self._start("test")

    def _run_test(self, cfg: ServerConfig) -> DeployResult:
        """连接测试：握手 + 环境探测，不改动服务器上任何文件。"""
        self._post("status", text="测试连接中…")
        self._post("step", text="连接服务器")
        deployer = Deployer(lambda text: self._post("log", text=text))
        try:
            started = time.time()
            deployer.connect(cfg)
            self._post("log", text=f"握手成功，耗时 {time.time() - started:.1f}s", level="ok")

            self._post("step", text="环境探测")
            self._post(
                "log", text=f"  部署目录：{deployer.resolve_dir(cfg.remote_dir)}", level="info"
            )
            info = deployer.probe(cfg)
            for key, value in info.items():
                level = "warn" if ("未" in value and key in ("Docker", "GPU")) else "info"
                self._post("log", text=f"  {key}：{value}", level=level)

            if cfg.mode == "docker" and info.get("Docker") == "未安装":
                self._post("log", text="docker 模式下缺少 Docker，部署会失败", level="err")
            if info.get("已部署") == "是":
                self._post("log", text="该目录已有部署，上传会覆盖同名文件（权重不受影响）", level="warn")

            return DeployResult(True, "连接测试通过", f"http://{cfg.host}:{cfg.service_port}")
        except Exception as exc:
            return DeployResult(False, f"{type(exc).__name__}: {exc}")
        finally:
            deployer.close()

    def _finish(self, payload: dict) -> None:
        ok = payload.get("ok", False)
        message = payload.get("message", "")
        self._set_busy(False)
        self._append("", "info")
        if ok:
            url = payload.get("url") or ""
            self.status.set("完成")
            self._append(f"===== 成功：{message} =====", "ok")
            if url:
                self._append(f"访问地址：{url}", "ok")
                self._append(f"若从本机打不开，检查服务器安全组是否放行该端口（默认 8000）", "muted")
        else:
            self.status.set("失败")
            self._append(f"===== 失败：{message} =====", "err")

    def _install_paramiko(self) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self._append("正在安装 paramiko …", "info")
        self.status.set("安装依赖中…")

        def worker() -> None:
            import subprocess

            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "paramiko"],
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-6:]
                for line in tail:
                    self._post("log", text=line, level="muted")
                if proc.returncode == 0:
                    self._post(
                        "log",
                        text="安装成功，请关闭本窗口后重新运行部署助手",
                        level="ok",
                    )
                else:
                    self._post("log", text=f"安装失败（退出码 {proc.returncode}）", level="err")
            except Exception as exc:
                self._post("log", text=f"安装失败：{exc}", level="err")
            finally:
                self._post("finish", ok=False, message="依赖安装流程结束（paramiko 需重启程序才生效）")

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------ 生命周期
    def _on_close(self) -> None:
        from tkinter import messagebox

        if self.busy and not messagebox.askokcancel(
            "正在执行", "任务尚未结束，强行退出会中断上传或部署。确定退出吗？"
        ):
            return
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


# ---------------------------------------------------------------- 入口


def _package_only(target: str) -> int:
    out = Path(target).expanduser().resolve()
    stats = build_package(out, on_progress=lambda msg: print(f"  {msg}"))
    print(f"压缩包：{out}")
    print(f"文件数：{stats.files}")
    print(f"原始大小：{stats.raw_bytes / 1024 / 1024:.2f} MB")
    print(f"压缩后：{stats.archive_bytes / 1024 / 1024:.2f} MB")
    print("按顶层目录：")
    for name, size in sorted(stats.top_level.items(), key=lambda kv: -kv[1]):
        print(f"  {name:<22} {size / 1024 / 1024:>7.2f} MB")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RADAR 图形化部署助手")
    parser.add_argument("--selftest", action="store_true", help="只构建界面做自检，不进入事件循环")
    parser.add_argument("--package-only", metavar="OUT", help="只打包到指定路径，不连接服务器")
    args = parser.parse_args(argv)

    if args.package_only:
        return _package_only(args.package_only)

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高分屏下界面不发虚
        except Exception:
            pass

    app = DeployApp()
    if args.selftest:
        app.root.update_idletasks()
        app.root.update()
        panels = len(app.root.winfo_children())
        print(f"界面构建成功：顶层容器 {panels} 个，paramiko={'可用' if HAS_PARAMIKO else '缺失'}")
        app.root.destroy()
        return 0

    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
