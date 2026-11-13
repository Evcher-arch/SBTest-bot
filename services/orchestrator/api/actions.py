"""Подтверждение и отмена черновика обращения.

Подтверждение — единственное место реального вызова sd_create_ticket (принцип II).
idempotency_key = action_id; повторное подтверждение возвращает тот же номер.
"""

from __future__ import annotations

from common.errors import ApiError
from common.jwt import verify_token
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from .. import drafts
from ..agent import registry
from ..clients.tokens import session_token
from ..tracing import get_tracer

router = APIRouter()


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=1)


async def _caller(session_id: str) -> str:
    token = await session_token(session_id)
    if not token:
        raise ApiError("forbidden", "Сессия истекла: выберите роль или отправьте сообщение заново.")
    return verify_token(token).sub


def _confirmed(result: dict, repeated: bool) -> dict:
    prefix = "Обращение уже создано" if repeated else "Обращение создано"
    return {
        "status": "confirmed",
        "number": result["number"],
        "url": result["url"],
        "text": f"{prefix}: {result['number']}. Ссылка: {result['url']}",
    }


@router.post("/actions/{action_id}/confirm")
async def confirm(action_id: str, body: ActionRequest) -> dict:
    employee_id = await _caller(body.session_id)
    tracer = get_tracer()
    with tracer.trace("confirm", session_id=body.session_id, user_id=employee_id,
                      input={"action_id": action_id}) as trace:
        stored = await drafts.get_result(action_id)
        if stored is not None:
            if stored["employee_id"] != employee_id:
                raise ApiError("forbidden", "Это черновик другого сотрудника.")
            trace.update(output={"status": "confirmed", "repeated": True, "number": stored["number"]})
            return _confirmed(stored, repeated=True)

        draft = await drafts.get(action_id)
        if draft is None:
            trace.update(output={"status": "expired"})
            raise ApiError("not_found", "Черновик истёк или не существует. Попросите подготовить обращение заново.")
        if draft["employee_id"] != employee_id:
            raise ApiError("forbidden", "Это черновик другого сотрудника.")
        if draft["status"] == "cancelled":
            raise ApiError("not_found", "Черновик отменён. Попросите подготовить обращение заново.")

        state = await drafts.try_begin_confirm(action_id)
        if state == "busy":
            result = await drafts.wait_result(action_id)
            if result is None:
                raise ApiError("unavailable", "Подтверждение уже выполняется, повторите через несколько секунд.")
            return _confirmed(result, repeated=True)
        if state == "confirmed":
            result = await drafts.get_result(action_id)
            if result:
                return _confirmed(result, repeated=True)
        if state != "ok":
            raise ApiError("not_found", "Черновик истёк или не существует. Попросите подготовить обращение заново.")

        args = {"type_id": draft["type_id"], "title": draft["title"],
                "description": draft["description"], "fields": draft["fields"],
                "idempotency_key": action_id}
        registry.validate("sd_create_ticket", args, from_model=False)
        ctx = registry.ToolContext(session_id=body.session_id, employee_id=employee_id)
        with tracer.step("tool:sd_create_ticket", as_type="tool",
                         input={k: v for k, v in args.items()}) as obs:
            try:
                created = await registry.execute("sd_create_ticket", ctx, args)
            except Exception:  # noqa: BLE001
                created = {"error": {"code": "unavailable", "message": "Service-Desk временно недоступен."}}
            obs.update(output=created)
        error = created.get("error") if isinstance(created, dict) else None
        if error or not created.get("number"):
            await drafts.fail_confirm(action_id)
            code = (error or {}).get("code", "unavailable")
            trace.error(f"create failed: {code}")
            raise ApiError(code, (error or {}).get("message"))
        result = {"number": created["number"], "url": created["url"], "employee_id": employee_id}
        await drafts.finish_confirm(action_id, result)
        trace.update(output={"status": "confirmed", "number": created["number"]})
        return _confirmed(result, repeated=False)


@router.post("/actions/{action_id}/cancel")
async def cancel(action_id: str, body: ActionRequest) -> dict:
    employee_id = await _caller(body.session_id)
    draft = await drafts.get(action_id)
    if draft is None:
        raise ApiError("not_found", "Черновик истёк или не существует.")
    if draft["employee_id"] != employee_id:
        raise ApiError("forbidden", "Это черновик другого сотрудника.")
    if draft["status"] == "confirmed":
        raise ApiError("invalid_input", "Обращение уже создано, отменить черновик нельзя.")
    await drafts.cancel(action_id)
    with get_tracer().trace("cancel", session_id=body.session_id, user_id=employee_id,
                            input={"action_id": action_id}) as trace:
        trace.update(output={"status": "cancelled"})
    return {"status": "cancelled"}
