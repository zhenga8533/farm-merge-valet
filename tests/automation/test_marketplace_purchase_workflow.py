from dataclasses import replace
from types import SimpleNamespace

from farm_merge_valet.automation.action_control import ActionCoordinator
from farm_merge_valet.automation.runtime import ActionResult, ActionStatus, RuntimeHealth
from farm_merge_valet.automation.workflows.marketplace import MarketplaceWorkflow
from farm_merge_valet.config import AppConfig
from farm_merge_valet.core.marketplace import MarketplaceLiveOffer
from tests.marketplace_fixtures import marketplace_catalog


def _live(index: int = 0) -> MarketplaceLiveOffer:
    offer = marketplace_catalog()[index]
    return MarketplaceLiveOffer(
        offer.policy_key,
        offer.offer_id,
        offer.reward_key,
        offer.reward_amount,
        offer.payment_type,
        offer.payment_key,
        offer.payment_amount,
        offer.stock,
        offer.slot_id,
        offer.candidate_key,
        1_000,
    )


def _health() -> RuntimeHealth:
    return RuntimeHealth(True, 1, True, True, True, True, 1, 0.0, True)


def _bot(now: list[float], live: MarketplaceLiveOffer):
    class Runtime:
        submissions = 0
        submitted_keys: list[str] = []

        def submit_marketplace_purchase(self, action):
            self.submissions += 1
            self.submitted_keys.append(action.policy_key)
            return ActionResult(ActionStatus.SUBMITTED)

    action_control = ActionCoordinator()
    return SimpleNamespace(
        config=AppConfig(marketplace_policy_overrides={live.policy_key: True}),
        runtime=Runtime(),
        _now=lambda: now[0],
        _report_wait=lambda *_args, **_kwargs: None,
        _merge_workflow=SimpleNamespace(pending=None),
        _interaction_workflow=SimpleNamespace(pending=None),
        _shop_workflow=SimpleNamespace(pending=None),
        _last_wait_reason=None,
        _actions=lambda: action_control,
        _marketplace_catalog=marketplace_catalog(),
        _record_action_no_progress=lambda *_args, **_kwargs: action_control.record_no_progress(),
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


def test_ambiguous_timeout_blocks_only_the_affected_offer() -> None:
    now = [10.0]
    first = _live(1)
    second = replace(
        first,
        policy_key="free:coins_25_no_ads",
        offer_id="coins_25_no_ads",
        reward_key="coins",
        reward_amount=25,
    )
    bot = _bot(now, first)
    bot.config = AppConfig()
    workflow = MarketplaceWorkflow()
    workflow.step(bot, _health(), (first, second))

    now[0] = 21.0
    assert not workflow.verify_pending(bot, _health(), (first, second))
    assert workflow.pending is None
    assert workflow.step(bot, _health(), (first, second))
    assert workflow.pending is not None
    assert workflow.pending.action.policy_key == second.policy_key
    assert bot.runtime.submitted_keys == [first.policy_key, second.policy_key]


def test_free_claims_are_enabled_by_default_and_can_be_disabled() -> None:
    now = [10.0]
    live = _live(1)
    bot = _bot(now, live)
    workflow = MarketplaceWorkflow()
    bot.config = AppConfig()

    assert workflow.step(bot, _health(), (live,))

    workflow.pending = None
    bot.config = AppConfig(marketplace_policy_overrides={live.policy_key: False})
    assert not workflow.step(bot, _health(), (live,))


def test_marketplace_master_switch_preserves_offer_policy_without_purchasing() -> None:
    now = [10.0]
    live = _live(1)
    bot = _bot(now, live)
    bot.config = AppConfig(marketplace_automation_enabled=False)

    assert not MarketplaceWorkflow().step(bot, _health(), (live,))
    assert bot.runtime.submissions == 0
