from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app_paths import APP_NAME, APP_VERSION, ensure_runtime_layout, reports_root, resource_root, user_root

RESOURCE_ROOT = resource_root()
if str(RESOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(RESOURCE_ROOT))

from core.data import merge_history, write_draws
from core.engine import LotteryEngine
from core.history_expansion import load_flexible_csv
from core.online_updates import (
    OnlineUpdateError,
    fetch_official_results,
    merge_official_update,
    preview_official_update,
)
from games.registry import ALL_GAMES, BY_KEY, BY_NAME


PALETTE = {
    "bg": "#F4F7FB",
    "surface": "#FFFFFF",
    "sidebar": "#0B1B2F",
    "sidebar_card": "#102844",
    "sidebar_border": "#29496A",
    "sidebar_text": "#F8FAFC",
    "sidebar_muted": "#B8C7DA",
    "blue": "#2563EB",
    "blue_bright": "#58A6FF",
    "ink": "#0F172A",
    "muted": "#526174",
    "line": "#D7E0EB",
    "soft_blue": "#EAF3FF",
    "success": "#15803D",
    "success_bg": "#EAF8EE",
    "warning": "#A16207",
    "warning_bg": "#FFF8E6",
}

GAME_ACCENTS = {
    "lotto": "#2563EB",
    "lotto_hotpicks": "#0E7490",
    "euromillions": "#D97706",
    "euromillions_hotpicks": "#7C3AED",
    "set_for_life": "#15803D",
    "thunderball": "#EA580C",
    "powerball": "#DC2626",
}

SPECIAL_STYLES = {
    "Powerball": ("#DC2626", "#FFFFFF"),
    "Lucky Stars": ("#FACC15", "#111827"),
    "Lucky Star": ("#FACC15", "#111827"),
    "Life Ball": ("#7C3AED", "#FFFFFF"),
    "Thunderball": ("#EA580C", "#FFFFFF"),
}

LINE_OPTIONS = (1, 3, 5, 10, 20)


