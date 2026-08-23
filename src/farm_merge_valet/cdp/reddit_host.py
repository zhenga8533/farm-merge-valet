"""Reddit-host fullscreen state and controls."""

from __future__ import annotations

from dataclasses import dataclass

from farm_merge_valet.cdp.client import evaluate_top_page

_FULLSCREEN_STATE_EXPRESSION = """
(() => {
  let toggle = null;
  function walk(root) {
    for (const el of root.querySelectorAll('*')) {
      if (el.matches && el.matches('button[data-test-id="expand-collapse-button"]')) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight) {
          toggle = el;
          return;
        }
      }
      if (el.shadowRoot) {
        walk(el.shadowRoot);
        if (toggle) return;
      }
    }
  }
  walk(document);
  const icon = toggle ? toggle.querySelector('svg[icon-name]') : null;
  const iconName = icon ? icon.getAttribute('icon-name') : null;
  return {
    browserFullscreen:
      Math.abs(innerWidth - screen.width) <= 2 &&
      Math.abs(innerHeight - screen.height) <= 2,
    gameExpanded: iconName ? iconName.startsWith('collapse') : null,
  };
})()
"""

_EXPAND_GAME_EXPRESSION = """
(() => {
  let toggle = null;
  function walk(root) {
    for (const el of root.querySelectorAll('*')) {
      if (el.matches && el.matches('button[data-test-id="expand-collapse-button"]')) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight) {
          toggle = el;
          return;
        }
      }
      if (el.shadowRoot) {
        walk(el.shadowRoot);
        if (toggle) return;
      }
    }
  }
  walk(document);
  if (!toggle) return 'not-found';
  const icon = toggle.querySelector('svg[icon-name]');
  const iconName = icon ? icon.getAttribute('icon-name') : '';
  if (iconName.startsWith('collapse')) return 'already-expanded';
  toggle.click();
  return 'expanded';
})()
"""


@dataclass(frozen=True)
class RedditFullscreenState:
    browser_fullscreen: bool
    game_expanded: bool | None


def read_fullscreen_state(port: int, page_title: str) -> RedditFullscreenState | None:
    raw = evaluate_top_page(port, _FULLSCREEN_STATE_EXPRESSION, page_title)
    if not isinstance(raw, dict) or not isinstance(raw.get("browserFullscreen"), bool):
        return None
    expanded = raw.get("gameExpanded")
    return RedditFullscreenState(
        browser_fullscreen=raw["browserFullscreen"],
        game_expanded=expanded if isinstance(expanded, bool) else None,
    )


def expand_game(port: int, page_title: str) -> str:
    result = evaluate_top_page(port, _EXPAND_GAME_EXPRESSION, page_title)
    return str(result) if result is not None else "not-found"
