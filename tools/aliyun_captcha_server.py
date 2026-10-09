"""Local demo server for Aliyun Captcha 2.0 server-side verification.

Run from the repository root after setting RAM-user credentials in the
environment. The server never accepts credentials from the browser and never
logs CaptchaVerifyParam.
"""
from __future__ import annotations

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from alibabacloud_captcha20230305 import models as captcha_models
from alibabacloud_captcha20230305.client import Client as CaptchaClient
from alibabacloud_tea_openapi import models as open_api_models


ROOT = Path(__file__).resolve().parents[1]
VERIFY_PATH = "/aliyun-captcha/verify"
SCENE_ID = os.getenv("ALIYUN_CAPTCHA_SCENE_ID", "j0l0dikm")
ENDPOINT = os.getenv("ALIYUN_CAPTCHA_ENDPOINT", "captcha.cn-shanghai.aliyuncs.com")


def _json(handler: SimpleHTTPRequestHandler, status: int, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(payload)


def _client() -> CaptchaClient:
    access_key_id = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = os.getenv("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    if not access_key_id or not access_key_secret:
        raise RuntimeError(
            "请设置 ALIBABA_CLOUD_ACCESS_KEY_ID 和 "
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET（仅放在服务端环境变量）"
        )
    config = open_api_models.Config(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        security_token=os.getenv("ALIBABA_CLOUD_SECURITY_TOKEN"),
        endpoint=ENDPOINT,
    )
    return CaptchaClient(config)


class Handler(SimpleHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        if self.path != VERIFY_PATH:
            _json(self, 404, {"ok": False, "error": "not_found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 32 * 1024:
                raise ValueError("请求体大小不合法")
            body = json.loads(self.rfile.read(size))
            param = body.get("captchaVerifyParam") if isinstance(body, dict) else None
            if not isinstance(param, str) or not param:
                raise ValueError("captchaVerifyParam 不能为空")
            response = _client().verify_intelligent_captcha(
                captcha_models.VerifyIntelligentCaptchaRequest(
                    captcha_verify_param=param,
                    # 服务端固定场景，避免客户端改写 SceneId。
                    scene_id=SCENE_ID,
                )
            )
            result = response.body.result if response.body else None
            passed = bool(result and result.verify_result)
            _json(self, 200, {
                "ok": passed,
                "captchaVerifyResult": passed,
                "verifyCode": getattr(result, "verify_code", None),
                "requestId": getattr(response.body, "request_id", None),
            })
        except ValueError as exc:
            _json(self, 400, {"ok": False, "error": str(exc)})
        except RuntimeError as exc:
            _json(self, 503, {"ok": False, "error": str(exc)})
        except Exception:
            # Do not expose SDK credentials, request data, or provider details
            # to the browser. The demo fails closed when server verification is
            # unavailable; production business policy may choose its own fallback.
            _json(self, 502, {"ok": False, "error": "aliyun_verify_unavailable"})

    def log_message(self, fmt: str, *args: object) -> None:
        # Avoid logging the POST body or any token-bearing URL.
        if self.path == VERIFY_PATH:
            print(f"[captcha] {self.command} {self.path}")
        else:
            super().log_message(fmt, *args)


def main() -> None:
    port = int(os.getenv("ALIYUN_CAPTCHA_PORT", "8765"))
    os.chdir(ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[captcha] test page: http://127.0.0.1:{port}/tools/aliyun_captcha_test.html")
    print(f"[captcha] verify endpoint: http://127.0.0.1:{port}{VERIFY_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
