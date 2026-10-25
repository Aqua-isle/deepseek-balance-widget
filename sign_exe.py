# -*- coding: utf-8 -*-
"""给 dist 里的 exe 做 Authenticode 签名。

三种用法（按你手上的东西选）：

    python sign_exe.py --self-test
        自签名。**只适合本机验证或内网分发**——目标机器没信任这张证书，
        SmartScreen 照样会拦，而且"签名无效"比"没签名"更难看。

    python sign_exe.py --pfx my.pfx
        用商业 / OV 证书的 pfx 文件。密码从环境变量 DSB_SIGN_PASSWORD 读，
        或者加 --password（会出现在进程列表里，不推荐）。

    python sign_exe.py --thumbprint <证书 SHA1>
        用证书存储里的证书签名（USB Key / 中间证书也行，会弹令牌密码框）。

可选参数：
    --timestamp URL   默认 http://timestamp.digicert.com
    --file PATH       只签指定文件（默认签 dist 下的 DeepSeekBalance.exe
                      和 DeepSeekBalance-portable\\DeepSeekBalance-portable.exe）
    --no-timestamp    不盖时间戳（证书过期后签名随之失效，不推荐）

签完会用 signtool verify 复核一遍，并把结果打出来。
"""

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TIMESTAMP = "http://timestamp.digicert.com"
DEFAULT_TARGETS = (
    HERE / "dist" / "DeepSeekBalance.exe",
    HERE / "dist" / "DeepSeekBalance-portable" / "DeepSeekBalance-portable.exe",
)


def find_signtool() -> str:
    """在 Windows Kits 里找最新的 signtool。"""
    roots = [Path(r"C:\Program Files (x86)\Windows Kits\10\bin"),
             Path(r"C:\Program Files\Windows Kits\10\bin")]
    candidates = []
    for root in roots:
        if root.is_dir():
            candidates.extend(root.rglob("x64/signtool.exe"))
    if not candidates:
        raise SystemExit("找不到 signtool.exe，请安装 Windows SDK（Windows Kits 10）")
    candidates.sort(key=lambda p: p.parent.parent.name, reverse=True)
    return str(candidates[0])


def powershell(command: str) -> str:
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                             "-Command", command],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise SystemExit(f"PowerShell 失败：{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def make_self_signed() -> str:
    """创建一张自签名代码签名证书，返回指纹。"""
    script = (
        "$c = New-SelfSignedCertificate -Type CodeSigningCert "
        "-Subject 'CN=DeepSeek Balance Widget (Self-Test), O=Local Test, C=CN' "
        "-KeyUsage DigitalSignature -KeyExportPolicy Exportable "
        "-NotAfter (Get-Date).AddYears(1) -CertStoreLocation Cert:\\CurrentUser\\My; "
        "$c.Thumbprint"
    )
    thumb = powershell(script).splitlines()[-1].strip()
    if len(thumb) != 40:
        raise SystemExit(f"创建自签名证书失败，返回：{thumb!r}")
    return thumb


def sign(signtool: str, target: Path, auth_args, timestamp: str = None) -> int:
    cmd = [signtool, "sign", "/fd", "SHA256"] + auth_args
    if timestamp:
        cmd += ["/td", "SHA256", "/tr", timestamp]
    cmd.append(str(target))
    print("  " + " ".join(cmd[:6] + (["..."] if len(cmd) > 8 else []) + [target.name]))
    return subprocess.call(cmd)


def verify(signtool: str, target: Path) -> int:
    print(f"  验证 {target.name} …")
    result = subprocess.run([signtool, "verify", "/pa", "/v", str(target)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (result.stdout or "") + (result.stderr or "")
    for line in text.splitlines():
        if any(key in line for key in ("Issued to", "Expires", "timestamped",
                                       "Successfully", "Error", "trusted")):
            print("    " + line.strip())
    return result.returncode


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
    sys.path.insert(0, str(HERE))
    argv = sys.argv[1:]

    target_arg = None
    if "--file" in argv:
        target_arg = argv[argv.index("--file") + 1]
    targets = [Path(target_arg)] if target_arg else [p for p in DEFAULT_TARGETS if p.exists()]
    if not targets:
        print("找不到要签名的 exe，先跑 build_exe.py")
        return 1

    timestamp = None if "--no-timestamp" in argv else DEFAULT_TIMESTAMP
    if "--timestamp" in argv:
        timestamp = argv[argv.index("--timestamp") + 1]

    if "--self-test" in argv:
        print("[sign] 创建自签名证书（仅本机/内网测试用）…")
        thumb = make_self_signed()
        print(f"[sign] 指纹 {thumb}")
        auth = ["/sha1", thumb]
        note = "自签名：目标机器没信任这张证书，SmartScreen 依然会拦"
    elif "--pfx" in argv:
        pfx = argv[argv.index("--pfx") + 1]
        password = os.environ.get("DSB_SIGN_PASSWORD") or (
            argv[argv.index("--password") + 1] if "--password" in argv else None)
        if not password:
            print("缺少密码：设环境变量 DSB_SIGN_PASSWORD 或加 --password")
            return 1
        auth = ["/f", pfx, "/p", password]
        note = "商业证书：受信任的签名，配合时间戳可长期有效"
    elif "--thumbprint" in argv:
        thumb = argv[argv.index("--thumbprint") + 1]
        auth = ["/sha1", thumb, "/a"]
        note = "证书存储里的证书"
    else:
        print(__doc__)
        return 1

    signtool = find_signtool()
    print(f"[sign] signtool: {signtool}")
    print(f"[sign] 说明：{note}")

    failed = 0
    for target in targets:
        print(f"[sign] 处理 {target.relative_to(HERE) if target.is_absolute() else target}")
        if sign(signtool, target, auth, timestamp) != 0:
            failed += 1
            continue
        if verify(signtool, target) != 0:
            # 自签名一定会在"根证书不受信任"这里失败，属于预期
            print("    （验证未通过——自签名证书通常如此，见上面的说明）")

    print("\n[sign] 完成" if not failed else f"\n[sign] {failed} 个文件签名失败")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
