from __future__ import annotations

from typing import Any

from payg_offers_service import payg_offer_service
from payg_service import payg_service
from trust_service import trust_service


class PaygKycGate:
    """Payment-first KYC gate for initial PAYG purchases."""

    async def requires_kyc(self, user_id: int) -> bool:
        return not await trust_service.is_identity_approved(int(user_id))

    async def hold_after_payment(self, topup_id: int, actor_id: int) -> dict[str, Any]:
        return await payg_service.hold_topup_for_kyc(int(topup_id), int(actor_id))

    async def has_waiting(self, user_id: int) -> bool:
        return bool(await payg_service.list_kyc_held_topups(int(user_id)))

    async def release_after_kyc(self, user_id: int, reviewer_id: int) -> list[dict[str, Any]]:
        held = await payg_service.list_kyc_held_topups(int(user_id))
        results: list[dict[str, Any]] = []
        for topup in held:
            topup_id = int(topup["id"])
            purpose = str(topup.get("purpose") or "")
            if purpose == "initial_service":
                result = await payg_offer_service.approve_topup(topup_id, int(reviewer_id))
                results.append({"kind": "service", "topup_id": topup_id, "result": result})
            elif purpose == "initial":
                approved = await payg_service.approve_topup(topup_id, int(reviewer_id))
                continuation = await payg_service.post_topup_approval(topup_id)
                results.append({
                    "kind": "legacy",
                    "topup_id": topup_id,
                    "topup": approved,
                    "result": continuation,
                })
        return results

    async def refund_pending_after_rejection(self, user_id: int) -> list[int]:
        held = await payg_service.list_kyc_held_topups(int(user_id))
        result: list[int] = []
        for topup in held:
            topup_id = int(topup["id"])
            if await payg_service.mark_topup_refund_pending(topup_id):
                result.append(topup_id)
        return result

    async def expire_stale(self, hours: int = 48) -> list[dict[str, Any]]:
        return await payg_service.expire_kyc_held_topups(int(hours))


payg_kyc_gate = PaygKycGate()
