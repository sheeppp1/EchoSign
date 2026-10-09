"""Browser login and attendance workflows for HDU's 上课啦."""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

import yaml
from playwright.sync_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError, sync_playwright

from hdusign.attendance import SignResult, classify_response, diagnostic_sign_code, write_json
from hdusign.location import DEFAULT_LAT, DEFAULT_LNG
from hdusign.processes import hidden_subprocess_options
from hdusign.runtime import application_root, configure_browser_runtime

ROOT = application_root()
PROFILE = ROOT / "browser_profile"
START = "https://skl.hdu.edu.cn/index.html"


def account_profile(secrets: dict) -> Path:
    """Keep attendance sessions tied to the account entered on the home page."""
    username = str(secrets.get("skl_username", "")).strip()
    if not username:
        return PROFILE
    identity = hashlib.sha256(username.encode("utf-8")).hexdigest()
    return PROFILE / "accounts" / identity


def load_secrets() -> dict:
    p = ROOT / "secrets_local.json"
    if not p.exists():
        print(f"请创建 {p} 并填入 skl_username / skl_password")
        sys.exit(2)
    return json.loads(p.read_text(encoding="utf-8"))


def try_sso_login(page, secrets: dict) -> bool:
    """Fill the HDU SSO form. Returns True if a login submit was attempted.

    The current 统一身份认证 page is an Angular form whose 登录 button stays
    disabled until both fields register input, so fields are clicked before
    filling and the button is awaited. Older CAS layouts fall back to heuristics.
    """
    user = secrets["skl_username"]
    pwd = secrets["skl_password"]

    user_selectors = ["input[name=username]", "#username", "input[name=account]",
                      "input[type=text]:not([name*=ver])", "input[placeholder*=账号]",
                      "input[placeholder*=学号]", "input[placeholder*=用户名]"]
    filled_user = None
    for sel in user_selectors:
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.click()
            el.fill(user)
            filled_user = sel
            break
    pw_selectors = ["input[type=password]", "#password", "input[name=password]"]
    filled_pw = None
    for sel in pw_selectors:
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.click()
            el.fill(pwd)
            filled_pw = sel
            break
    if not (filled_user and filled_pw):
        print(f"[!] 没找齐输入框 (user={filled_user}, pwd={filled_pw}), 请手动在浏览器里完成登录")
        return False

    print(f"[i] 自动填充登录表单 ({filled_user} / {filled_pw})")
    try:
        page.wait_for_function(
            "() => { const b = document.querySelector('button.login-button');"
            " return !b || (!b.disabled && !b.classList.contains('disabled')); }", timeout=8000)
    except PlaywrightTimeoutError:
        print("[!] 登录按钮未启用，请在浏览器中手动完成登录")
        return False
    for sel in ["button.login-button", "button:has-text('登 录')", "button:has-text('登录')",
                "#login_submit", "input[type=submit]", "button[type=submit]", ".login-btn", "#loginBtn"]:
        el = page.query_selector(sel)
        if el and el.is_visible():
            try:
                el.click(timeout=5000)
            except PlaywrightTimeoutError:
                print(f"[!] 无法点击登录按钮 {sel}，请在浏览器中手动完成登录")
                return False
            print(f"[i] 点击登录: {sel}")
            return True
    print("[i] 未找到登录按钮, 尝试回车提交")
    page.keyboard.press("Enter")
    return True


CAMPUS_HOSTS = ("skl.hdu.edu.cn", "sso.hdu.edu.cn", "cas.hdu.edu.cn")


def _browser_args() -> list[str]:
    """Optional network workarounds from config.yaml `browser:`.

    ipv4_only pins campus hosts to their IPv4 addresses (some networks close
    IPv6 TLS to hdu.edu.cn); bypass_proxy ignores a system proxy such as Clash
    that breaks TLS to those hosts. Both default to off.
    """
    options = {}
    cfgp = ROOT / "config.yaml"
    if cfgp.exists():
        try:
            options = (yaml.safe_load(cfgp.read_text(encoding="utf-8")) or {}).get("browser") or {}
        except Exception:  # noqa: BLE001
            options = {}
    args: list[str] = []
    if options.get("bypass_proxy"):
        args.append("--no-proxy-server")
    if options.get("ipv4_only"):
        import socket

        rules = []
        for host in CAMPUS_HOSTS:
            try:
                addresses = sorted({a[4][0] for a in socket.getaddrinfo(host, 443, socket.AF_INET)})
            except socket.gaierror:
                continue
            if addresses:
                rules.append(f"MAP {host} {addresses[0]}")
        if rules:
            args.append("--host-resolver-rules=" + ", ".join(rules))
    return args


