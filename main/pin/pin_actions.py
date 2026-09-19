# -*- coding: utf-8 -*-
"""贴图窗口的动作表：贴图上能做哪些事、哪个鼠标手势做哪件事。

结构跟「全局鼠标动作」（``core/actions.py`` + ``app/mouse_gestures``）一致：一张
注册表说明**能做哪些事**，一张配置表（``app/pin_mouse_actions``）说明**哪个手势做哪件事**。
默认表复刻改造前写死在 ``pin/pin_window.py`` 里的行为——滚轮缩放、Ctrl+滚轮调透明度、
右键出菜单，中键与双击不做事。

与全局鼠标手势的区别只有一点：这里的「手势」是贴图窗口自己收到的事件，不需要全局钩子，
也就不需要任何系统权限。
"""

from dataclasses import dataclass

from core.logger import T, log_debug, log_warning

#: 贴图窗口能识别的手势。滚轮分「裸滚」与「Ctrl+滚」，两者可以绑不同动作。
GESTURE_WHEEL_UP = "wheel_up"
GESTURE_WHEEL_DOWN = "wheel_down"
GESTURE_CTRL_WHEEL_UP = "ctrl_wheel_up"
GESTURE_CTRL_WHEEL_DOWN = "ctrl_wheel_down"
GESTURE_MIDDLE_CLICK = "middle_click"
GESTURE_DOUBLE_CLICK = "double_click"
GESTURE_RIGHT_CLICK = "right_click"

GESTURES = (
    GESTURE_WHEEL_UP,
    GESTURE_WHEEL_DOWN,
    GESTURE_CTRL_WHEEL_UP,
    GESTURE_CTRL_WHEEL_DOWN,
    GESTURE_MIDDLE_CLICK,
    GESTURE_DOUBLE_CLICK,
    GESTURE_RIGHT_CLICK,
)

#: 「这个手势不做任何事」——默认表里用得上（中键、双击）
ACTION_NONE = "none"


@dataclass(frozen=True)
class PinAction:
    """一个可以绑到贴图手势上的动作。

    ``label`` 是界面文案（英文源，界面用 tr() 翻译）；动作真正干什么由
    ``run_pin_action`` 决定——贴图窗口才是这些操作的所有者。
    """

    id: str
    label: str


PIN_ACTIONS = (
    PinAction("zoom_in", "Zoom In"),
    PinAction("zoom_out", "Zoom Out"),
    PinAction("fine_zoom_in", "Fine Zoom In"),
    PinAction("fine_zoom_out", "Fine Zoom Out"),
    PinAction("opacity_up", "Increase Opacity"),
    PinAction("opacity_down", "Decrease Opacity"),
    PinAction("reset_size", "Reset Size"),
    PinAction("save", "Save Image"),
    PinAction("rotate_cw", "Rotate Right"),
    PinAction("toggle_stay_on_top", "Toggle Always on Top"),
    PinAction("toggle_border", "Toggle Shadow"),
    PinAction("copy_content", "Copy Content"),
    PinAction("copy_text", "Copy Recognized Text"),
    PinAction("translate", "Translate"),
    PinAction("toggle_thumbnail", "Thumbnail Mode"),
    PinAction("toggle_toolbar", "Toggle Toolbar"),
    PinAction("toggle_lock", "Lock Position and Size"),
    PinAction("toggle_click_through", "Toggle Click-through"),
    PinAction("toggle_focus_mode", "Focus Mode"),
    PinAction("close_others", "Close Other Pins"),
    PinAction("load_content", "Load New Content"),
    PinAction("recognize_text", "Recognize Text Again"),
    PinAction("crop", "Crop"),
    PinAction("filter_grayscale", "Grayscale"),
    PinAction("filter_invert", "Invert Colours"),
    PinAction("filter_blur", "Blur"),
    PinAction("filter_emboss", "Emboss"),
    PinAction("context_menu", "Show Context Menu"),
    PinAction("close_selected", "Close Selected Pins"),
    PinAction("copy_and_close", "Copy and Close"),
    PinAction("close", "Close"),
)

PIN_ACTIONS_BY_ID = {action.id: action for action in PIN_ACTIONS}

#: 默认绑定：与改造前的写死行为一致
DEFAULT_PIN_MOUSE_ACTIONS = {
    GESTURE_WHEEL_UP: "zoom_in",
    GESTURE_WHEEL_DOWN: "zoom_out",
    GESTURE_CTRL_WHEEL_UP: "opacity_up",
    GESTURE_CTRL_WHEEL_DOWN: "opacity_down",
    GESTURE_MIDDLE_CLICK: ACTION_NONE,
    GESTURE_DOUBLE_CLICK: ACTION_NONE,
    GESTURE_RIGHT_CLICK: "context_menu",
}

