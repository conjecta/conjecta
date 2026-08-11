"""Alibaba Cloud Dypns (号码认证) SMS verify-code client."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("math_agent.web.dypns")


class DypnsError(RuntimeError):
    pass


@dataclass(frozen=True)
class DypnsConfig:
    access_key_id: str
    access_key_secret: str
    sign_name: str
    template_code: str
    region_id: str = "cn-hangzhou"
    scheme_name: str = ""
    country_code: str = "86"
    code_length: int = 6
    valid_time: int = 300
    interval: int = 60
    return_verify_code: bool = False

    @classmethod
    def from_env(cls) -> "DypnsConfig":
        access_key_id = os.getenv("ALIYUN_ACCESS_KEY_ID", "").strip()
        access_key_secret = os.getenv("ALIYUN_ACCESS_KEY_SECRET", "").strip()
        sign_name = os.getenv("ALIYUN_DYPNS_SIGN_NAME", "").strip()
        template_code = (
            os.getenv("ALIYUN_DYPNS_TEMPLATE_CODE", "").strip()
            or os.getenv("ALIYUN_SMS_TEMPLATE_CODE", "").strip()
            or "100001"
        )
        if not access_key_id or not access_key_secret:
            raise DypnsError("Set ALIYUN_ACCESS_KEY_ID and ALIYUN_ACCESS_KEY_SECRET.")
        if not sign_name:
            raise DypnsError("Set ALIYUN_DYPNS_SIGN_NAME (号码认证控制台赠送签名).")
        if not template_code:
            raise DypnsError("Set ALIYUN_DYPNS_TEMPLATE_CODE.")
        debug = os.getenv("CONJECTA_SMS_DEBUG", "").strip().lower() in {"1", "true", "yes"}
        return cls(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            sign_name=sign_name,
            template_code=template_code,
            region_id=os.getenv("ALIYUN_DYPNS_REGION", "cn-hangzhou").strip() or "cn-hangzhou",
            scheme_name=os.getenv("ALIYUN_DYPNS_SCHEME_NAME", "").strip(),
            country_code=os.getenv("ALIYUN_DYPNS_COUNTRY_CODE", "86").strip() or "86",
            code_length=int(os.getenv("ALIYUN_DYPNS_CODE_LENGTH", "6") or "6"),
            valid_time=int(os.getenv("ALIYUN_DYPNS_VALID_TIME", "300") or "300"),
            interval=int(os.getenv("ALIYUN_DYPNS_INTERVAL", "60") or "60"),
            return_verify_code=debug,
        )


def dypns_configured() -> bool:
    try:
        DypnsConfig.from_env()
        return True
    except DypnsError:
        return False


def _build_client(config: DypnsConfig):
    from alibabacloud_dypnsapi20170525.client import Client as DypnsClient
    from alibabacloud_tea_openapi import models as open_api_models

    openapi_config = open_api_models.Config(
        access_key_id=config.access_key_id,
        access_key_secret=config.access_key_secret,
        region_id=config.region_id,
        endpoint="dypnsapi.aliyuncs.com",
    )
    return DypnsClient(openapi_config)


def _template_param_for(config: DypnsConfig) -> str:
    """Build TemplateParam JSON for SendSmsVerifyCode."""
    raw = os.getenv("ALIYUN_DYPNS_TEMPLATE_PARAM", "").strip()
    if raw:
        return raw
    # Gift templates (100001–100005) include ${min}; custom SMS_* templates usually only ${code}.
    if config.template_code.isdigit():
        minutes = max(1, config.valid_time // 60)
        return json.dumps({"code": "##code##", "min": str(minutes)}, ensure_ascii=False)
    return json.dumps({"code": "##code##"}, ensure_ascii=False)


def _send_sync(config: DypnsConfig, phone_number: str, out_id: str) -> dict[str, Any]:
    from alibabacloud_dypnsapi20170525 import models as dypns_models

    client = _build_client(config)
    request = dypns_models.SendSmsVerifyCodeRequest(
        phone_number=phone_number,
        sign_name=config.sign_name,
        template_code=config.template_code,
        template_param=_template_param_for(config),
        country_code=config.country_code,
        code_type=1,
        code_length=config.code_length,
        valid_time=config.valid_time,
        interval=config.interval,
        duplicate_policy=1,
        out_id=out_id,
        return_verify_code=config.return_verify_code,
    )
    if config.scheme_name:
        request.scheme_name = config.scheme_name
    response = client.send_sms_verify_code(request)
    body = response.body.to_map() if response.body else {}
    if not body.get("Success"):
        code = body.get("Code")
        message = body.get("Message")
        raise DypnsError(
            str(message or code or "SendSmsVerifyCode failed")
            + (f" (code={code})" if code and message and code != message else "")
        )
    model = body.get("Model") or {}
    return {
        "biz_id": model.get("BizId"),
        "out_id": model.get("OutId") or out_id,
        "verify_code": model.get("VerifyCode") if config.return_verify_code else None,
    }


def _check_sync(
    config: DypnsConfig,
    phone_number: str,
    verify_code: str,
    out_id: str | None,
) -> bool:
    from alibabacloud_dypnsapi20170525 import models as dypns_models

    client = _build_client(config)
    request = dypns_models.CheckSmsVerifyCodeRequest(
        phone_number=phone_number,
        verify_code=verify_code.strip(),
    )
    if config.scheme_name:
        request.scheme_name = config.scheme_name
    if out_id:
        request.out_id = out_id
    response = client.check_sms_verify_code(request)
    body = response.body.to_map() if response.body else {}
    if not body.get("Success"):
        raise DypnsError(str(body.get("Message") or body.get("Code") or "CheckSmsVerifyCode failed"))
    model = body.get("Model") or {}
    return str(model.get("VerifyResult") or "").upper() == "PASS"


async def send_sms_verify_code(phone_number: str) -> dict[str, Any]:
    config = DypnsConfig.from_env()
    out_id = uuid.uuid4().hex
    result = await asyncio.to_thread(_send_sync, config, phone_number, out_id)
    log.info("Dypns send ok phone=%s out_id=%s", phone_number[:3] + "****", out_id[:8])
    return result


async def check_sms_verify_code(
    phone_number: str,
    verify_code: str,
    *,
    out_id: str | None = None,
) -> bool:
    config = DypnsConfig.from_env()
    passed = await asyncio.to_thread(_check_sync, config, phone_number, verify_code, out_id)
    log.info("Dypns check phone=%s passed=%s", phone_number[:3] + "****", passed)
    return passed
