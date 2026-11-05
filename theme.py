"""
Visual theme for Secure Message.

macOS-inspired look built entirely on GTK4 + libadwaita:
  * one palette per colour scheme, injected through libadwaita's named colours
    so every stock widget follows it automatically;
  * all custom styling is written against those names, so light and dark
    can never drift apart;
  * icons are *symbolic* SVGs (see icons/) and are recoloured by GTK from the
    CSS `color` property, which is what makes them work on both themes.
"""

from __future__ import annotations

LIGHT = {
    "window_bg": "#f5f5f7",
    "window_fg": "#1d1d1f",
    "card_bg": "#ffffff",
    "field_bg": "#ffffff",
    "header_bg": "#ececee",
    "popover_bg": "#ffffff",
    "dim": "#6e6e73",
    "border": "rgba(0,0,0,0.10)",
    "border_strong": "rgba(0,0,0,0.16)",
    "track": "rgba(0,0,0,0.065)",
    "seg_active": "#ffffff",
    "btn_bg": "#ffffff",
    "btn_hover": "#f2f2f4",
    "toast_bg": "#fbfbfd",
    "accent": "#007aff",
    "accent_text": "#0062cc",
    "success": "#1f8a3b",
    "on_success": "#ffffff",
    "warning": "#c76a00",
    "error": "#d70015",
    "info": "#0071e3",
    "shadow": "0,0,0",
    "shadow_a": "0.10",
}

DARK = {
    "window_bg": "#1c1c1e",
    "window_fg": "#f5f5f7",
    "card_bg": "#2a2a2d",
    "field_bg": "#1f1f21",
    "header_bg": "#2b2b2e",
    "popover_bg": "#323235",
    "dim": "#a1a1a6",
    "border": "rgba(255,255,255,0.10)",
    "border_strong": "rgba(255,255,255,0.18)",
    "track": "rgba(255,255,255,0.09)",
    "seg_active": "#636366",
    "btn_bg": "#3a3a3d",
    "btn_hover": "#454548",
    "toast_bg": "#323235",
    "accent": "#0a84ff",
    "accent_text": "#64a8ff",
    "success": "#32d74b",
    "on_success": "#052e10",
    "warning": "#ff9f0a",
    "error": "#ff6961",
    "info": "#64b5ff",
    "shadow": "0,0,0",
    "shadow_a": "0.40",
}