#: 「精细缩放」用的步长（与滚轮常态步长共用上下限，见 PIN_ZOOM_STEP_RANGE）
FINE_ZOOM_STEP = 1.01


def is_known_action(action_id: str) -> bool:
    return action_id == ACTION_NONE or action_id in PIN_ACTIONS_BY_ID


def normalize(bindings) -> dict:
    """收敛绑定表：丢掉不认识的手势与动作，缺失的手势补默认值。

    配置可能来自旧版本或被手工改坏。丢掉而不是报错：贴图交互坏掉的表现是「点了没反应」，
    比在界面上明确看到「未绑定」难查得多。
    """
    table = dict(DEFAULT_PIN_MOUSE_ACTIONS)
    for gesture, action_id in (bindings or {}).items():
        gesture = str(gesture)
        action_id = str(action_id)
        if gesture not in GESTURES:
            log_warning(T("忽略不认识的贴图手势: {gesture}", gesture=gesture), "PinAction")
            continue
        if not is_known_action(action_id):
            log_warning(T("忽略不认识的贴图动作: {action_id}", action_id=action_id), "PinAction")
            continue
        table[gesture] = action_id
    return table


def resolve(config_manager) -> dict:
    """读出当前生效的手势表（配置缺失或读失败都用默认表）。"""
    getter = getattr(config_manager, "get_pin_mouse_actions", None) if config_manager else None
    if getter is None:
        return dict(DEFAULT_PIN_MOUSE_ACTIONS)
    try:
        return normalize(getter())
    except Exception as e:
        from core.logger import log_exception

        log_exception(e, T("读取贴图手势配置"))
        return dict(DEFAULT_PIN_MOUSE_ACTIONS)


def run_pin_action(action_id: str, window) -> bool:
    """在贴图窗口上执行一个动作；不认识的 id 记日志并返回 False。"""
    if action_id == ACTION_NONE:
        return False

    action = PIN_ACTIONS_BY_ID.get(action_id)
    if action is None:
        log_warning(T("不认识的贴图动作: {action_id}", action_id=action_id), "PinAction")
        return False

    log_debug(T("贴图动作: {action_id}", action_id=action_id), "PinAction")

    if action_id == "zoom_in":
        return bool(window.apply_zoom(1))
    if action_id == "zoom_out":
        return bool(window.apply_zoom(-1))
    if action_id == "fine_zoom_in":
        return bool(window.apply_zoom(1, fine=True))
    if action_id == "fine_zoom_out":
        return bool(window.apply_zoom(-1, fine=True))
    if action_id == "opacity_up":
        return bool(window.adjust_opacity(1))
    if action_id == "opacity_down":
        return bool(window.adjust_opacity(-1))
    if action_id == "reset_size":
        window.reset_to_original_size()
        return True
    if action_id == "save":
        window.save_image()
        return True
    if action_id == "rotate_cw":
        window.rotate_image_cw()
        return True
    if action_id == "toggle_stay_on_top":
        window.toggle_stay_on_top()
        return True
    if action_id == "toggle_border":
        window.toggle_border_effect()
        return True
    if action_id == "copy_content":
        window.copy_to_clipboard()
        return True
    if action_id == "copy_text":
        return bool(window.copy_recognized_text())
    if action_id == "translate":
        window.request_translation()
        return True
    if action_id == "toggle_thumbnail":
        window.toggle_thumbnail_mode()
        return True
    if action_id == "toggle_toolbar":
        window.toggle_toolbar()
        return True
    if action_id == "toggle_lock":
        window.toggle_lock()
        return True
    if action_id == "toggle_click_through":
        return bool(window.toggle_click_through())
    if action_id == "toggle_focus_mode":
        return bool(window.toggle_focus_mode())
    if action_id == "close_others":
        return window.close_other_pins() >= 0
    if action_id == "load_content":
        return bool(window.load_image_from_file())
    if action_id == "crop":
        return bool(window.start_crop_mode())
    if action_id == "filter_grayscale":
        return bool(window.apply_filter("grayscale"))
    if action_id == "filter_invert":
        return bool(window.apply_filter("invert"))
    if action_id == "filter_blur":
        return bool(window.apply_filter("blur"))
    if action_id == "filter_emboss":
        return bool(window.apply_filter("emboss"))
    if action_id == "recognize_text":
        return bool(window.recognize_text_now())
    if action_id == "context_menu":
        return bool(window.show_context_menu_at_cursor())
    if action_id == "close_selected":
        return bool(window.close_selected_pins())
    if action_id == "copy_and_close":
        window.copy_and_close()
        return True
    if action_id == "close":
        window.close_window()
        return True
    return False