def _windows_commandline_args(command_line: str) -> list[str]:
    """Use Windows' quoting rules, including quoted values after an equals sign."""
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    arguments = parse(command_line, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))


def _commandline_uses_profile(command_line: str, profile: Path) -> bool:
    """Match a parsed user-data-dir value, never a path substring in another arg."""
    arguments = _windows_commandline_args(command_line)
    expected = os.path.normcase(str(profile.resolve()))
    values = []
    for index, argument in enumerate(arguments[1:], 1):
        option, separator, value = argument.partition("=")
        if option.lower() != "--user-data-dir":
            continue
        if not separator:
            value = arguments[index + 1] if index + 1 < len(arguments) else ""
        # A relative path belongs to the process's working directory, which CIM
        # does not provide. Do not guess and then delete potentially live locks.
        if not value or not Path(value).is_absolute():
            raise ValueError("无法确认浏览器使用的用户数据目录")
        values.append(os.path.normcase(str(Path(value).resolve())))
    # Chromium uses the last value when an option is repeated. Respect that
    # rule rather than treating an earlier stale argument as the active profile.
    return bool(values) and values[-1] == expected


def _cleanup_stale_profile(profile: Path = PROFILE) -> None:
    """Remove stale locks only after confirming this profile has no live browser.

    A browser using the same account can belong to another active task. Never
    terminate it here; owned child processes are reaped by the task dispatcher.
    """
    import subprocess

    if sys.platform != "win32":
        return  # No process inventory is available here to prove locks stale.
    profile = profile.resolve()
    ps = ("$ErrorActionPreference = 'Stop'; "
          "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR "
          "Name='chromium.exe' OR Name='msedge.exe' OR Name='headless_shell.exe'\" | "
          "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
             "-Command", ps], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=30, **hidden_subprocess_options())
        if result.returncode != 0 or (result.stderr or "").strip():
            raise ValueError("浏览器进程查询失败")
        raw = json.loads(result.stdout or "[]")
        processes = raw if isinstance(raw, list) else [raw]
        for item in processes:
            if (not isinstance(item, dict) or type(item.get("ProcessId")) is not int
                    or item["ProcessId"] <= 0 or not isinstance(item.get("CommandLine"), str)
                    or not item["CommandLine"].strip()):
                raise ValueError("浏览器进程信息不可读")
            if _commandline_uses_profile(item["CommandLine"], profile):
                raise RuntimeError("当前账号的签到浏览器仍在使用，请先关闭对应浏览器或停止相关监控，再重新登录。")
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法确认签到浏览器是否仍在使用，已保留登录目录；请关闭相关浏览器后重试。") from exc
    for lock in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"):
        path = profile / lock
        try:
            if path.exists() or path.is_symlink():
                path.unlink()
        except OSError:
            pass