_TEMPLATE = """
/* ---- libadwaita named colours -> macOS palette ---- */
@define-color window_bg_color      {window_bg};
@define-color window_fg_color      {window_fg};
@define-color view_bg_color        {field_bg};
@define-color view_fg_color        {window_fg};
@define-color headerbar_bg_color   {header_bg};
@define-color headerbar_fg_color   {window_fg};
@define-color headerbar_backdrop_color {window_bg};
@define-color headerbar_border_color {border};
@define-color card_bg_color        {card_bg};
@define-color card_fg_color        {window_fg};
@define-color popover_bg_color     {popover_bg};
@define-color popover_fg_color     {window_fg};
@define-color accent_bg_color      {accent};
@define-color accent_color         {accent_text};
@define-color accent_fg_color      #ffffff;
@define-color success_color        {success};
@define-color warning_color        {warning};
@define-color error_color          {error};
@define-color sm_info_color        {info};
@define-color sm_on_success        {on_success};
@define-color sm_dim               {dim};
@define-color sm_border            {border};
@define-color sm_border_strong     {border_strong};
@define-color sm_track             {track};
@define-color sm_seg_active        {seg_active};
@define-color sm_btn_bg            {btn_bg};
@define-color sm_btn_hover         {btn_hover};
@define-color sm_toast_bg          {toast_bg};

window {{
    font-family: "SF Pro Text", "Inter", "Cantarell", "Vazirmatn", system-ui, sans-serif;
}}

/* ---- Title bar: unified toolbar + traffic lights ---- */
headerbar {{
    min-height: 46px;
    box-shadow: none;
    border-bottom: 1px solid @sm_border;
}}
headerbar windowtitle .title {{ font-weight: 600; font-size: 13px; }}
headerbar windowtitle .subtitle {{ font-size: 11px; color: @sm_dim; }}

/* Own traffic lights: fixed 14px circles, independent of the libadwaita version */
.traffic {{ margin: 0 6px 0 4px; }}
.traffic button.tl {{
    min-width: 14px; min-height: 14px; padding: 0; margin: 0;
    border-radius: 50%;
    border: 1px solid alpha(black, 0.18);
    box-shadow: none; background-image: none;
    color: alpha(black, 0.62);
    transition: background-color 150ms ease, opacity 120ms ease;
}}
.traffic button.tl image {{ opacity: 0; transition: opacity 120ms ease; }}
.traffic:hover button.tl image {{ opacity: 1; }}
.traffic button.tl.close    {{ background-color: #ff5f57; }}
.traffic button.tl.minimize {{ background-color: #febc2e; }}
.traffic button.tl.maximize {{ background-color: #28c840; }}
.traffic button.tl:active   {{ opacity: 0.75; }}
window:backdrop .traffic button.tl {{ background-color: alpha(@sm_dim, 0.35); border-color: alpha(black, 0.08); }}

headerbar button.sm-header-btn,
headerbar menubutton.sm-header-btn > button {{
    min-width: 30px; min-height: 30px; padding: 0;
    border-radius: 8px; background: transparent; background-image: none; box-shadow: none; border: none;
    color: @sm_dim;
    transition: background-color 150ms ease, color 150ms ease, transform 100ms ease;
}}
headerbar button.sm-header-btn:hover,
headerbar menubutton.sm-header-btn > button:hover {{ background-color: alpha(currentColor, 0.10); color: @window_fg_color; }}
headerbar button.sm-header-btn:active,
headerbar menubutton.sm-header-btn > button:active {{ transform: scale(0.92); }}
headerbar menubutton.sm-header-btn > button:checked {{ background-color: alpha(currentColor, 0.14); color: @window_fg_color; }}

/* ---- Panes (macOS "inset group" cards) ---- */
.pane {{
    background-color: @card_bg_color;
    border: 1px solid @sm_border;
    border-radius: 12px;
    box-shadow: 0 1px 2px rgba({shadow}, 0.05), 0 6px 18px rgba({shadow}, 0.05);
    transition: border-color 180ms ease, box-shadow 180ms ease;
}}
.pane:focus-within {{
    border-color: alpha(@accent_bg_color, 0.85);
    box-shadow: 0 0 0 3px alpha(@accent_bg_color, 0.28);
}}
.pane.invalid {{
    border-color: @error_color;
    box-shadow: 0 0 0 3px alpha(@error_color, 0.28);
}}
.pane.flash {{
    border-color: @success_color;
    box-shadow: 0 0 0 3px alpha(@success_color, 0.30);
}}
.pane-header {{ padding: 8px 8px 8px 14px; border-bottom: 1px solid @sm_border; min-height: 30px; }}
.pane-footer {{ padding: 6px 14px; border-top: 1px solid @sm_border; }}
.pane-title  {{ font-weight: 600; font-size: 13px; }}
.sm-dim      {{ color: @sm_dim; }}
.sm-small    {{ font-size: 12px; }}
.sm-caption  {{ font-size: 12px; font-weight: 600; color: @sm_dim; }}

textview, textview > text {{ background: transparent; color: @window_fg_color; }}
textview {{ font-size: 14px; line-height: 1.5; }}
textview.mono {{ font-family: "JetBrains Mono", "Fira Code", "SF Mono", "Source Code Pro", monospace; font-size: 12.5px; line-height: 1.55; }}
textview > text selection {{ background-color: alpha(@accent_bg_color, 0.35); }}

.empty-title {{ font-weight: 600; font-size: 14px; }}
.empty-icon  {{ color: @sm_dim; opacity: 0.55; }}

/* ---- Text fields ---- */
.sm-entry {{
    min-height: 34px;
    border-radius: 8px;
    background-color: @view_bg_color;
    border: 1px solid @sm_border_strong;
    box-shadow: none;
    outline: none;
    transition: border-color 160ms ease, box-shadow 160ms ease;
}}
.sm-entry:focus-within {{
    border-color: @accent_bg_color;
    box-shadow: 0 0 0 3px alpha(@accent_bg_color, 0.28);
}}
.sm-entry.invalid {{
    border-color: @error_color;
    box-shadow: 0 0 0 3px alpha(@error_color, 0.28);
}}

/* ---- Segmented control ---- */
.segmented {{ background-color: @sm_track; border-radius: 9px; padding: 2px; }}
.segmented button {{
    background: transparent; background-image: none; border: none; box-shadow: none;
    border-radius: 7px; min-height: 28px; padding: 0 10px;
    font-weight: 500; font-size: 13px; color: @sm_dim;
    transition: background-color 200ms ease, color 200ms ease, box-shadow 200ms ease;
}}
.segmented button:hover:not(:checked) {{ color: @window_fg_color; }}
.segmented button:checked {{
    background-color: @sm_seg_active; color: @window_fg_color;
    box-shadow: 0 1px 3px rgba(0,0,0,0.20), 0 0 0 0.5px rgba(0,0,0,0.08);
}}

/* ---- Buttons ---- */
button.sm-btn {{
    border-radius: 8px; min-height: 34px; padding: 0 14px;
    font-weight: 500;
    background-color: @sm_btn_bg; background-image: none;
    color: @window_fg_color;
    border: 1px solid @sm_border_strong;
    box-shadow: 0 1px 1px rgba({shadow}, 0.06);
    transition: background-color 160ms ease, color 160ms ease, border-color 160ms ease,
                box-shadow 160ms ease, transform 100ms ease;
}}
button.sm-btn:hover  {{ background-color: @sm_btn_hover; }}
button.sm-btn:active {{ transform: scale(0.97); }}
button.sm-btn:disabled {{ opacity: 0.45; }}
button.sm-btn.primary {{
    background-color: @accent_bg_color; color: #ffffff; border-color: transparent;
}}
button.sm-btn.primary:hover {{ background-color: shade(@accent_bg_color, 1.10); }}
button.sm-btn.done {{
    background-color: alpha(@success_color, 0.16);
    border-color: alpha(@success_color, 0.50);
    color: @success_color;
}}
button.sm-btn.primary.done {{ background-color: @success_color; color: @sm_on_success; border-color: transparent; }}

button.sm-tool {{
    min-height: 26px; padding: 0 9px; border-radius: 7px;
    background: transparent; background-image: none; border: none; box-shadow: none;
    font-size: 12px; font-weight: 500; color: @sm_dim;
    transition: background-color 160ms ease, color 160ms ease, transform 100ms ease;
}}
button.sm-tool:hover  {{ background-color: alpha(currentColor, 0.10); color: @window_fg_color; }}
button.sm-tool:active {{ transform: scale(0.96); }}
button.sm-tool:disabled {{ opacity: 0.35; }}
button.sm-tool.done {{ color: @success_color; background-color: alpha(@success_color, 0.14); }}

/* ---- Chips ---- */
.chip {{
    border-radius: 999px; padding: 1px 9px;
    font-size: 11px; font-weight: 600;
    background-color: alpha(@accent_bg_color, 0.16); color: @accent_color;
}}
.chip.ok {{ background-color: alpha(@success_color, 0.16); color: @success_color; }}
.chip.info    {{ background-color: alpha(@sm_info_color, 0.16); color: @sm_info_color; }}

/* ---- Toasts: four kinds, told apart by colour AND icon shape ---- */
.toast {{
    background-color: @sm_toast_bg;
    border: 1px solid @sm_border_strong;
    border-radius: 14px;
    padding: 9px 6px 9px 12px;
    min-width: 200px;
    box-shadow: 0 8px 28px rgba(0,0,0,0.22), 0 1px 3px rgba(0,0,0,0.16);
    transition: transform 160ms ease;
}}
.toast.bump {{ transform: scale(1.035); }}
.toast .toast-text {{ font-size: 13px; font-weight: 500; }}
.toast .toast-close {{
    min-width: 24px; min-height: 24px; padding: 0; border-radius: 50%;
    background: transparent; background-image: none; border: none; box-shadow: none;
    color: @sm_dim; transition: background-color 150ms ease, color 150ms ease;
}}
.toast .toast-close:hover {{ background-color: alpha(currentColor, 0.12); color: @window_fg_color; }}
.toast.kind-success {{ background-image: linear-gradient(alpha(@success_color, 0.13), alpha(@success_color, 0.13)); border-color: alpha(@success_color, 0.50); }}
.toast.kind-info    {{ background-image: linear-gradient(alpha(@sm_info_color, 0.13), alpha(@sm_info_color, 0.13)); border-color: alpha(@sm_info_color, 0.50); }}
.toast.kind-warning {{ background-image: linear-gradient(alpha(@warning_color, 0.15), alpha(@warning_color, 0.15)); border-color: alpha(@warning_color, 0.55); }}
.toast.kind-error   {{ background-image: linear-gradient(alpha(@error_color, 0.14), alpha(@error_color, 0.14)); border-color: alpha(@error_color, 0.55); }}
.toast.kind-success .toast-icon {{ color: @success_color; }}
.toast.kind-info    .toast-icon {{ color: @sm_info_color; }}
.toast.kind-warning .toast-icon {{ color: @warning_color; }}
.toast.kind-error   .toast-icon {{ color: @error_color; }}


.action-bar {{ background-color: @headerbar_bg_color; border-top: 1px solid @sm_border; padding: 10px 0; }}

/* ---- Voice studio ---- */
.wave {{ color: @accent_bg_color; transition: color 250ms ease; }}
.wave.live {{ color: @error_color; }}
.timer-label {{ font-size: 30px; font-weight: 300; font-feature-settings: "tnum"; }}
.tnum {{ font-feature-settings: "tnum"; }}
button.rec-btn {{
    min-width: 64px; min-height: 64px; padding: 0; border-radius: 50%;
    background-color: @sm_btn_bg; background-image: none; color: @error_color;
    border: 1px solid @sm_border_strong;
    box-shadow: 0 1px 3px rgba({shadow}, 0.14), 0 0 0 0 alpha(@error_color, 0);
    transition: background-color 220ms ease, color 220ms ease, box-shadow 700ms ease, transform 100ms ease;
}}
button.rec-btn:hover  {{ background-color: @sm_btn_hover; }}
button.rec-btn:active {{ transform: scale(0.93); }}
button.rec-btn.on {{
    background-color: @error_color; color: #ffffff; border-color: transparent;
    box-shadow: 0 0 0 6px alpha(@error_color, 0.20);
}}
button.rec-btn.on.pulse {{ box-shadow: 0 0 0 14px alpha(@error_color, 0.05); }}
button.play-btn {{
    min-width: 34px; min-height: 34px; padding: 0; border-radius: 50%;
    background-color: @accent_bg_color; background-image: none; color: #ffffff;
    border: none; box-shadow: 0 1px 2px rgba({shadow}, 0.2);
    transition: background-color 160ms ease, transform 100ms ease;
}}
button.play-btn:hover  {{ background-color: shade(@accent_bg_color, 1.1); }}
button.play-btn:active {{ transform: scale(0.92); }}
.notice {{
    background-color: alpha(@warning_color, 0.12); border: 1px solid alpha(@warning_color, 0.5);
    border-radius: 10px; padding: 10px;
}}
.notice-icon {{ color: @warning_color; }}

/* ---- Keys ---- */
.key-list, .key-list row {{ background: transparent; padding: 0; }}
.key-row {{ padding: 10px 12px; border-bottom: 1px solid @sm_border; }}
textview.keybox {{ font-family: monospace; font-size: 11.5px; background-color: @view_bg_color; }}
.keybox-frame {{ border: 1px solid @sm_border_strong; border-radius: 8px; }}
dropdown.sm-dropdown > button {{
    min-height: 34px; border-radius: 8px; background-color: @view_bg_color; background-image: none;
    border: 1px solid @sm_border_strong; box-shadow: none;
}}
dropdown.invalid > button {{ border-color: @error_color; box-shadow: 0 0 0 3px alpha(@error_color, 0.28); }}

/* ---- Popover content ---- */
button.dev-card {{ padding: 8px 10px; border-radius: 10px; }}
button.dev-card:hover {{ background-color: alpha(currentColor, 0.08); }}
.help-step-num {{
    min-width: 20px; min-height: 20px; border-radius: 50%;
    background-color: alpha(@accent_bg_color, 0.16); color: @accent_color;
    font-size: 11px; font-weight: 700;
}}
.kbd {{
    font-family: monospace; font-size: 11px; padding: 1px 6px;
    border-radius: 5px; background-color: @sm_track; border: 1px solid @sm_border;
}}
"""


def build_css(dark: bool) -> str:
    return _TEMPLATE.format(**(DARK if dark else LIGHT))
