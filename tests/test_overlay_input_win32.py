from forza_tracker.overlay.controller import OverlayAction
from forza_tracker.overlay.input_win32 import (
    VK_ESCAPE,
    VK_RETURN,
    VK_UP,
    WindowsInputPoller,
    XINPUT_GAMEPAD_A,
    XINPUT_GAMEPAD_B,
    XINPUT_GAMEPAD_DPAD_UP,
)


def test_keyboard_actions_emit_only_on_rising_edge() -> None:
    keys = {VK_ESCAPE: False, VK_UP: False, VK_RETURN: False}
    poller = WindowsInputPoller(key_reader=lambda key: keys.get(key, False), gamepad_reader=lambda: None)

    keys[VK_RETURN] = True
    assert poller.poll() == (OverlayAction.CONFIRM,)
    assert poller.poll() == ()
    keys[VK_RETURN] = False
    assert poller.poll() == ()
    keys[VK_RETURN] = True
    assert poller.poll() == (OverlayAction.CONFIRM,)


def test_keyboard_and_gamepad_are_normalized_in_sequence_order() -> None:
    keys = {VK_ESCAPE: True, VK_UP: False, VK_RETURN: True}
    gamepad = [(XINPUT_GAMEPAD_DPAD_UP | XINPUT_GAMEPAD_A | XINPUT_GAMEPAD_B, 0)]
    poller = WindowsInputPoller(
        key_reader=lambda key: keys.get(key, False), gamepad_reader=lambda: gamepad[0]
    )

    assert poller.poll() == (
        OverlayAction.BACK,
        OverlayAction.UP,
        OverlayAction.CONFIRM,
    )


def test_thumb_up_and_reset_do_not_create_false_edges() -> None:
    gamepad = [(0, 20000)]
    poller = WindowsInputPoller(key_reader=lambda _key: False, gamepad_reader=lambda: gamepad[0])

    poller.reset()
    assert poller.poll() == ()
    gamepad[0] = (0, 0)
    poller.poll()
    gamepad[0] = (0, 20000)
    assert poller.poll() == (OverlayAction.UP,)
