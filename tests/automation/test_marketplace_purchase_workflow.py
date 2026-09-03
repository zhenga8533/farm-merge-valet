from dataclasses import replace
from types import SimpleNamespace

from farm_merge_valet.automation.runtime import ActionResult, ActionStatus, RuntimeHealth
from farm_merge_valet.automation.workflows.marketplace import MarketplaceWorkflow
from farm_merge_valet.catalog.marketplace import marketplace_catalog
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.marketplace import MarketplaceLiveOffer


def _live() -> MarketplaceLiveOffer:
    offer = marketplace_catalog()[0]
    return MarketplaceLiveOffer(
        offer.policy_key,
        offer.offer_id,
        offer.reward_key,
        offer.reward_amount,
        offer.payment_type,
        offer.payment_key,
        offer.payment_amount,
        offer.stock,
        True,
        offer.slot_id,
        offer.candidate_key,
        1_000,
    )


def _health() -> RuntimeHealth:
    return RuntimeHealth(True, 1, True, True, True, True, 1, 0.0, True)


def _bot(now: list[float], live: MarketplaceLiveOffer):
    class Runtime:
        submissions = 0

        def submit_marketplace_purchase(self, action):
            self.submissions += 1
            assert action.policy_key == live.policy_key
            return ActionResult(ActionStatus.SUBMITTED)

    return SimpleNamespace(
        config=AppConfig(marketplace_policy_overrides={live.policy_key: True}),
        runtime=Runtime(),
        _now=lambda: now[0],
        _report_wait=lambda *_args, **_kwargs: None,
        _merge_workflow=SimpleNamespace(pending=None),
        _interaction_workflow=SimpleNamespace(pending=None),
        _shop_workflow=SimpleNamespace(pending=None),
        _last_wait_reason=None,
    )


def test_workflow_submits_one_unit_then_requires_stock_and_balance_verification() -> None:
    now = [10.0]
    live = _live()
    bot = _bot(now, live)
    workflow = MarketplaceWorkflow()

    assert workflow.step(bot, _health(), (live,))
    assert bot.runtime.submissions == 1
    assert workflow.pending is not None

    now[0] = 14.0
    assert not workflow.verify_pending(bot, _health(), (live,))
    assert bot.runtime.submissions == 1

    confirmed = replace(
        live,
        remaining_stock=live.remaining_stock - 1,
        balance=live.balance - live.payment_amount,
    )
    assert workflow.verify_pending(bot, _health(), (confirmed,))
    assert workflow.pending is None


def test_ambiguous_timeout_blocks_all_resubmission() -> None:
    now = [10.0]
    live = _live()
    bot = _bot(now, live)
    workflow = MarketplaceWorkflow()
    workflow.step(bot, _health(), (live,))

    now[0] = 21.0
    assert not workflow.verify_pending(bot, _health(), (live,))
    assert workflow.pending is None
    assert not workflow.step(bot, _health(), (live,))
    assert bot.runtime.submissions == 1