class SmartPickApp(tk.Tk):
    """Simple personal front end for DrawWise.

    The historical analysis, updater, wheeling and backtest machinery remains available
    under Advanced Tools, but the normal workflow is deliberately only:
    choose game -> choose number of lines -> generate.
    """

    def __init__(self, start_game: str | None = None):
        super().__init__()
        self.runtime_root = ensure_runtime_layout()
        self.engine = LotteryEngine(self.runtime_root)
        self.last_result = None
        self._refresh_thread: threading.Thread | None = None

        self.title(f"{APP_NAME} {APP_VERSION} — Smart Pick")
        self.geometry("1280x820")
        self.minsize(1040, 680)
        self.configure(bg=PALETTE["bg"])

        icon = RESOURCE_ROOT / "assets" / "drawwise.ico"
        if icon.exists() and sys.platform.startswith("win"):
            try:
                self.iconbitmap(str(icon))
            except tk.TclError:
                pass

        self._build_style()
        self._build_ui()
        self._bind_shortcuts()

        if start_game and start_game in BY_KEY:
            self.game_var.set(BY_KEY[start_game].name)
        else:
            # A neutral default for UK use.
            self.game_var.set(BY_KEY["lotto"].name)
        self.on_game_changed()

    # ---------- styling ----------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Smart.TCombobox",
            fieldbackground="#FFFFFF",
            background="#FFFFFF",
            foreground=PALETTE["ink"],
            bordercolor=PALETTE["line"],
            arrowcolor=PALETTE["ink"],
            padding=10,
        )
        style.map(
            "Smart.TCombobox",
            fieldbackground=[("readonly", "#FFFFFF")],
            foreground=[("readonly", PALETTE["ink"])],
            bordercolor=[("focus", PALETTE["blue_bright"])],
            lightcolor=[("focus", PALETTE["blue_bright"])],
            darkcolor=[("focus", PALETTE["blue_bright"])],
        )

    def _button(self, parent, text, command, *, bg, fg="white", small=False, subtle=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=PALETTE["blue_bright"] if not subtle else "#183858",
            activeforeground="white" if fg == "white" else fg,
            relief="flat",
            bd=0,
            cursor="hand2",
            takefocus=True,
            highlightthickness=2,
            highlightbackground=bg,
            highlightcolor=PALETTE["blue_bright"],
            font=("Segoe UI", 9 if small else 11, "bold"),
            padx=12 if small else 16,
            pady=7 if small else 11,
        )

    # ---------- layout ----------
    def _build_ui(self):
        self.game_var = tk.StringVar()
        self.lines_var = tk.StringVar(value="5")
        self.status_var = tk.StringVar(value="Ready")
        self.history_var = tk.StringVar(value="Loading history…")
        self.mode_var = tk.StringVar(value="Smart portfolio")
        self.view_var = tk.StringVar(value="pick")

        shell = tk.Frame(self, bg=PALETTE["bg"])
        shell.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(shell, bg=PALETTE["sidebar"], width=325)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.main = tk.Frame(shell, bg=PALETTE["bg"])
        self.main.pack(side="left", fill="both", expand=True, padx=24, pady=20)

        self._build_sidebar()
        self._build_main()

    def _build_sidebar(self):
        s = self.sidebar
        padx = 20

        brand = tk.Frame(s, bg=PALETTE["sidebar"])
        brand.pack(fill="x", padx=padx, pady=(22, 18))
        tk.Label(
            brand, text="DW", width=3, bg="#174EA6", fg="white",
            font=("Segoe UI", 12, "bold"), padx=5, pady=10,
        ).pack(side="left")
        bt = tk.Frame(brand, bg=PALETTE["sidebar"])
        bt.pack(side="left", padx=(12, 0))
        tk.Label(bt, text="DrawWise", bg=PALETTE["sidebar"], fg="white",
                 font=("Segoe UI", 19, "bold")).pack(anchor="w")
        tk.Label(bt, text=f"SMART LOTTERY PICKS  •  v{APP_VERSION}", bg=PALETTE["sidebar"], fg=PALETTE["sidebar_muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")

        # Simple navigation only.
        nav = tk.Frame(s, bg=PALETTE["sidebar"])
        nav.pack(fill="x", padx=padx, pady=(0, 18))
        self.pick_nav = self._sidebar_nav(nav, "◎  Smart Pick", lambda: self._show_view("pick"), active=True)
        self.pick_nav.pack(fill="x", pady=(0, 6))
        self.saved_nav = self._sidebar_nav(nav, "▣  Saved Picks", lambda: self._show_view("saved"))
        self.saved_nav.pack(fill="x")

        tk.Label(s, text="CHOOSE GAME", bg=PALETTE["sidebar"], fg=PALETTE["sidebar_muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=padx)
        self.game_combo = ttk.Combobox(
            s, textvariable=self.game_var, values=[g.name for g in ALL_GAMES], state="readonly",
            style="Smart.TCombobox", font=("Segoe UI", 11), takefocus=True,
        )
        self.game_combo.pack(fill="x", padx=padx, pady=(7, 15), ipady=2)
        self.game_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_game_changed())

        tk.Label(s, text="HOW MANY LINES?", bg=PALETTE["sidebar"], fg=PALETTE["sidebar_muted"],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=padx)
        self.lines_combo = ttk.Combobox(
            s, textvariable=self.lines_var, values=[str(n) for n in LINE_OPTIONS], state="readonly",
            style="Smart.TCombobox", font=("Segoe UI", 11), takefocus=True,
        )
        self.lines_combo.pack(fill="x", padx=padx, pady=(7, 15), ipady=2)
        self.lines_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_lines_changed())

        mode = tk.Frame(s, bg=PALETTE["sidebar_card"], highlightthickness=1,
                        highlightbackground=PALETTE["sidebar_border"])
        mode.pack(fill="x", padx=padx, pady=(0, 14))
        tk.Label(mode, text="SELECTION MODE", bg=PALETTE["sidebar_card"], fg="#8FC4FF",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=13, pady=(11, 3))
        tk.Label(mode, textvariable=self.mode_var, bg=PALETTE["sidebar_card"], fg="#FFFFFF",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=13)
        tk.Label(
            mode,
            text="One line uses the top-ranked candidate. Multiple lines keep that top pick and spread the alternatives for broader coverage.",
            bg=PALETTE["sidebar_card"], fg="#E7EFF8", justify="left", wraplength=260,
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=13, pady=(4, 12))

        history = tk.Frame(s, bg="#0F2D42", highlightthickness=1, highlightbackground="#23577C")
        history.pack(fill="x", padx=padx, pady=(0, 14))
        tk.Label(history, text="RESULT HISTORY", bg="#0F2D42", fg="#67E8F9",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=13, pady=(10, 3))
        tk.Label(history, textvariable=self.history_var, bg="#0F2D42", fg="#F1F7FC",
                 justify="left", wraplength=260, font=("Segoe UI", 9)).pack(anchor="w", padx=13)
        self.refresh_button = self._button(history, "Update online results", self.refresh_results,
                                           bg="#087EA4", small=True)
        self.refresh_button.pack(fill="x", padx=13, pady=(9, 6))
        self.import_button = self._button(history, "Import history file", self.import_history_file,
                                          bg="#1D4E73", small=True)
        self.import_button.pack(fill="x", padx=13, pady=(0, 10))

        self.generate_button = self._button(s, "🎯  GENERATE MY NUMBERS", self.generate, bg="#2563EB")
        self.generate_button.pack(fill="x", padx=padx, pady=(0, 8))

        advanced = self._button(s, "⚙  ADVANCED TOOLS", self.open_advanced, bg="#173B60", small=True)
        advanced.pack(fill="x", padx=padx, pady=(0, 8))

    def _sidebar_nav(self, parent, text, command, active=False):
        return tk.Button(
            parent, text=text, command=command, anchor="w",
            bg="#12304F" if active else PALETTE["sidebar"],
            fg="white", activebackground="#173B60", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", takefocus=True,
            highlightthickness=1, highlightbackground="#4EA1FF" if active else PALETTE["sidebar"],
            highlightcolor=PALETTE["blue_bright"], font=("Segoe UI", 10, "bold"),
            padx=14, pady=11,
        )

    def _build_main(self):
        head = tk.Frame(self.main, bg=PALETTE["bg"])
        head.pack(fill="x", pady=(0, 14))
        left = tk.Frame(head, bg=PALETTE["bg"])
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text="Smart Pick", bg=PALETTE["bg"], fg=PALETTE["ink"],
                 font=("Segoe UI", 23, "bold")).pack(anchor="w")
        tk.Label(left, text="Choose a game and how many lines. DrawWise ranks the candidates for you.",
                 bg=PALETTE["bg"], fg=PALETTE["muted"], font=("Segoe UI", 11)).pack(anchor="w", pady=(3, 0))
        self.game_chip = tk.Label(head, text="", bg=PALETTE["soft_blue"], fg="#174EA6",
                                  font=("Segoe UI", 9, "bold"), padx=12, pady=8)
        self.game_chip.pack(side="right", anchor="n")

        intro = tk.Frame(self.main, bg="#FFFFFF", highlightthickness=1, highlightbackground=PALETTE["line"])
        intro.pack(fill="x", pady=(0, 14))
        self.intro_accent = tk.Frame(intro, width=5, bg=PALETTE["blue"])
        self.intro_accent.pack(side="left", fill="y")
        ib = tk.Frame(intro, bg="#FFFFFF")
        ib.pack(side="left", fill="x", expand=True, padx=16, pady=12)
        tk.Label(ib, text="Your candidate selections", bg="#FFFFFF", fg=PALETTE["ink"],
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(
            ib,
            text="DrawWise scores many valid combinations, returns its top-ranked candidate, and diversifies extra lines when you ask for more than one.",
            bg="#FFFFFF", fg=PALETTE["muted"], font=("Segoe UI", 9), wraplength=900, justify="left",
        ).pack(anchor="w", pady=(3, 0))

        self.view_stack = tk.Frame(self.main, bg=PALETTE["bg"])
        self.view_stack.pack(fill="both", expand=True)
        self.view_stack.grid_rowconfigure(0, weight=1)
        self.view_stack.grid_columnconfigure(0, weight=1)

        self.pick_view = tk.Frame(self.view_stack, bg="#FFFFFF", highlightthickness=1,
                                  highlightbackground=PALETTE["line"])
        self.pick_view.grid(row=0, column=0, sticky="nsew")
        self.saved_view = tk.Frame(self.view_stack, bg="#FFFFFF", highlightthickness=1,
                                   highlightbackground=PALETTE["line"])
        self.saved_view.grid(row=0, column=0, sticky="nsew")

        self._build_pick_view()
        self._build_saved_view()
        self._show_view("pick")

        footer = tk.Frame(self.main, bg=PALETTE["bg"])
        footer.pack(fill="x", pady=(9, 0))
        tk.Label(footer, text="Smart Ensemble ranks structured candidates; it cannot guarantee a future random draw.",
                 bg=PALETTE["bg"], fg=PALETTE["muted"], font=("Segoe UI", 8)).pack(side="left")
        tk.Label(footer, textvariable=self.status_var, bg=PALETTE["bg"], fg=PALETTE["muted"],
                 font=("Segoe UI", 9, "bold")).pack(side="right")

    def _build_pick_view(self):
        top = tk.Frame(self.pick_view, bg="#FFFFFF")
        top.pack(fill="x", padx=20, pady=(17, 10))
        tleft = tk.Frame(top, bg="#FFFFFF")
        tleft.pack(side="left", fill="x", expand=True)
        tk.Label(tleft, text="My numbers", bg="#FFFFFF", fg=PALETTE["ink"],
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")
        self.pick_subtitle = tk.Label(tleft, text="Generate a Smart Pick to begin.", bg="#FFFFFF", fg=PALETTE["muted"],
                                      font=("Segoe UI", 9))
        self.pick_subtitle.pack(anchor="w", pady=(2, 0))

        actions = tk.Frame(top, bg="#FFFFFF")
        actions.pack(side="right")
        self.copy_all_button = self._workspace_button(actions, "Copy all", self.copy_all, bg="#EAF0F7", fg=PALETTE["ink"])
        self.copy_all_button.pack(side="left")
        self.regenerate_button = self._workspace_button(actions, "Regenerate", self.generate, bg="#EAF0F7", fg=PALETTE["ink"])
        self.regenerate_button.pack(side="left", padx=(7, 0))
        self.save_button = self._workspace_button(actions, "Save picks", self.save_picks, bg="#2563EB", fg="white")
        self.save_button.pack(side="left", padx=(7, 0))

        summary = tk.Frame(self.pick_view, bg="#FFFFFF")
        summary.pack(fill="x", padx=20, pady=(0, 10))
        self.summary_vars = [tk.StringVar(value="—") for _ in range(4)]
        labels = ("Lines", "History", "Different numbers", "Selection mode")
        for i, label in enumerate(labels):
            card = tk.Frame(summary, bg="#F8FAFC", highlightthickness=1, highlightbackground=PALETTE["line"])
            card.pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 6, 0))
            tk.Label(card, textvariable=self.summary_vars[i], bg="#F8FAFC", fg=PALETTE["ink"],
                     font=("Segoe UI", 14, "bold")).pack(pady=(10, 1))
            tk.Label(card, text=label, bg="#F8FAFC", fg=PALETTE["muted"],
                     font=("Segoe UI", 8, "bold")).pack(pady=(0, 9))

        # Ticket area.  The Recommended card deliberately lives OUTSIDE the
        # scrollable alternatives canvas.  This makes the top pick sticky and
        # removes the V5.1/V5.2 regression where LINE 02 could become the first
        # visible card after changing line count or regenerating.
        container = tk.Frame(self.pick_view, bg="#FFFFFF")
        container.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        self.ticket_container = container

        self.recommended_host = tk.Frame(container, bg="#FFFFFF")
        # Do not pack it until a result exists.

        self.alternatives_container = tk.Frame(container, bg="#FFFFFF")
        self.alternatives_container.pack(fill="both", expand=True)

        # Short portfolios (3 or 5 lines) are rendered in a normal frame, not a
        # Canvas.  This is deliberately boring: it removes the platform-specific
        # canvas y-offset that repeatedly clipped LINE 02 on Windows.  Only long
        # portfolios (10/20 lines) need a scrollbar.
        self.static_alternatives_body = tk.Frame(self.alternatives_container, bg="#FFFFFF")

        self.ticket_canvas = tk.Canvas(self.alternatives_container, bg="#FFFFFF", highlightthickness=0)
        self.ticket_scrollbar = ttk.Scrollbar(self.alternatives_container, orient="vertical", command=self.ticket_canvas.yview)
        self.ticket_body = tk.Frame(self.ticket_canvas, bg="#FFFFFF")
        self.ticket_body.bind("<Configure>", self._on_ticket_body_configure)
        self.ticket_window = self.ticket_canvas.create_window((0, 0), window=self.ticket_body, anchor="nw")
        self.ticket_canvas.bind("<Configure>", self._on_ticket_canvas_configure)
        self.ticket_canvas.configure(yscrollcommand=self.ticket_scrollbar.set)
        self.ticket_canvas.bind("<Enter>", lambda _e: self._bind_ticket_wheel())
        self.ticket_canvas.bind("<Leave>", lambda _e: self._unbind_ticket_wheel())
        self._use_scrolling_alternatives()
        self._render_empty()

    def _workspace_button(self, parent, text, command, *, bg, fg):
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground="#DCEBFF" if fg != "white" else PALETTE["blue_bright"],
            activeforeground=PALETTE["ink"] if fg != "white" else "white",
            relief="flat", bd=0, cursor="hand2", takefocus=True,
            highlightthickness=2, highlightbackground=bg, highlightcolor=PALETTE["blue_bright"],
            font=("Segoe UI", 9, "bold"), padx=13, pady=8,
        )

    def _build_saved_view(self):
        header = tk.Frame(self.saved_view, bg="#FFFFFF")
        header.pack(fill="x", padx=20, pady=(17, 10))
        tk.Label(header, text="Saved Picks", bg="#FFFFFF", fg=PALETTE["ink"],
                 font=("Segoe UI", 15, "bold")).pack(side="left")
        self._workspace_button(header, "Open saved file", self.open_saved_file,
                               bg="#EAF0F7", fg=PALETTE["ink"]).pack(side="right")

        self.saved_canvas = tk.Canvas(self.saved_view, bg="#FFFFFF", highlightthickness=0)
        saved_scroll = ttk.Scrollbar(self.saved_view, orient="vertical", command=self.saved_canvas.yview)
        self.saved_body = tk.Frame(self.saved_canvas, bg="#FFFFFF")
        self.saved_body.bind("<Configure>", lambda _e: self.saved_canvas.configure(scrollregion=self.saved_canvas.bbox("all")))
        sw = self.saved_canvas.create_window((0, 0), window=self.saved_body, anchor="nw")
        self.saved_canvas.bind("<Configure>", lambda e: self.saved_canvas.itemconfigure(sw, width=e.width))
        self.saved_canvas.configure(yscrollcommand=saved_scroll.set)
        self.saved_canvas.pack(side="left", fill="both", expand=True, padx=(20, 0), pady=(0, 16))
        saved_scroll.pack(side="right", fill="y", padx=(0, 20), pady=(0, 16))

    # ---------- state helpers ----------
    @property
    def config(self):
        return BY_NAME[self.game_var.get()]

    def _show_view(self, key: str):
        self.view_var.set(key)
        if key == "saved":
            self._render_saved()
            self.saved_view.tkraise()
        else:
            self.pick_view.tkraise()
        active_pick = key == "pick"
        self.pick_nav.configure(bg="#12304F" if active_pick else PALETTE["sidebar"],
                                highlightbackground="#4EA1FF" if active_pick else PALETTE["sidebar"])
        self.saved_nav.configure(bg="#12304F" if not active_pick else PALETTE["sidebar"],
                                 highlightbackground="#4EA1FF" if not active_pick else PALETTE["sidebar"])

    def on_lines_changed(self):
        try:
            lines = int(self.lines_var.get())
        except ValueError:
            lines = 5
        self.mode_var.set("Best single pick" if lines == 1 else "Smart portfolio")
        self._reset_ticket_scroll()
        # Do not leave an old portfolio visible under a newly selected line count.
        if self.last_result is not None and len(self.last_result.tickets) != lines:
            self.last_result = None
            self._render_empty()
            self.status_var.set(f"Ready to generate {lines} line{'s' if lines != 1 else ''}")

    def on_game_changed(self):
        cfg = self.config
        self.on_lines_changed()
        accent = GAME_ACCENTS.get(cfg.key, PALETTE["blue"])
        self.game_chip.configure(text=cfg.name, fg=accent)
        self.intro_accent.configure(bg=accent)
        self.last_result = None
        self._render_empty()
        self._refresh_history_status()
        self.status_var.set(f"Selected {cfg.name}")
        self._show_view("pick")

    def _refresh_history_status(self):
        cfg = self.config
        try:
            draws = self.engine.analysis_draws(cfg)
        except Exception as exc:
            self.history_var.set(f"History unavailable\n{exc}")
            return
        if not draws:
            self.history_var.set("No compatible history loaded")
            return
        latest = draws[-1].draw_date.strftime("%d %b %Y")
        self.history_var.set(f"{len(draws)} compatible draws\nLatest: {latest}")

    # ---------- generation ----------
    def generate(self):
        cfg = self.config
        try:
            lines = int(self.lines_var.get())
        except ValueError:
            lines = 5
        self.generate_button.configure(state="disabled", text="Generating…")
        self.status_var.set("Analysing history and building candidate lines…")
        self.update_idletasks()
        try:
            result = self.engine.generate(
                config=cfg,
                strategy="Smart Ensemble",
                lines=lines,
                pool_size=max(cfg.default_pool_size, cfg.main_pick + 5),
                special_pool_size=(cfg.special_range_size if cfg.special_pick else 0),
                recent_window=min(20, max(5, len(self.engine.analysis_draws(cfg)))),
            )
            self.last_result = result
            self._render_result(result)
            self.status_var.set(f"Generated {len(result.tickets)} {cfg.name} Smart Ensemble line(s)")
        except Exception as exc:
            messagebox.showerror("Could not generate picks", str(exc))
            self.status_var.set("Generation failed")
        finally:
            self.generate_button.configure(state="normal", text="🎯  GENERATE MY NUMBERS")

    def _clear_ticket_body(self):
        for child in self.ticket_body.winfo_children():
            child.destroy()
        if hasattr(self, "static_alternatives_body"):
            for child in self.static_alternatives_body.winfo_children():
                child.destroy()
        if hasattr(self, "recommended_host"):
            for child in self.recommended_host.winfo_children():
                child.destroy()

    def _show_recommended_host(self, show: bool):
        if not hasattr(self, "recommended_host"):
            return
        if show:
            if not self.recommended_host.winfo_manager():
                self.recommended_host.pack(fill="x", before=self.alternatives_container, pady=(0, 4))
        else:
            if self.recommended_host.winfo_manager():
                self.recommended_host.pack_forget()

    def _show_alternatives(self, show: bool):
        if not hasattr(self, "alternatives_container"):
            return
        if show:
            if not self.alternatives_container.winfo_manager():
                self.alternatives_container.pack(fill="both", expand=True)
        else:
            if self.alternatives_container.winfo_manager():
                self.alternatives_container.pack_forget()

    def _use_static_alternatives(self):
        """Use a non-scrolling frame for short portfolios.

        The previous canvas implementation could start a few pixels below y=0 on
        Windows after geometry changes, which visually cropped LINE 02.  A normal
        frame has no viewport and therefore no scroll offset to inherit.
        """
        if not hasattr(self, "static_alternatives_body"):
            return
        self._unbind_ticket_wheel()
        if self.ticket_canvas.winfo_manager():
            self.ticket_canvas.pack_forget()
        if self.ticket_scrollbar.winfo_manager():
            self.ticket_scrollbar.pack_forget()
        if not self.static_alternatives_body.winfo_manager():
            self.static_alternatives_body.pack(fill="both", expand=True)

    def _use_scrolling_alternatives(self):
        """Use the canvas only when the portfolio is too long to fit normally."""
        if not hasattr(self, "ticket_canvas"):
            return
        if hasattr(self, "static_alternatives_body") and self.static_alternatives_body.winfo_manager():
            self.static_alternatives_body.pack_forget()
        if not self.ticket_canvas.winfo_manager():
            self.ticket_canvas.pack(side="left", fill="both", expand=True)
        if not self.ticket_scrollbar.winfo_manager():
            self.ticket_scrollbar.pack(side="right", fill="y")

    def _on_ticket_body_configure(self, _event=None):
        if not hasattr(self, "ticket_canvas"):
            return
        try:
            bbox = self.ticket_canvas.bbox("all")
            self.ticket_canvas.configure(scrollregion=bbox if bbox else (0, 0, 0, 0))
        except tk.TclError:
            pass

    def _on_ticket_canvas_configure(self, event):
        try:
            self.ticket_canvas.itemconfigure(self.ticket_window, width=event.width)
            self._on_ticket_body_configure()
        except tk.TclError:
            pass

    def _render_empty(self):
        if not hasattr(self, "ticket_body"):
            return
        self._clear_ticket_body()
        self._show_recommended_host(False)
        self._show_alternatives(True)
        self._use_scrolling_alternatives()
        self.pick_subtitle.configure(text="Generate your next Smart Ensemble pick.")
        for v in self.summary_vars:
            v.set("—")
        empty = tk.Frame(self.ticket_body, bg="#F8FAFC", highlightthickness=1, highlightbackground=PALETTE["line"])
        empty.pack(fill="x", padx=4, pady=4)
        tk.Label(empty, text="🎯", bg="#F8FAFC", fg=PALETTE["blue"], font=("Segoe UI Emoji", 28)).pack(pady=(34, 7))
        tk.Label(empty, text="Ready for your next pick", bg="#F8FAFC", fg=PALETTE["ink"],
                 font=("Segoe UI", 16, "bold")).pack()
        tk.Label(empty, text="Choose a game and number of lines, then click Generate My Numbers.",
                 bg="#F8FAFC", fg=PALETTE["muted"], font=("Segoe UI", 10)).pack(pady=(5, 12))
        self._workspace_button(empty, "Generate my numbers", self.generate, bg="#2563EB", fg="white").pack(pady=(0, 24))
        self._reset_ticket_scroll()

    def _render_result(self, result):
        self._clear_ticket_body()
        cfg = result.config
        accent = GAME_ACCENTS.get(cfg.key, PALETTE["blue"])
        covered = len({n for t in result.tickets for n in t.main})
        mode = "Best single pick" if len(result.tickets) == 1 else "Smart portfolio"
        self.pick_subtitle.configure(
            text=f"Smart Ensemble • History: {len(result.draws)} draws • Top-ranked line shown first"
        )
        self.summary_vars[0].set(str(len(result.tickets)))
        self.summary_vars[1].set(f"{len(result.draws)} draws")
        self.summary_vars[2].set(f"{covered} / {cfg.main_range_size}")
        self.summary_vars[3].set(mode)

        if not result.tickets:
            self._show_recommended_host(False)
            self._show_alternatives(True)
            self._render_empty()
            return

        # Sticky top pick: never part of the scrolling canvas.
        self._show_recommended_host(True)
        self._render_ticket_card(
            self.recommended_host, result.tickets[0], 0, True, accent, cfg
        )

        # One-line mode has no alternatives.  Short portfolios are intentionally
        # non-scrolling so LINE 02 cannot be clipped by a canvas viewport.  Only
        # 10/20-line portfolios use the scrolling canvas.
        has_alternatives = len(result.tickets) > 1
        self._show_alternatives(has_alternatives)
        if has_alternatives:
            if len(result.tickets) <= 5:
                self._use_static_alternatives()
                parent = self.static_alternatives_body
            else:
                self._use_scrolling_alternatives()
                parent = self.ticket_body
            for idx, ticket in enumerate(result.tickets[1:], start=1):
                self._render_ticket_card(
                    parent, ticket, idx, False, accent, cfg, compact=True
                )

        self._reset_ticket_scroll()

    def _render_ticket_card(self, parent, ticket, idx: int, recommended: bool, accent: str, cfg, compact: bool = False):
        border = accent if recommended else PALETTE["line"]
        card = tk.Frame(
            parent, bg="#FFFFFF",
            highlightthickness=2 if recommended else 1,
            highlightbackground=border,
        )
        card.pack(fill="x", padx=4, pady=(4, 7 if recommended else (3 if compact else 8)))
        tk.Frame(card, width=5, bg=accent).pack(side="left", fill="y")

        label_box = tk.Frame(card, bg="#F8FAFC", width=150 if recommended else (118 if compact else 132))
        label_box.pack(side="left", fill="y")
        label_box.pack_propagate(False)
        tk.Label(
            label_box,
            text="RECOMMENDED" if recommended else f"LINE {idx + 1:02d}",
            bg="#F8FAFC",
            fg=accent if recommended else PALETTE["muted"],
            font=("Segoe UI", 10 if recommended else (8 if compact else 9), "bold"),
        ).pack(expand=True)

        middle = tk.Frame(card, bg="#FFFFFF")
        middle.pack(
            side="left", fill="x", expand=True,
            padx=16 if recommended else (11 if compact else 14),
            pady=12 if recommended else (5 if compact else 9),
        )
        balls = tk.Frame(middle, bg="#FFFFFF")
        balls.pack(anchor="w")
        for n in ticket.main:
            tk.Label(
                balls, text=f"{n:02d}", bg=accent, fg="white", width=3,
                font=("Segoe UI", 13 if recommended else (10 if compact else 11), "bold"),
                padx=5 if recommended else (3 if compact else 4),
                pady=7 if recommended else (3 if compact else 5),
            ).pack(side="left", padx=(0, 8 if recommended else 7))

        if ticket.special:
            tk.Frame(balls, width=1, bg=PALETTE["line"], height=38).pack(
                side="left", fill="y", padx=(5, 10)
            )
            special_label = cfg.special_name or "Special"
            tk.Label(
                balls, text=special_label, bg="#FFFFFF", fg=PALETTE["muted"],
                font=("Segoe UI", 8, "bold"),
            ).pack(side="left", padx=(0, 8))
            fill, fg = SPECIAL_STYLES.get(special_label, (accent, "white"))
            for n in ticket.special:
                tk.Label(
                    balls, text=f"{n:02d}", bg=fill, fg=fg, width=3,
                    font=("Segoe UI", 13 if recommended else 11, "bold"),
                    padx=5 if recommended else 4,
                    pady=7 if recommended else 5,
                ).pack(side="left", padx=(0, 8 if recommended else 7))

        sub = "Top-ranked candidate in this run" if recommended else "Diversified Smart Ensemble alternative"
        tk.Label(
            middle, text=sub, bg="#FFFFFF", fg=PALETTE["muted"],
            font=("Segoe UI", 8 if recommended or not compact else 7),
        ).pack(anchor="w", pady=(4 if recommended or not compact else 1, 0))

        card_actions = tk.Frame(card, bg="#FFFFFF")
        card_actions.pack(side="right", padx=15 if recommended else (10 if compact else 15))
        if recommended:
            self._workspace_button(
                card_actions, "Pick again", self.pick_again,
                bg="#EAF3FF", fg=accent,
            ).pack(pady=(0, 7))
        self._workspace_button(
            card_actions, "Copy", lambda t=ticket: self.copy_ticket(t),
            bg="#EAF0F7", fg=PALETTE["ink"],
        ).pack()

    def _reset_ticket_scroll(self):
        """Reset the alternatives viewport after state/render changes.

        The Recommended card is sticky outside this canvas in V5.3.3.  We still force
        alternatives to absolute zero in multiple event-loop phases so LINE 02 is
        always the first alternative after Generate/Regenerate/game/line changes.
        """
        if not hasattr(self, "ticket_canvas"):
            return

        def reset():
            try:
                self.ticket_canvas.update_idletasks()
                self._on_ticket_body_configure()
                self.ticket_canvas.yview_moveto(0.0)
            except tk.TclError:
                pass

        reset()
        self.after_idle(reset)
        self.after(25, reset)
        self.after(100, reset)

    def _bind_ticket_wheel(self):
        self.bind_all("<MouseWheel>", self._on_ticket_wheel)

    def _unbind_ticket_wheel(self):
        self.unbind_all("<MouseWheel>")

    def _on_ticket_wheel(self, event):
        if (
            hasattr(self, "ticket_canvas")
            and hasattr(self, "alternatives_container")
            and self.alternatives_container.winfo_ismapped()
        ):
            self.ticket_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def pick_again(self):
        """Replace only the recommended line with the next strongest unseen candidate."""
        if not self.last_result:
            self.generate()
            return
        cfg = self.last_result.config
        excluded = {tuple(t.main) for t in self.last_result.tickets}
        self.status_var.set("Finding another strong candidate…")
        self.update_idletasks()
        try:
            replacement = self.engine.generate(
                config=cfg,
                strategy="Smart Ensemble",
                lines=1,
                pool_size=max(cfg.default_pool_size, cfg.main_pick + 5),
                special_pool_size=(cfg.special_range_size if cfg.special_pick else 0),
                recent_window=min(20, max(5, len(self.engine.analysis_draws(cfg)))),
                excluded_main_lines=excluded,
            )
            if not replacement.tickets:
                raise ValueError("No alternative candidate was generated")
            self.last_result.tickets[0] = replacement.tickets[0]
            self.last_result.base_pool = sorted({n for t in self.last_result.tickets for n in t.main})
            self._render_result(self.last_result)
            self.status_var.set("Recommended line replaced with the next ranked alternative")
        except Exception as exc:
            messagebox.showinfo("No new pick", f"DrawWise could not produce another distinct candidate right now.\n\n{exc}")
            self.status_var.set("Recommended line unchanged")

    def ticket_text(self, ticket) -> str:
        return ticket.display(self.config.special_name)

    def copy_ticket(self, ticket):
        self.clipboard_clear()
        self.clipboard_append(self.ticket_text(ticket))
        self.status_var.set("Copied line")

    def copy_all(self):
        if not self.last_result:
            messagebox.showinfo("Nothing to copy", "Generate your numbers first.")
            return
        text = "\n".join(f"{i + 1:02d}. {self.ticket_text(t)}" for i, t in enumerate(self.last_result.tickets))
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_var.set("Copied all generated lines")

    # ---------- saved picks ----------
    def _saved_path(self) -> Path:
        p = user_root() / "saved_picks.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _load_saved(self) -> list[dict]:
        p = self._saved_path()
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def save_picks(self):
        if not self.last_result:
            messagebox.showinfo("Nothing to save", "Generate your numbers first.")
            return
        saved = self._load_saved()
        cfg = self.last_result.config
        entry = {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "game": cfg.name,
            "game_key": cfg.key,
            "method": "Smart Ensemble",
            "tickets": [
                {"main": list(t.main), "special": list(t.special)} for t in self.last_result.tickets
            ],
        }
        saved.insert(0, entry)
        self._saved_path().write_text(json.dumps(saved[:100], indent=2), encoding="utf-8")
        self.status_var.set("Saved current picks")
        messagebox.showinfo("Saved", "Your current picks have been saved locally.")

    def _render_saved(self):
        for child in self.saved_body.winfo_children():
            child.destroy()
        saved = self._load_saved()
        if not saved:
            tk.Label(self.saved_body, text="No saved picks yet.", bg="#FFFFFF", fg=PALETTE["muted"],
                     font=("Segoe UI", 11)).pack(anchor="w", padx=6, pady=20)
            return
        for entry in saved[:30]:
            card = tk.Frame(self.saved_body, bg="#F8FAFC", highlightthickness=1, highlightbackground=PALETTE["line"])
            card.pack(fill="x", padx=5, pady=(4, 8))
            head = tk.Frame(card, bg="#F8FAFC")
            head.pack(fill="x", padx=14, pady=(10, 5))
            tk.Label(head, text=entry.get("game", "Lottery"), bg="#F8FAFC", fg=PALETTE["ink"],
                     font=("Segoe UI", 10, "bold")).pack(side="left")
            stamp = entry.get("saved_at", "").replace("T", " ")
            tk.Label(head, text=stamp, bg="#F8FAFC", fg=PALETTE["muted"],
                     font=("Segoe UI", 8)).pack(side="right")
            lines = []
            cfg = BY_KEY.get(entry.get("game_key", ""))
            special_name = cfg.special_name if cfg else "Special"
            for i, raw in enumerate(entry.get("tickets", [])):
                main = " ".join(f"{int(n):02d}" for n in raw.get("main", []))
                special = raw.get("special", [])
                if special:
                    main += f"  |  {special_name}: " + " ".join(f"{int(n):02d}" for n in special)
                lines.append(f"{i + 1:02d}. {main}")
            tk.Label(card, text="\n".join(lines), bg="#F8FAFC", fg=PALETTE["ink"],
                     justify="left", font=("Consolas", 9)).pack(anchor="w", padx=14, pady=(0, 12))
        self.saved_canvas.yview_moveto(0)

    def open_saved_file(self):
        p = self._saved_path()
        if not p.exists():
            messagebox.showinfo("No saved picks", "No picks have been saved yet.")
            return
        self._open_path(p)

    # ---------- simple results refresh ----------
    def refresh_results(self):
        if self._refresh_thread and self._refresh_thread.is_alive():
            return
        cfg = self.config
        self.refresh_button.configure(state="disabled", text="Checking…")
        self.status_var.set(f"Checking official {cfg.name} results…")

        def worker():
            try:
                result = fetch_official_results(cfg, registry=BY_KEY)
                existing = self.engine.load(cfg)
                preview = preview_official_update(cfg, existing, result)
                self.after(0, lambda: self._finish_refresh(cfg, result, existing, preview, None))
            except Exception as exc:
                self.after(0, lambda: self._finish_refresh(cfg, None, None, None, exc))

        self._refresh_thread = threading.Thread(target=worker, daemon=True)
        self._refresh_thread.start()

    def _finish_refresh(self, cfg, result, existing, preview, error):
        self.refresh_button.configure(state="normal", text="Update online results")
        if error is not None:
            self.status_var.set("Could not refresh online results; using current history")
            messagebox.showinfo(
                "Using current history",
                "DrawWise could not refresh the official source right now. Your existing local history is unchanged and can still be used for Smart Ensemble.\n\nYou can still use Import history file if you downloaded the results yourself.",
            )
            return

        if preview.status != "Update available":
            self.status_var.set(f"{cfg.name} history is up to date")
            self._refresh_history_status()
            messagebox.showinfo("Up to date", f"{cfg.name} history is already up to date.")
            return

        message = (
            f"DrawWise found an official {cfg.name} history update.\n\n"
            f"New records: {preview.new_records}\n"
            f"Corrections/date normalisations: {preview.corrected_records + preview.date_normalisations}\n\n"
            "Apply it now? DrawWise will create a backup first."
        )
        if not messagebox.askyesno("Update result history", message):
            self.status_var.set("History update not applied")
            return

        path = cfg.csv_path(self.runtime_root)
        backup_dir = user_root() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"{path.stem}_smartpick_{stamp}{path.suffix}"
        if path.exists():
            shutil.copy2(path, backup)
        merged, _ = merge_official_update(cfg, existing, result)
        write_draws(path, cfg, merged)
        self._refresh_history_status()
        self.last_result = None
        self._render_empty()
        self.status_var.set(f"Updated {cfg.name} result history")
        messagebox.showinfo("Results updated", "Official result history was updated and the previous file was backed up.")

    # ---------- manual history import ----------
    def import_history_file(self):
        cfg = self.config
        source = filedialog.askopenfilename(
            title=f"Import draw history for {cfg.name}",
            filetypes=[
                ("Lottery result files", "*.csv *.xlsx *.xlsm *.txt *.tsv"),
                ("CSV files", "*.csv"),
                ("Excel workbooks", "*.xlsx *.xlsm"),
                ("All files", "*.*"),
            ],
        )
        if not source:
            return

        report = load_flexible_csv(Path(source), cfg)
        if not report.draws:
            details = "\n".join(
                f"{issue.severity}: {issue.detail}" for issue in report.issues[:8]
            ) or "No compatible draw rows were found."
            messagebox.showerror(
                "History file not imported",
                f"DrawWise could not find usable {cfg.name} draw results in this file.\n\n{details}",
            )
            return

        try:
            existing = self.engine.load(cfg)
            merged, summary = merge_history(existing, list(report.draws))
        except Exception as exc:
            messagebox.showerror("History merge failed", str(exc))
            return

        first = report.draws[0].draw_date
        last = report.draws[-1].draw_date
        rejected_note = f"\nRejected rows: {report.rejected_rows}" if report.rejected_rows else ""
        review = (
            f"File: {Path(source).name}\n"
            f"Game: {cfg.name}\n"
            f"Accepted rows: {report.accepted_rows}{rejected_note}\n"
            f"File range: {first:%d-%b-%Y} to {last:%d-%b-%Y}\n\n"
            f"New draw records: {summary.new_records}\n"
            f"Corrected/replaced records: {summary.replaced_records}\n"
            f"Already identical: {summary.unchanged_records}\n"
            f"History after import: {summary.final_rows} records\n\n"
        )

        if summary.new_records == 0 and summary.replaced_records == 0:
            self._refresh_history_status()
            messagebox.showinfo("No changes needed", review + "Your stored history already contains these results.")
            return

        if not messagebox.askyesno(
            "Import draw history",
            review + "Import these changes? DrawWise will back up your existing history first.",
        ):
            self.status_var.set("History import cancelled")
            return

        path = cfg.csv_path(self.runtime_root)
        backup_dir = user_root() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"{path.stem}_manual_{stamp}{path.suffix}"
        try:
            if path.exists():
                shutil.copy2(path, backup)
            write_draws(path, cfg, merged)
        except Exception as exc:
            messagebox.showerror("History import failed", str(exc))
            return

        self.last_result = None
        self._render_empty()
        self._refresh_history_status()
        self.status_var.set(f"Imported {cfg.name} draw history")
        messagebox.showinfo(
            "History updated",
            f"{cfg.name} history was updated successfully.\n\n"
            f"New records: {summary.new_records}\n"
            f"Corrected records: {summary.replaced_records}\n"
            f"Total stored records: {summary.final_rows}\n\n"
            "Your previous history was backed up automatically.",
        )

    # ---------- advanced ----------
    def open_advanced(self):
        """Switch to the full Advanced workspace in the current app process.

        Relaunching the frozen executable with --advanced proved unreliable on some
        Windows installations. Import the Advanced UI directly instead.
        """
        try:
            from drawwise_app import main as advanced_main
            game_key = self.config.key
            self.status_var.set("Opening Advanced tools…")
            self.update_idletasks()
            self.destroy()
            advanced_main(game_key)
        except Exception as exc:
            try:
                messagebox.showerror("Advanced tools", f"Could not open Advanced tools.\n\n{exc}")
            except Exception:
                pass

    def _open_path(self, path: Path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            messagebox.showerror("Open file", str(exc))

    def _bind_shortcuts(self):
        self.bind_all("<Alt-g>", lambda _e: self.generate())
        self.bind_all("<Control-s>", lambda _e: self.save_picks())
        self.bind_all("<Control-r>", lambda _e: self.refresh_results())


def main(start_game: str | None = None):
    app = SmartPickApp(start_game=start_game)
    app.mainloop()


if __name__ == "__main__":
    main()