def login(stop=None) -> int:
    if stop is not None and stop.is_set():
        return 0
    configure_browser_runtime()
    secrets = load_secrets()
    profile = account_profile(secrets)
    _cleanup_stale_profile(profile)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(profile), headless=False, args=_browser_args())
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        def wait(milliseconds):
            # Keep Playwright dispatching events while allowing window close to
            # cancel manual login instead of waiting for its three-minute limit.
            remaining = milliseconds
            while remaining > 0:
                if stop is not None and stop.is_set():
                    return False
                step = min(100, remaining)
                page.wait_for_timeout(step)
                remaining -= step
            return stop is None or not stop.is_set()

        page.goto(START, wait_until="domcontentloaded", timeout=60000)
        if not wait(3000):
            ctx.close()
            return 0

        deadline = time.time() + 180
        login_attempted = False
        while time.time() < deadline:
            if stop is not None and stop.is_set():
                ctx.close()
                return 0
            url = page.url
            if "sso.hdu.edu.cn" in url or "cas.hdu.edu.cn" in url:
                if not login_attempted:
                    if not wait(1500):
                        ctx.close()
                        return 0
                    login_attempted = True
                    try_sso_login(page, secrets)
            elif "skl.hdu.edu.cn" in url:
                token = page.evaluate("() => window.localStorage.getItem('sessionId') || ''")
                if token:
                    print("[OK] 已确认登录态")
                    (ROOT / "session_local.json").write_text(
                        json.dumps({"x_auth_token": token, "captured_at": time.time()},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
                    print("[i] 已保存到 session_local.json")
                    ctx.close()
                    return 0
                print(f"[i] 在 skl 页面但 localStorage 还没有 sessionId, url={url[:80]}")
            else:
                print(f"[i] 当前页面: {url[:90]}")
            print("    (180秒内自动检测, 也可手动在浏览器里完成任何操作)")
            if not wait(5000):
                ctx.close()
                return 0

        print("[!] 超时未确认登录态; profile 已保留, 可重跑")
        ctx.close()
        return 1


def _location() -> tuple[float, float]:
    cfgp = ROOT / "config.yaml"
    if cfgp.exists():
        try:
            loc = (yaml.safe_load(cfgp.read_text(encoding="utf-8")) or {}).get("location") or {}
            return float(loc.get("lat", DEFAULT_LAT)), float(loc.get("lng", DEFAULT_LNG))
        except Exception:  # noqa: BLE001
            pass
    return DEFAULT_LAT, DEFAULT_LNG


def ensure_logged_in(page, secrets: dict, timeout_s: int = 120) -> str:
    page.goto(START, wait_until="domcontentloaded", timeout=60000)
    deadline = time.monotonic() + timeout_s
    attempted = False
    while time.monotonic() < deadline:
        url = page.url
        if "sso.hdu.edu.cn" in url or "cas.hdu.edu.cn" in url:
            if not attempted:
                try:
                    page.locator("input[type=password]").first.wait_for(state="visible", timeout=5000)
                    # A disabled submit / verification prompt needs the user;
                    # repeatedly refilling it can interrupt that interaction.
                    attempted = True
                    try_sso_login(page, secrets)
                except PlaywrightTimeoutError:
                    pass
        else:
            try:
                token = page.evaluate("() => window.localStorage.getItem('sessionId') || ''")
            except PlaywrightError as exc:
                if "Execution context was destroyed" not in str(exc):
                    raise
                token = ""
            if token:
                return token
        page.wait_for_timeout(100)
    raise RuntimeError("登录态获取失败(超时)")


def click_visible(page, selector: str, text: str | None = None, timeout_ms: int = 8000):
    """Click first visible element matching selector (and exact text if given)."""
    locator = page.locator(selector)
    if text is not None:
        locator = locator.filter(has_text=re.compile(r"^\s*" + re.escape(text) + r"\s*$"))
    target = locator.filter(visible=True).first
    target.click(timeout=timeout_ms)
    return target


KEYPAD = "button, .van-button, [role=button]"


def _is_sign_endpoint(url: str, method: str, code: str) -> bool:
    parsed = urlparse(url)
    return (parsed.scheme == "https" and parsed.hostname == "skl.hdu.edu.cn"
            and parsed.path.rstrip("/") == "/api/ali-nvc/captcha-verify"
            and method == "POST"
            and parse_qs(parsed.query).get("code") == [code])


def is_sign_response(response, code: str) -> bool:
    # Keep the response URL check as well (redirects cannot establish success).
    return _is_sign_endpoint(response.url, response.request.method, code)


CAPTCHA_CONTAINERS = (
    ".nc-container, .nc_scale, #baxia-dialog-content, "
    "#aliyunCaptcha-window-popup, #aliyunCaptcha-sliding-wrapper"
)
VERIFICATION_ACTION = re.compile(
    r"^\s*(?:点击(?:按钮)?(?:开始|进行|完成)?(?:智能)?验证|"
    r"点此(?:开始|进行)?验证|我不是机器人|验证(?:您|你)是真人)\s*$"
)
VERIFICATION_MESSAGE = re.compile(
    r"(?:请完成|需要|要求|失败|错误|未通过|不通过|重试).{0,12}"
    r"(?:验证|验证码|人机|安全|滑块|captcha)"
    r"|(?:验证|验证码|人机验证|安全验证|滑块|captcha).{0,12}"
    r"(?:失败|错误|未通过|不通过|重试|请完成|需要|要求|required|challenge)",
    re.I,
)


def _click_verification(page, check_cancelled) -> bool:
    """Try one ordinary click on an explicitly labelled Alibaba verification control.

    True means attempted, not passed. The caller must never retry a click whose
    result is uncertain. Sliders, image challenges and unlabelled controls are
    left for the user.
    """
    for frame in page.frames:
        try:
            owner = frame
            while owner.parent_frame is not None:
                if not owner.frame_element().is_visible():
                    break
                owner = owner.parent_frame
            else:
                scopes = frame.locator(CAPTCHA_CONTAINERS).filter(visible=True)
                candidates = scopes.get_by_role("button", name=VERIFICATION_ACTION).or_(
                    scopes.get_by_role("checkbox", name=VERIFICATION_ACTION)).or_(
                    scopes.get_by_text(VERIFICATION_ACTION))
                target = candidates.filter(visible=True).first
                if target.count() and target.is_enabled():
                    checked = target.evaluate("""el => {
                        const role = el.closest('[role=checkbox]');
                        if (role?.getAttribute('aria-checked') === 'true') return true;
                        const label = el.closest('label');
                        const input = el.matches('input[type=checkbox]') ? el : label?.control;
                        return input?.type === 'checkbox' && input.checked;
                    }""")
                    if checked:
                        continue
                    check_cancelled()
                    try:
                        target.click(timeout=1000)
                    except PlaywrightError:
                        # The click may have been delivered before a navigation.
                        pass
                    return True
        except PlaywrightError:
            # A verification frame may disappear or navigate during inspection.
            continue
    return False


def _captcha_visible(page) -> bool:
    """Detect visible verification UI without treating it as a sign-in result."""
    selector = (
        CAPTCHA_CONTAINERS + ", "
        "iframe[src*='captcha' i], iframe[src*='baxia' i], "
        "iframe[title*='验证码'], iframe[title*='安全验证'], "
        "iframe[title*='captcha' i], iframe[aria-label*='captcha' i], "
        "[role=dialog]:has-text('安全验证'), [role=dialog]:has-text('验证码')"
    )
    try:
        return bool(page.locator(selector).evaluate_all("""els => els.some(el => {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 1 || rect.height <= 1) return false;
            let left = rect.left, right = rect.right, top = rect.top, bottom = rect.bottom;
            for (let node = el; node; node = node.parentElement) {
                const style = getComputedStyle(node);
                if (style.display === 'none' || style.visibility === 'hidden' ||
                    style.visibility === 'collapse' || Number(style.opacity) === 0 ||
                    node.getAttribute('aria-hidden') === 'true') return false;
                if (node !== el) {
                    const bounds = node.getBoundingClientRect();
                    if (['hidden', 'clip', 'scroll', 'auto'].includes(style.overflowX)) {
                        left = Math.max(left, bounds.left); right = Math.min(right, bounds.right);
                    }
                    if (['hidden', 'clip', 'scroll', 'auto'].includes(style.overflowY)) {
                        top = Math.max(top, bounds.top); bottom = Math.min(bottom, bounds.bottom);
                    }
                }
            }
            return right - left > 1 && bottom - top > 1;
        })"""))
    except PlaywrightError:
        # The page may navigate while a verification result is arriving.
        return False


def _wait_for_sign_result(page, code, stage, check_cancelled, *, timeout_s=120) -> SignResult:
    responses = deque()
    requests = set()

    def on_request(request):
        if _is_sign_endpoint(request.url, request.method, code):
            requests.add(request)

    def on_response(response):
        if response.request in requests and is_sign_response(response, code):
            responses.append(response)

    verification = False

    def require_verification():
        nonlocal verification
        if verification:
            return
        verification = True
        stage("verification_required", time.time())
        print("[i] 签到等待验证：请查看浏览器并完成验证码，完成后自动核对签到结果。", flush=True)
        try:
            page.bring_to_front()
        except PlaywrightError:
            pass

    # Capture requests as well as responses so a late response to an earlier
    # submission of the same code cannot satisfy this one.
    page.on("request", on_request)
    page.on("response", on_response)
    try:
        check_cancelled()
        click_visible(page, KEYPAD, "签到", timeout_ms=6000)
        stage("submit", time.time())
        started = time.monotonic()
        deadline = started + timeout_s
        pending_result = None
        settle_at = started
        verification_clicked_at = None
        while time.monotonic() < deadline:
            check_cancelled()
            if verification_clicked_at is None and _click_verification(page, check_cancelled):
                verification_clicked_at = time.monotonic()
                stage("verification_clicked", time.time())
                print("[i] 已尝试点击验证按钮一次，等待平台确认。", flush=True)
            click_pending = (verification_clicked_at is not None
                            and time.monotonic() - verification_clicked_at < 3)
            visible = _captcha_visible(page)
            if visible and not click_pending:
                require_verification()
            while responses:
                response = responses.popleft()
                try:
                    payload = response.json()
                except (ValueError, PlaywrightError):
                    payload = None
                result = classify_response(code, response.status, payload)
                if VERIFICATION_MESSAGE.search(result.message):
                    # A challenge/rejected attempt is intermediate. Only the
                    # site's later attendance response can confirm completion.
                    if not click_pending:
                        require_verification()
                    pending_result = None
                elif result.status == "success":
                    stage("response", time.time())
                    return result
                else:
                    pending_result = result
                    # Give UI triggered by a response time to show its challenge.
                    settle_at = time.monotonic() + 1
            if visible:
                # A business rejection may arrive just before the widget closes.
                # Preserve it and let the UI settle instead of losing the result.
                settle_at = time.monotonic() + 1
            if pending_result is not None and not visible and time.monotonic() >= settle_at:
                stage("response", time.time())
                return pending_result
            page.wait_for_timeout(100)
        return SignResult("unknown", code, "等待验证或签到结果超时，请到平台核对；未自动重复提交")
    except PlaywrightTimeoutError:
        return SignResult("unknown", code, "未能确认本次签到提交，请到平台核对")
    finally:
        for event, handler in (("request", on_request), ("response", on_response)):
            try:
                page.remove_listener(event, handler)
            except PlaywrightError:
                pass


def open_sign_in(page) -> None:
    if not isinstance(page.url, str) or "/sign/in" not in page.url:
        print("[i] 打开 课堂签到 ...")
        click_visible(page, "span, button", "课堂签到", timeout_ms=15000)
        page.wait_for_url("**/sign/in**", timeout=15000)
    page.locator(KEYPAD).filter(has_text=re.compile(r"^\s*0\s*$")).filter(
        visible=True).first.wait_for(state="visible", timeout=15000)


def prepared_page_ready(page) -> bool:
    """Check local session and keypad readiness without refreshing or submitting.

    A local token cannot prove that the server still accepts the session. The
    matched business response remains the authority for the sign-in result.
    """
    try:
        url = urlparse(page.url)
        if (url.scheme != "https" or url.netloc != "skl.hdu.edu.cn"
                or "/sign/in" not in url.path + url.fragment):
            return False
        return page.evaluate("""selector => {
            if (!localStorage.getItem('sessionId')) return false;
            const buttons = Array.from(document.querySelectorAll(selector));
            const usable = (el, label) => {
                if (el.textContent.trim() !== label) return false;
                // Submit may stay disabled until all four digits are entered.
                if (label === '0' && (el.disabled ||
                    el.getAttribute('aria-disabled') === 'true')) return false;
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) return false;
                for (let node = el; node; node = node.parentElement) {
                    const style = getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' ||
                        Number(style.opacity) === 0) return false;
                }
                return true;
            };
            return buttons.some(el => usable(el, '0')) &&
                   buttons.some(el => usable(el, '签到'));
        }""", KEYPAD) is True
    except (PlaywrightError, ValueError):
        return False


def sign_with_code(ctx, page, code: str, *, on_stage=None, cancelled=None) -> SignResult:
    stage = on_stage or (lambda name, when: None)
    def check_cancelled():
        if cancelled is not None and cancelled():
            raise InterruptedError("监控已停止，未继续提交")

    check_cancelled()
    open_sign_in(page)
    stage("page_ready", time.time())
    print("[i] 已进入签到键盘页")

    for d in code:
        check_cancelled()
        click_visible(page, KEYPAD, d, timeout_ms=6000)
    stage("digits_entered", time.time())
    print(f"[i] 已输入四位签到码 {diagnostic_sign_code(code)}")

    return _wait_for_sign_result(page, code, stage, check_cancelled)


def sign(argv=None) -> int:
    parser = argparse.ArgumentParser(description="在本人已授权的课堂中提交四位签到码")
    parser.add_argument("code")
    parser.add_argument("--result-file", type=Path)
    parser.add_argument("--progress-file", type=Path)
    args = parser.parse_args(argv)
    code = args.code
    if len(code) != 4 or not code.isascii() or not code.isdigit():
        parser.error("签到码必须是四位数字")
    try:
        configure_browser_runtime()
        secrets = load_secrets()
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(account_profile(secrets)),
                                                        headless=False, args=_browser_args())
            try:
                lat, lng = _location()
                ctx.grant_permissions(["geolocation"], origin="https://skl.hdu.edu.cn")
                ctx.set_geolocation({"latitude": lat, "longitude": lng, "accuracy": 30})
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                ensure_logged_in(page, secrets)
                print("[i] 登录态已确认")
                def on_stage(name, when):
                    if name == "verification_required" and args.progress_file:
                        write_json(args.progress_file, {"status": name, "sign_code": code})

                result = sign_with_code(ctx, page, code, on_stage=on_stage)
            finally:
                ctx.close()
    except Exception as exc:  # A browser error cannot establish a sign-in outcome.
        result = SignResult("unknown", code, f"浏览器操作异常：{exc}")
    print(f"[结果] {result.status}: {result.message}")
    if args.result_file:
        result.write(args.result_file)
    return result.exit_code


def serve_prepared(ctx, page, secrets: dict, directory: Path) -> None:
    """Read-only preparation, followed only by explicitly confirmed requests."""
    ensure_logged_in(page, secrets)
    open_sign_in(page)
    ready_at = time.time()
    write_json(directory / "ready.json", {"status": "ready", "page_ready_at": ready_at})
    last_request = 0
    while not (directory / "stop.json").exists():
        request_path = directory / f"request-{last_request + 1}.json"
        if not request_path.exists():
            time.sleep(0.05)
            continue
        request = json.loads(request_path.read_text(encoding="utf-8"))
        sequence = request.get("id")
        if type(sequence) is not int or sequence != last_request + 1:
            raise ValueError("无效的签到请求序号")
        code = request.get("code")
        if not isinstance(code, str) or len(code) != 4 or not code.isascii() or not code.isdigit():
            raise ValueError("签到码必须是四位数字")
        last_request = sequence
        if (directory / "stop.json").exists():
            return
        timings = {"detected_at": request.get("detected_at"), "prewarmed_page_ready": ready_at}
        def on_stage(name, when):
            timings[name] = when
            if name == "verification_required":
                write_json(directory / f"progress-{sequence}.json", {"status": name, "sign_code": code})

        session_invalid = False
        try:
            if not prepared_page_ready(page):
                session_invalid = True
                result = SignResult("failure", code,
                                    "签到页或登录态已失效，请重新登录；本次未输入或提交签到码")
            else:
                result = sign_with_code(ctx, page, code,
                                        on_stage=on_stage,
                                        cancelled=lambda: (directory / "stop.json").exists())
        except Exception as exc:
            session_invalid = True
            result = SignResult("unknown", code, f"浏览器操作异常：{exc}")
        write_json(directory / f"timing-{sequence}.json", timings)
        result.write(directory / f"result-{sequence}.json")
        if session_invalid or (directory / "stop.json").exists():
            return
        # Return to a fresh, empty keypad while waiting for another sign-in in
        # the same lesson. Never reuse the previous four digits or response.
        ensure_logged_in(page, secrets)
        open_sign_in(page)
        ready_at = time.time()


def sign_worker(argv=None) -> int:
    parser = argparse.ArgumentParser(description="预备签到页并等待本次监控确认的签到码")
    parser.add_argument("--exchange-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    directory = args.exchange_dir.resolve(strict=True)
    try:
        configure_browser_runtime()
        secrets = load_secrets()
        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(str(account_profile(secrets)), headless=False, args=_browser_args())
            try:
                lat, lng = _location()
                ctx.grant_permissions(["geolocation"], origin="https://skl.hdu.edu.cn")
                ctx.set_geolocation({"latitude": lat, "longitude": lng, "accuracy": 30})
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                serve_prepared(ctx, page, secrets, directory)
            finally:
                ctx.close()
    except Exception as exc:
        write_json(directory / "ready.json", {"status": "error", "message": str(exc)[:240]})
        return 2
    return 0
